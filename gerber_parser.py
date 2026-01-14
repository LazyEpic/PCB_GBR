# gerber_parser.py
#
# Gerber parsing + copper/pads/tracks helpers.
# This module does NOT know about job settings or gcode; it just parses Gerber and returns geometry/data.

import os
import re
import logging
from dataclasses import dataclass
from typing import Dict, Tuple, List, Optional, Any

from shapely.geometry import (
    Point,
    LineString,
    Polygon,
)
from shapely.ops import unary_union
from shapely.affinity import rotate

_LOGGER_NAME = "cnc_pcb.gerber"
_default_logger = logging.getLogger(_LOGGER_NAME)

_MAX_REASONABLE_MM = 2000.0
_MIN_REASONABLE_MM = 0.01


class GerberParseError(RuntimeError):
    pass


def _get_logger(logger: Optional[logging.Logger]) -> logging.Logger:
    return logger if logger is not None else _default_logger


def _warn(logger: logging.Logger, msg: str, *, strict: bool) -> None:
    logger.warning(msg)
    if strict:
        raise GerberParseError(msg)


def _debug(logger: logging.Logger, msg: str) -> None:
    logger.debug(msg)


_FS_FULL_RE = re.compile(r"FS([LT])([AI])X(\d)(\d)Y(\d)(\d)")
_MO_MM = "MOMM"
_MO_IN = "MOIN"

_LP_DARK_RE = re.compile(r"LPD", re.IGNORECASE)
_LP_CLEAR_RE = re.compile(r"LPC", re.IGNORECASE)

_ADD_STD_RE = re.compile(
    r"%ADD(\d+)([A-Z])\s*,?\s*([0-9\.\+\-]+)(?:X([0-9\.\+\-]+))?.*\*%?"
)
_ADD_MACRO_RE = re.compile(
    r"%ADD(\d+)([A-Za-z][A-Za-z0-9_]*)\s*,?\s*([0-9\.\+\-]+)(?:X([0-9\.\+\-]+))?(?:X([0-9\.\+\-]+))?.*\*%?"
)

_AM_START_RE = re.compile(r"%AM([A-Za-z][A-Za-z0-9_]*)\*")
_AM_END_RE = re.compile(r"\*%\s*$")


def _parse_units(line: str):
    if _MO_IN in line:
        return "inch"
    if _MO_MM in line:
        return "mm"
    return None


def _parse_fs_full(line: str):
    m = _FS_FULL_RE.search(line)
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))


def _parse_macro_defs(lines: List[str]) -> Dict[str, Dict[str, Any]]:
    macros: Dict[str, Dict[str, Any]] = {}

    i = 0
    while i < len(lines):
        l = lines[i].strip()
        m = _AM_START_RE.match(l)
        if not m:
            i += 1
            continue

        name = m.group(1)
        body_parts = [l]

        if not _AM_END_RE.search(l):
            j = i + 1
            while j < len(lines):
                body_parts.append(lines[j].strip())
                if _AM_END_RE.search(lines[j]):
                    break
                j += 1
            i = j

        body = "".join(body_parts)

        try:
            inside = body.split("*", 1)[1]
            inside = inside.rsplit("*%", 1)[0]
        except Exception:
            inside = ""

        inside = inside.strip()

        # Tiny macro support used by some CAD exports:
        # center rectangle primitive: "21,1,$1,$2,..."
        if inside.startswith("21,") and "$1" in inside and "$2" in inside:
            macros[name] = {"type": "center_rect_21"}

        i += 1

    return macros


def _parse_rs274x_coord(tok: str, *, int_d: int, dec_d: int, zero_mode: str) -> float:
    if tok is None:
        return 0.0

    tok = tok.strip()
    if not tok:
        return 0.0

    if "." in tok:
        return float(tok)

    neg = tok.startswith("-")
    tok_digits = tok[1:] if neg else tok

    total = int_d + dec_d
    if total <= 0:
        total = max(1, len(tok_digits))

    if len(tok_digits) > total:
        tok_digits = tok_digits[-total:]

    if len(tok_digits) < total:
        if zero_mode.upper() == "L":
            tok_digits = tok_digits.rjust(total, "0")
        else:
            tok_digits = tok_digits.ljust(total, "0")

    if dec_d <= 0:
        val = float(int(tok_digits))
    else:
        ip = tok_digits[:int_d] if int_d > 0 else "0"
        fp = tok_digits[int_d:] if int_d > 0 else tok_digits
        if ip == "":
            ip = "0"
        val = float(f"{int(ip)}.{fp}")

    return -val if neg else val


@dataclass
class GerberFull:
    aps: Dict[int, Any]
    macros: Dict[str, Dict[str, Any]]

    flashes: List[Tuple[int, float, float]]
    tracks: List[Tuple[int, Tuple[float, float], Tuple[float, float]]]

    dark_geoms: List[Any]
    clear_geoms: List[Any]

    units: str
    fs_zero_mode: str
    fs_coord_mode: str
    fs_x: Tuple[int, int]
    fs_y: Tuple[int, int]


def pad_from_ap(ap_def, x: float, y: float, macros: Dict[str, Dict[str, Any]]):
    if not ap_def:
        return None

    shape = ap_def[0]

    if shape == "C":
        a = float(ap_def[1])
        return Point(x, y).buffer(a / 2.0)

    if shape == "R":
        a = float(ap_def[1])
        b = float(ap_def[2])
        return Polygon(
            [
                (x - a / 2.0, y - b / 2.0),
                (x + a / 2.0, y - b / 2.0),
                (x + a / 2.0, y + b / 2.0),
                (x - a / 2.0, y + b / 2.0),
            ]
        )

    if shape == "O":
        a = float(ap_def[1])
        b = float(ap_def[2])
        r = min(a, b) / 2.0
        if a > b:
            return LineString([(x - (a / 2.0 - r), y), (x + (a / 2.0 - r), y)]).buffer(r)
        return LineString([(x, y - (b / 2.0 - r)), (x, y + (b / 2.0 - r))]).buffer(r)

    if shape == "MACRO":
        name = ap_def[1]
        params = ap_def[2] if len(ap_def) > 2 else []
        mdef = macros.get(name, {})
        mtype = mdef.get("type")

        if mtype == "center_rect_21":
            w = float(params[0]) if len(params) > 0 else 0.0
            h = float(params[1]) if len(params) > 1 else w
            rot = float(params[2]) if len(params) > 2 else 0.0

            poly = Polygon(
                [
                    (x - w / 2.0, y - h / 2.0),
                    (x + w / 2.0, y - h / 2.0),
                    (x + w / 2.0, y + h / 2.0),
                    (x - w / 2.0, y + h / 2.0),
                ]
            )
            if abs(rot) > 1e-9:
                poly = rotate(poly, rot, origin=(x, y), use_radians=False)
            return poly

    return None


def parse_gerber_full(fn: str, *, strict: bool = False, logger: Optional[logging.Logger] = None) -> GerberFull:
    lg = _get_logger(logger)

    if not fn or not os.path.exists(fn):
        raise FileNotFoundError(fn)

    with open(fn, "r", errors="ignore") as f:
        lines = [ln.rstrip("\n") for ln in f]

    macros = _parse_macro_defs(lines)

    aps: Dict[int, Any] = {}
    flashes: List[Tuple[int, float, float]] = []
    tracks: List[Tuple[int, Tuple[float, float], Tuple[float, float]]] = []

    dark_geoms: List[Any] = []
    clear_geoms: List[Any] = []

    cur: Optional[int] = None
    prev: Optional[Tuple[float, float]] = None

    units = "mm"
    unit_scale = 1.0
    saw_units = False

    fs_zero_mode = "L"
    fs_coord_mode = "A"
    x_int, x_dec = 3, 6
    y_int, y_dec = 3, 6
    saw_fs = False

    polarity = "D"  # D=dark, C=clear

    in_region = False
    region_contours: List[List[Tuple[float, float]]] = []
    region_current: Optional[List[Tuple[float, float]]] = None

    line_no = 0

    for raw in lines:
        line_no += 1
        l = raw.strip()
        if not l:
            continue

        if l.startswith("G04") or l.startswith(";"):
            continue

        u = _parse_units(l)
        if u:
            units = u
            unit_scale = 25.4 if units == "inch" else 1.0
            saw_units = True
            continue

        fs = _parse_fs_full(l)
        if fs:
            fs_zero_mode, fs_coord_mode, x_int, x_dec, y_int, y_dec = fs
            saw_fs = True
            continue

        if _LP_DARK_RE.search(l):
            polarity = "D"
            continue
        if _LP_CLEAR_RE.search(l):
            polarity = "C"
            continue

        if l.startswith("G36"):
            in_region = True
            region_contours = []
            region_current = None
            prev = None
            continue

        if l.startswith("G37"):
            if region_current and len(region_current) >= 3:
                region_contours.append(region_current)

            polys = []
            for pts in region_contours:
                if len(pts) < 3:
                    continue
                if pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                try:
                    poly = Polygon(pts)
                    if not poly.is_empty and poly.is_valid and poly.area > 0:
                        polys.append(poly)
                except Exception:
                    _warn(lg, f"{os.path.basename(fn)}:{line_no}: failed building region polygon.", strict=strict)

            if polys:
                rg = unary_union(polys)
                if polarity == "D":
                    dark_geoms.append(rg)
                else:
                    clear_geoms.append(rg)

            in_region = False
            region_contours = []
            region_current = None
            prev = None
            continue

        m = _ADD_STD_RE.match(l)
        if m:
            ap_id = int(m.group(1))
            shape = m.group(2)
            try:
                a = float(m.group(3)) * unit_scale
                b = float(m.group(4) if m.group(4) is not None else m.group(3)) * unit_scale
            except Exception:
                _warn(lg, f"{os.path.basename(fn)}:{line_no}: invalid ADD params: {l}", strict=strict)
                continue
            aps[ap_id] = (shape, a, b)
            continue

        m = _ADD_MACRO_RE.match(l)
        if m:
            ap_id = int(m.group(1))
            name = m.group(2)
            try:
                p1 = float(m.group(3)) * unit_scale
                p2 = float(m.group(4)) * unit_scale if m.group(4) is not None else None
                p3 = float(m.group(5)) if m.group(5) is not None else None
            except Exception:
                _warn(lg, f"{os.path.basename(fn)}:{line_no}: invalid macro ADD params: {l}", strict=strict)
                continue
            params = [p for p in (p1, p2, p3) if p is not None]
            aps[ap_id] = ("MACRO", name, params)
            if name not in macros:
                _warn(
                    lg,
                    f"{os.path.basename(fn)}:{line_no}: macro aperture uses undefined macro '{name}' (aperture D{ap_id}).",
                    strict=strict,
                )
            continue

        mm = re.fullmatch(r"D(\d+)\*", l)
        if mm:
            try:
                cur = int(mm.group(1))
            except Exception:
                cur = None
            continue

        xm = re.search(r"X(-?[\d\.]+)", l)
        ym = re.search(r"Y(-?[\d\.]+)", l)
        if not xm or not ym:
            continue

        try:
            x = _parse_rs274x_coord(xm.group(1), int_d=x_int, dec_d=x_dec, zero_mode=fs_zero_mode) * unit_scale
            y = _parse_rs274x_coord(ym.group(1), int_d=y_int, dec_d=y_dec, zero_mode=fs_zero_mode) * unit_scale
        except Exception:
            _warn(lg, f"{os.path.basename(fn)}:{line_no}: invalid coordinate line: {l}", strict=strict)
            continue

        if fs_coord_mode.upper() == "I" and prev is not None:
            x = prev[0] + x
            y = prev[1] + y

        if in_region:
            if l.endswith("D02*"):
                if region_current and len(region_current) >= 3:
                    region_contours.append(region_current)
                region_current = [(x, y)]
                prev = (x, y)
                continue

            if l.endswith("D01*"):
                if region_current is None:
                    region_current = []
                region_current.append((x, y))
                prev = (x, y)
                continue

            if l.endswith("D03*"):
                prev = (x, y)
                continue

        if l.endswith("D03*") and cur is not None:
            if cur not in aps:
                _warn(lg, f"{os.path.basename(fn)}:{line_no}: flash uses undefined aperture D{cur}.", strict=strict)
            flashes.append((cur, x, y))
            ap_def = aps.get(int(cur))
            g = pad_from_ap(ap_def, x, y, macros) if ap_def else None
            if g is not None and not g.is_empty:
                (dark_geoms if polarity == "D" else clear_geoms).append(g)
            prev = (x, y)

        elif l.endswith("D01*") and prev is not None and cur is not None:
            if cur not in aps:
                _warn(lg, f"{os.path.basename(fn)}:{line_no}: draw uses undefined aperture D{cur}.", strict=strict)
            tracks.append((cur, prev, (x, y)))

            ap_def = aps.get(int(cur))
            if ap_def and ap_def[0] != "MACRO":
                shape, a, b = ap_def
                width = float(a) if shape == "C" else min(float(a), float(b))
                if width > 0:
                    gg = LineString([prev, (x, y)]).buffer(width / 2.0)
                    if gg is not None and not gg.is_empty:
                        (dark_geoms if polarity == "D" else clear_geoms).append(gg)

            prev = (x, y)

        elif l.endswith("D02*"):
            prev = (x, y)

    if not saw_units:
        _warn(lg, f"{os.path.basename(fn)}: no explicit units (MOMM/MOIN) found; defaulted to mm.", strict=False)
    if not saw_fs:
        _warn(lg, f"{os.path.basename(fn)}: no FS format found; defaulted to L,A, X3.6 / Y3.6.", strict=False)
    if not aps:
        _warn(lg, f"{os.path.basename(fn)}: no aperture definitions found.", strict=False)

    try:
        allg = []
        if dark_geoms:
            allg.append(unary_union(dark_geoms))
        if clear_geoms:
            allg.append(unary_union(clear_geoms))
        if allg:
            bb = unary_union(allg).bounds
            minx, miny, maxx, maxy = bb
            w = maxx - minx
            h = maxy - miny
            if (w > _MAX_REASONABLE_MM) or (h > _MAX_REASONABLE_MM):
                _warn(
                    lg,
                    f"{os.path.basename(fn)}: very large extents ({w:.1f} x {h:.1f} mm). Check units/FS/zero suppression.",
                    strict=False,
                )
            if (w < _MIN_REASONABLE_MM) or (h < _MIN_REASONABLE_MM):
                _warn(
                    lg,
                    f"{os.path.basename(fn)}: very small extents ({w:.6f} x {h:.6f} mm). Check units/FS/zero suppression.",
                    strict=False,
                )
    except Exception:
        pass

    _debug(
        lg,
        f"Parsed {os.path.basename(fn)}: aps={len(aps)} flashes={len(flashes)} tracks={len(tracks)} "
        f"dark={len(dark_geoms)} clear={len(clear_geoms)} units={units} FS={fs_zero_mode}{fs_coord_mode} "
        f"X{x_int}.{x_dec} Y{y_int}.{y_dec}",
    )

    return GerberFull(
        aps=aps,
        macros=macros,
        flashes=flashes,
        tracks=tracks,
        dark_geoms=dark_geoms,
        clear_geoms=clear_geoms,
        units=units,
        fs_zero_mode=fs_zero_mode,
        fs_coord_mode=fs_coord_mode,
        fs_x=(x_int, x_dec),
        fs_y=(y_int, y_dec),
    )


def parse_gerber(fn: str, *, strict: bool = False, logger: Optional[logging.Logger] = None):
    gf = parse_gerber_full(fn, strict=strict, logger=logger)
    return gf.aps, gf.flashes, gf.tracks, gf.macros


def _compose_dark_clear(dark_geoms: List[Any], clear_geoms: List[Any]):
    dark_u = unary_union(dark_geoms) if dark_geoms else unary_union([])
    if not clear_geoms:
        return dark_u
    clear_u = unary_union(clear_geoms)
    try:
        return dark_u.difference(clear_u)
    except Exception:
        return dark_u


def load_copper(fn: str, *, strict: bool = False, logger: Optional[logging.Logger] = None):
    gf = parse_gerber_full(fn, strict=strict, logger=logger)
    return _compose_dark_clear(gf.dark_geoms, gf.clear_geoms)


def load_pads(fn: str, *, strict: bool = False, logger: Optional[logging.Logger] = None):
    gf = parse_gerber_full(fn, strict=strict, logger=logger)

    pads_u = []
    for ap_id, x, y in gf.flashes:
        ap_def = gf.aps.get(int(ap_id))
        g = pad_from_ap(ap_def, x, y, gf.macros) if ap_def else None
        if g is not None and not g.is_empty:
            pads_u.append(g)
    pads_u = unary_union(pads_u) if pads_u else unary_union([])

    composed = _compose_dark_clear(gf.dark_geoms, gf.clear_geoms)
    try:
        return composed.intersection(pads_u)
    except Exception:
        return pads_u


def load_tracks(fn: str, *, strict: bool = False, logger: Optional[logging.Logger] = None):
    gf = parse_gerber_full(fn, strict=strict, logger=logger)

    track_geoms = []
    for ap_id, p1, p2 in gf.tracks:
        ap_def = gf.aps.get(int(ap_id))
        if not ap_def or ap_def[0] == "MACRO":
            continue
        shape, a, b = ap_def
        width = float(a) if shape == "C" else min(float(a), float(b))
        if width <= 0:
            continue
        g = LineString([p1, p2]).buffer(width / 2.0)
        if g is not None and not g.is_empty:
            track_geoms.append(g)

    tracks_u = unary_union(track_geoms) if track_geoms else unary_union([])
    composed = _compose_dark_clear(gf.dark_geoms, gf.clear_geoms)
    try:
        return composed.intersection(tracks_u)
    except Exception:
        return tracks_u
