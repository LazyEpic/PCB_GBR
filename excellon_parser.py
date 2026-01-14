# excellon_parser.py
# Complete Excellon (DRL) parser for PCB drilling + slots
# Units: millimeters internally
#
# Step-2 hardening additions:
# - Units + format parsing improvements for common Excellon headers:
#     METRIC,LZ / METRIC,TZ / INCH,LZ / INCH,TZ
#     M71/M72
#     FILE_FORMAT = i:d (in comments) (kept)
# - Zero suppression-aware coordinate parsing (when coords have no decimal point)
# - Bounding-box sanity checks (warn on “probably wrong units/format”)
#
# IMPORTANT CHANGE (from your version):
# - De-duplicate holes by XY only (within tolerance), regardless of diameter.
#   If multiple hits land on the same XY, keep ONE with the LARGEST diameter.
#
# FIX (this step):
# - When a larger hole is found within tol_xy, we now ALSO take the center (x,y)
#   from that larger hole (not the first one seen).

import os
import re
import configparser
import logging


DEFAULT_HOLE_DEDUPE_TOL = 0.10  # mm
_MAX_REASONABLE_MM = 2000.0
_MIN_REASONABLE_MM = 0.01

_LOGGER_NAME = "cnc_pcb.excellon"
_default_logger = logging.getLogger(_LOGGER_NAME)


class ExcellonParseError(RuntimeError):
    pass


def _get_logger(logger: logging.Logger | None) -> logging.Logger:
    return logger if logger is not None else _default_logger


def _warn(logger: logging.Logger, msg: str, *, strict: bool) -> None:
    logger.warning(msg)
    if strict:
        raise ExcellonParseError(msg)


def _read_job_cfg():
    cfg = configparser.ConfigParser()

    try:
        import common_gerber as cg
        root_ini = os.path.join(os.path.dirname(os.path.abspath(cg.__file__)), "job_settings.ini")
        if os.path.exists(root_ini):
            cfg.read(root_ini)
            return cfg
    except Exception:
        pass

    cfg.read("job_settings.ini")
    return cfg


def load_hole_dedupe_tol():
    cfg = _read_job_cfg()
    try:
        v = cfg.getfloat("job", "hole_dedupe_tol")
        return max(0.0, float(v))
    except Exception:
        return DEFAULT_HOLE_DEDUPE_TOL


def dedupe_holes_by_xy(holes, tol_xy: float):
    """
    holes: [(x, y, d_mm)]
    tol_xy: mm

    If two holes are within tol_xy (euclidean), they are the same location
    and we keep ONE hole there with the LARGEST diameter.

    IMPORTANT: center (x,y) is taken from the largest-diameter hole.
    """
    if not holes:
        return []

    tol_xy = float(tol_xy)
    if tol_xy <= 0:
        best = {}
        for x, y, d in holes:
            key = (round(float(x), 6), round(float(y), 6))
            d = float(d)
            if key not in best or d > best[key][2]:
                # if larger, also keep its center
                best[key] = (float(x), float(y), d)
        return list(best.values())

    inv = 1.0 / tol_xy
    r2 = tol_xy * tol_xy

    grid = {}  # (ix,iy) -> list of indices into out
    out = []

    def cell(x, y):
        # round-based binning is OK as long as we also check neighboring bins
        return int(round(x * inv)), int(round(y * inv))

    for x, y, d in holes:
        x = float(x)
        y = float(y)
        d = float(d)

        ix, iy = cell(x, y)

        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                lst = grid.get((ix + dx, iy + dy))
                if not lst:
                    continue
                for idx in lst:
                    x2, y2, d2 = out[idx]
                    ddx = x - x2
                    ddy = y - y2
                    if (ddx * ddx + ddy * ddy) <= r2:
                        found = idx
                        break
                if found is not None:
                    break
            if found is not None:
                break

        if found is None:
            out.append((x, y, d))
            idx = len(out) - 1
            grid.setdefault((ix, iy), []).append(idx)
        else:
            x2, y2, d2 = out[found]
            # If the new hit is larger, replace BOTH diameter and center with the larger hole's center.
            if d > d2:
                out[found] = (x, y, d)

    return out


class ExcellonTool:
    def __init__(self, tool_id, diameter):
        self.id = tool_id
        self.diameter = diameter
        self.holes = []  # (x, y)


class ExcellonFile:
    def __init__(self):
        self.tools = {}
        self.units = "mm"
        self.zero_suppression = "leading"  # 'leading' or 'trailing'
        self.format = (3, 3)  # (int_digits, dec_digits)
        self._scale = 0.001
        self._slots = []  # ((x1,y1),(x2,y2), slot_width)

    def set_format(self, int_digits, dec_digits):
        self.format = (int_digits, dec_digits)
        self._scale = 10 ** (-int(dec_digits))

    def set_zero_suppression(self, z: str):
        z = (z or "").lower()
        if z.startswith("t"):
            self.zero_suppression = "trailing"
        else:
            self.zero_suppression = "leading"

    def add_tool(self, tool_id, diameter):
        self.tools[tool_id] = ExcellonTool(tool_id, diameter)

    def add_hole(self, tool_id, x, y):
        if tool_id in self.tools:
            self.tools[tool_id].holes.append((x, y))

    def add_slot(self, tool_id, x1, y1, x2, y2):
        if tool_id in self.tools:
            w = self.tools[tool_id].diameter
            self._slots.append(((x1, y1), (x2, y2), w))

    def all_holes(self):
        out = []
        for t in self.tools.values():
            for x, y in t.holes:
                out.append((x, y, t.diameter))
        return out

    def all_slots(self):
        return list(self._slots)

    def _parse_fixed(self, token: str) -> float:
        """
        Excellon coordinate without decimal point, interpreted using format and zero suppression.
        """
        if token is None:
            return 0.0
        token = token.strip()
        if not token:
            return 0.0
        if "." in token:
            return float(token)

        neg = token.startswith("-")
        s = token[1:] if neg else token

        int_d, dec_d = self.format
        total = int_d + dec_d
        if total <= 0:
            total = max(1, len(s))

        if len(s) > total:
            # Wrong format is likely; keep rightmost digits defensively
            s = s[-total:]

        if len(s) < total:
            if self.zero_suppression == "leading":
                s = s.rjust(total, "0")
            else:
                s = s.ljust(total, "0")

        if dec_d <= 0:
            v = float(int(s))
        else:
            ip = s[:int_d] if int_d > 0 else "0"
            fp = s[int_d:] if int_d > 0 else s
            if ip == "":
                ip = "0"
            v = float(f"{int(ip)}.{fp}")

        return -v if neg else v

    def parse_xy(self, token: str) -> float:
        return self._parse_fixed(token)


def parse_excellon_file(filename, *, strict: bool = False, logger: logging.Logger | None = None):
    lg = _get_logger(logger)

    ex = ExcellonFile()
    if not filename:
        _warn(lg, "Excellon: empty filename.", strict=strict)
        return ex
    if not os.path.exists(filename):
        _warn(lg, f"Excellon: file not found: {filename}", strict=False)
        return ex

    current_tool = None
    unit_scale = 1.0  # to mm

    # Tool def: T01C0.800 or T01D0.031 (some exporters)
    tool_def_re = re.compile(r"^T(\d+)[CD]([\d\.]+)$")
    tool_sel_re = re.compile(r"^T\d+$")

    hole_re = re.compile(r"X(-?[\d\.]+)Y(-?[\d\.]+)")
    file_fmt_re = re.compile(r"FILE_FORMAT\s*=\s*(\d+)\s*:\s*(\d+)", re.IGNORECASE)

    # G85 slots (EasyEDA style)
    g85_re = re.compile(
        r"X(-?[\d\.]+)Y(-?[\d\.]+)G85X(-?[\d\.]+)Y(-?[\d\.]+)", re.IGNORECASE
    )

    # Header units like: METRIC,LZ  or  INCH,TZ
    unit_hdr_re = re.compile(r"^(METRIC|INCH)\s*,\s*(LZ|TZ)\s*$", re.IGNORECASE)

    route_mode = False
    last_route_xy = None

    saw_units = False
    saw_any_tool_def = False

    holes_for_bounds = []

    with open(filename, "r", errors="ignore") as f:
        line_no = 0
        for raw in f:
            line_no += 1
            line = raw.strip()
            if not line:
                continue

            # Comments / format hints
            if line.startswith(";"):
                mfmt = file_fmt_re.search(line)
                if mfmt:
                    try:
                        ex.set_format(int(mfmt.group(1)), int(mfmt.group(2)))
                    except Exception:
                        _warn(
                            lg,
                            f"{os.path.basename(filename)}:{line_no}: bad FILE_FORMAT comment: {line}",
                            strict=strict,
                        )
                continue

            # Header unit line
            m_u = unit_hdr_re.match(line)
            if m_u:
                u = m_u.group(1).upper()
                z = m_u.group(2).upper()
                if u == "METRIC":
                    ex.units = "mm"
                    unit_scale = 1.0
                else:
                    ex.units = "inch"
                    unit_scale = 25.4
                ex.set_zero_suppression("leading" if z == "LZ" else "trailing")
                saw_units = True
                continue

            # Units (legacy)
            if "METRIC" in line or line.startswith("M71"):
                ex.units = "mm"
                unit_scale = 1.0
                saw_units = True
                continue
            if "INCH" in line or line.startswith("M72"):
                ex.units = "inch"
                unit_scale = 25.4
                saw_units = True
                continue

            # Route mode markers (KiCad)
            if line.startswith("M15"):
                route_mode = True
                continue
            if line.startswith("M16"):
                route_mode = False
                last_route_xy = None
                continue

            # Tool definition
            m = tool_def_re.match(line)
            if m:
                tid = "T" + m.group(1)
                try:
                    diam = float(m.group(2)) * unit_scale
                except Exception:
                    _warn(lg, f"{os.path.basename(filename)}:{line_no}: invalid tool diameter: {line}", strict=strict)
                    continue
                ex.add_tool(tid, diam)
                saw_any_tool_def = True
                continue

            # Tool select
            if tool_sel_re.match(line):
                current_tool = line
                last_route_xy = None
                if current_tool not in ex.tools:
                    _warn(
                        lg,
                        f"{os.path.basename(filename)}:{line_no}: selected {current_tool} before/without definition.",
                        strict=False,
                    )
                continue

            # EasyEDA slot (G85)
            if current_tool:
                m = g85_re.search(line)
                if m:
                    try:
                        x1 = ex.parse_xy(m.group(1)) * unit_scale
                        y1 = ex.parse_xy(m.group(2)) * unit_scale
                        x2 = ex.parse_xy(m.group(3)) * unit_scale
                        y2 = ex.parse_xy(m.group(4)) * unit_scale
                    except Exception:
                        _warn(lg, f"{os.path.basename(filename)}:{line_no}: bad G85 slot: {line}", strict=strict)
                        continue
                    ex.add_slot(current_tool, x1, y1, x2, y2)
                    holes_for_bounds.extend([(x1, y1), (x2, y2)])
                    continue

            # Coordinate line (hole or route segment endpoint)
            if current_tool and "X" in line and "Y" in line:
                m = hole_re.search(line)
                if not m:
                    continue

                try:
                    x = ex.parse_xy(m.group(1)) * unit_scale
                    y = ex.parse_xy(m.group(2)) * unit_scale
                except Exception:
                    _warn(lg, f"{os.path.basename(filename)}:{line_no}: bad XY: {line}", strict=strict)
                    continue

                if current_tool not in ex.tools:
                    _warn(
                        lg,
                        f"{os.path.basename(filename)}:{line_no}: XY uses undefined tool {current_tool}; dropping hit.",
                        strict=False,
                    )
                    continue

                if route_mode:
                    if last_route_xy is None:
                        last_route_xy = (x, y)
                    else:
                        x1, y1 = last_route_xy
                        ex.add_slot(current_tool, x1, y1, x, y)
                        last_route_xy = (x, y)
                else:
                    ex.add_hole(current_tool, x, y)

                holes_for_bounds.append((x, y))

    if not saw_units:
        _warn(lg, f"{os.path.basename(filename)}: no explicit units; defaulted to mm.", strict=False)
    if not saw_any_tool_def:
        _warn(lg, f"{os.path.basename(filename)}: no tool definitions found (TxxC...).", strict=False)

    # Sanity extents
    try:
        if holes_for_bounds:
            xs = [p[0] for p in holes_for_bounds]
            ys = [p[1] for p in holes_for_bounds]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if (w > _MAX_REASONABLE_MM) or (h > _MAX_REASONABLE_MM):
                _warn(
                    lg,
                    f"{os.path.basename(filename)}: very large extents ({w:.1f} x {h:.1f} mm). Check units/format/zero suppression.",
                    strict=False,
                )
            if (w < _MIN_REASONABLE_MM) or (h < _MIN_REASONABLE_MM):
                _warn(
                    lg,
                    f"{os.path.basename(filename)}: very small extents ({w:.6f} x {h:.6f} mm). Check units/format/zero suppression.",
                    strict=False,
                )
    except Exception:
        pass

    return ex


def load_drills(prefix, tol_xy=None):
    holes, _slots = load_drills_and_slots(prefix, tol_xy=tol_xy)
    return holes


def load_drills_and_slots(prefix, tol_xy=None, *, strict: bool = False, logger: logging.Logger | None = None):
    lg = _get_logger(logger)

    holes = []
    slots = []

    if not prefix:
        _warn(lg, "Excellon: empty prefix passed to load_drills_and_slots().", strict=strict)
        return holes, slots

    found_any = False
    for suffix in ("-PTH.drl", "-NPTH.drl"):
        fn = prefix + suffix
        if os.path.exists(fn):
            found_any = True
        ex = parse_excellon_file(fn, strict=strict, logger=lg)
        holes.extend(ex.all_holes())
        slots.extend(ex.all_slots())

    if not found_any:
        _warn(lg, f"Excellon: no drill files found for prefix '{prefix}' (-PTH.drl / -NPTH.drl).", strict=False)

    if tol_xy is None:
        tol_xy = load_hole_dedupe_tol()

    holes = dedupe_holes_by_xy(holes, tol_xy=float(tol_xy))
    return holes, slots
