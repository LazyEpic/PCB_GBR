# drilling.py

import os
import configparser
import math

from common_gerber import (
    load_copper,
    ensure_header,
    toolchange_sequence,
    end_sequence,
    out_nc,
)

from excellon_parser import (
    load_drills_and_slots,
    parse_excellon_file,
    dedupe_holes_by_xy,
    load_hole_dedupe_tol,
)

DEFAULT_PCB_THICKNESS = 1.6  # mm fallback
SAFE_Z = 5.0
DEFAULT_MILL_HOLES_OVER = 1.2  # mm
DEFAULT_HOLE_MATCH_TOL = 0.05  # mm


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


def load_pcb_thickness():
    cfg = _read_job_cfg()
    try:
        return cfg.getfloat("job", "pcb_thickness")
    except Exception:
        return DEFAULT_PCB_THICKNESS


def load_mill_holes_over():
    cfg = _read_job_cfg()
    try:
        return cfg.getfloat("job", "mill_holes_over")
    except Exception:
        return DEFAULT_MILL_HOLES_OVER


def load_hole_match_tol():
    cfg = _read_job_cfg()
    try:
        return cfg.getfloat("job", "hole_match_tol")
    except Exception:
        return DEFAULT_HOLE_MATCH_TOL


def _fallback_load_any_drl(tol_xy=None):
    holes = []
    slots = []

    candidates = []
    for f in os.listdir():
        low = f.lower()
        if low.endswith(".drl") or low.endswith(".txt"):
            candidates.append(f)
    candidates.sort()

    for fn in candidates:
        try:
            ex = parse_excellon_file(fn)
            holes.extend(ex.all_holes())
            slots.extend(ex.all_slots())
        except Exception:
            continue

    if tol_xy is None:
        tol_xy = load_hole_dedupe_tol()
    holes = dedupe_holes_by_xy(holes, float(tol_xy))
    return holes, slots


def _assign_holes_to_drills(holes, drill_bits, tol):
    """
    holes: [(x,y,diam)]
    drill_bits: list of bit dicts with diameter>0
    tol: mm

    Returns list of (bit, [(x,y),...]) sorted by diameter desc.
    Each hole goes to the largest drill <= hole_d + tol.
    """
    tol = float(tol)

    drills = [b for b in (drill_bits or []) if float(b.get("diameter", 0.0)) > 0]
    drills.sort(key=lambda b: float(b["diameter"]))  # ascending

    if not drills:
        return None, "No drill bits provided."

    ds = [float(b["diameter"]) for b in drills]
    assigned = {id(b): [] for b in drills}
    used = set()

    for x, y, hd in holes:
        limit = float(hd) + tol
        pick = None
        for i, d in enumerate(ds):
            if d <= limit + 1e-9:
                pick = drills[i]
            else:
                break
        if pick is None:
            return None, f"Impossible: no drill <= {hd:.3f}+{tol:.3f}mm"
        assigned[id(pick)].append((x, y))
        used.add(id(pick))

    ordered = []
    for b in sorted([b for b in drills if id(b) in used], key=lambda bb: float(bb["diameter"]), reverse=True):
        ordered.append((b, assigned[id(b)]))

    return ordered, None


def _order_points_nearest(points, start_xy=(0.0, 0.0)):
    """
    Step 5: order drill hits to minimize travel (nearest neighbor).
    points: [(x,y),...]
    """
    if not points:
        return []

    rem = [(float(x), float(y)) for (x, y) in points]
    out = []
    curx, cury = float(start_xy[0]), float(start_xy[1])

    while rem:
        best_i = 0
        best_d2 = float("inf")
        for i, (x, y) in enumerate(rem):
            dx = x - curx
            dy = y - cury
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_i = i
        x, y = rem.pop(best_i)
        out.append((x, y))
        curx, cury = x, y

    return out


def run_drill(bit, combined, prefix, drill_bits=None, tol=None):
    """
    If drill_bits is provided (list of bit dicts), uses planned drilling:
      - largest drill that fits each hole (<= hole_d + tol)
      - toolchange between each drill size used
      - Step 5: orders holes per tool to reduce travel
    Else legacy fallback (multi by DRL tool diameters, using single 'bit') with ordering.
    """
    copper = load_copper(prefix + "-TopLayer.gbr")
    minx, miny, _, _ = copper.bounds

    tol_xy = load_hole_dedupe_tol()

    holes_raw = []
    slots_raw = []
    try:
        holes_raw, slots_raw = load_drills_and_slots(prefix, tol_xy=tol_xy)
    except Exception:
        holes_raw, slots_raw = [], []

    if not holes_raw and not slots_raw:
        holes_raw, slots_raw = _fallback_load_any_drl(tol_xy=tol_xy)

    if not holes_raw:
        print("[DRILL] No round drill hits found, skipping")
        return

    mill_over = load_mill_holes_over()
    small_holes_raw = [(x, y, float(d)) for (x, y, d) in holes_raw if float(d) < float(mill_over)]
    small_holes_raw = dedupe_holes_by_xy(small_holes_raw, float(tol_xy))

    if not small_holes_raw:
        print("[DRILL] All holes are marked for milling, skipping drill phase")
        return

    holes = [(x - minx, y - miny, d) for (x, y, d) in small_holes_raw]

    pcb_thickness = load_pcb_thickness()
    depth = pcb_thickness

    out = out_nc("all.nc") if combined else out_nc("drill.nc")
    ensure_header(out)

    if drill_bits:
        tol = float(tol) if tol is not None else float(load_hole_match_tol())
        plan, err = _assign_holes_to_drills(holes, drill_bits, tol)
        if err:
            print(f"[DRILL] {err}")
            return

        total = sum(len(pts) for (_b, pts) in plan)

        with open(out, "a") as g:
            cur = (0.0, 0.0)
            for b, pts in plan:
                pts = _order_points_nearest(pts, start_xy=cur)
                if pts:
                    cur = pts[-1]

                toolchange_sequence(
                    g,
                    b,
                    f"Drill: {b['name']} ({float(b['diameter']):.3f}mm) | {len(pts)} holes",
                )
                for x, y in pts:
                    g.write(f"G0 Z{SAFE_Z:.3f}\n")
                    g.write(f"G0 X{x:.4f} Y{y:.4f}\n")
                    g.write(f"G1 Z{-depth:.4f} F{b['feed_z']}\n")
                    g.write(f"G0 Z{SAFE_Z:.3f}\n")

            end_sequence(g, end_program=not combined)

        print(f"[DRILL] {total} holes drilled, depth {depth:.2f} mm")
        return

    by_diam = {}
    for x, y, d in holes:
        key = round(float(d), 3)
        by_diam.setdefault(key, []).append((x, y))

    total = 0
    with open(out, "a") as g:
        cur = (0.0, 0.0)
        for diam in sorted(by_diam.keys(), reverse=True):
            holes_xy = _order_points_nearest(by_diam[diam], start_xy=cur)
            if holes_xy:
                cur = holes_xy[-1]

            total += len(holes_xy)

            toolchange_sequence(g, bit, f"Change drill to {diam:.3f}mm")
            for x, y in holes_xy:
                g.write(f"G0 Z{SAFE_Z:.3f}\n")
                g.write(f"G0 X{x:.4f} Y{y:.4f}\n")
                g.write(f"G1 Z{-depth:.4f} F{bit['feed_z']}\n")
                g.write(f"G0 Z{SAFE_Z:.3f}\n")

        end_sequence(g, end_program=not combined)

    print(f"[DRILL] {total} holes drilled, depth {depth:.2f} mm")
