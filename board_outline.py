# board_outline.py

import os
import configparser
import math
from shapely.geometry import LineString
from shapely.ops import substring

from common_gerber import (
    load_tracks,
    load_copper,
    normalize_to_ref,
    ensure_header,
    toolchange_sequence,
    end_sequence,
    out_nc,
)

from excellon_parser import load_drills_and_slots

DEFAULT_PCB_THICKNESS = 1.6
SAFE_Z = 5.0
DEFAULT_MILL_HOLES_OVER = 1.2
DEFAULT_STEPDOWN = 0.5
CIRCLE_SEGMENTS = 72

DEFAULT_HOLE_MATCH_TOL = 0.05
DEFAULT_SINGLE_DRILL_DIAM = 0.8


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


def _job_getfloat(section: str, key: str, default: float) -> float:
    try:
        cfg = _read_job_cfg()
        return float(cfg.getfloat(section, key, fallback=default))
    except Exception:
        return float(default)


def load_pcb_thickness():
    return _job_getfloat("job", "pcb_thickness", DEFAULT_PCB_THICKNESS)


def load_mill_holes_over():
    return _job_getfloat("job", "mill_holes_over", DEFAULT_MILL_HOLES_OVER)


def load_drill_mode():
    cfg = _read_job_cfg()
    try:
        m = (cfg.get("job", "drill_mode", fallback="multi") or "").strip().lower()
    except Exception:
        m = "multi"
    if m in ("single_plus_mill", "single+mill", "single_mill"):
        return "single_plus_mill"
    if m in ("single", "single_drill"):
        return "single"
    return "multi"


def load_single_drill_diam():
    return _job_getfloat("job", "single_drill_diam", DEFAULT_SINGLE_DRILL_DIAM)


def load_hole_match_tol():
    return _job_getfloat("job", "hole_match_tol", DEFAULT_HOLE_MATCH_TOL)


def _stepdown_list(full_depth, stepdown):
    stepdown = float(stepdown) if stepdown and float(stepdown) > 0 else DEFAULT_STEPDOWN
    full_depth = float(full_depth)
    d = 0.0
    out = []
    while d < full_depth - 1e-9:
        d2 = min(d + stepdown, full_depth)
        out.append(d2)
        d = d2
    return out


def _order_segments_nearest(segments, start_xy=(0.0, 0.0)):
    """
    Order line segments with optional reversal.
    segments: [ [(x,y), (x,y), ...], ... ]  (each must have len>=2)
    """
    if not segments:
        return []

    rem = [list(s) for s in segments if s and len(s) >= 2]
    out = []
    cur = (float(start_xy[0]), float(start_xy[1]))

    def d2(a, b):
        dx = a[0] - b[0]
        dy = a[1] - b[1]
        return dx * dx + dy * dy

    while rem:
        best_i = 0
        best_flip = False
        best_d = float("inf")
        for i, pts in enumerate(rem):
            s = (float(pts[0][0]), float(pts[0][1]))
            e = (float(pts[-1][0]), float(pts[-1][1]))
            ds = d2(cur, s)
            if ds < best_d:
                best_d = ds
                best_i = i
                best_flip = False
            de = d2(cur, e)
            if de < best_d:
                best_d = de
                best_i = i
                best_flip = True

        pts = rem.pop(best_i)
        if best_flip:
            pts = pts[::-1]
        out.append(pts)
        cur = (float(pts[-1][0]), float(pts[-1][1]))

    return out


def _order_points_nearest(points, start_xy=(0.0, 0.0)):
    """
    FIX: previously we (incorrectly) tried to order points using _order_segments_nearest()
    with 1-point "segments", which gets filtered out and returns nothing.
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


def _write_polyline(g, pts, z, bit, ramp_len=0.0):
    """
    Ramp-in along first part of path if ramp_len>0.
    """
    if not pts or len(pts) < 2:
        return

    ramp_len = float(ramp_len or 0.0)
    feed_xy = bit["feed_xy"]
    feed_z = bit["feed_z"]

    g.write(f"G0 Z{SAFE_Z:.3f}\n")
    g.write(f"G0 X{pts[0][0]:.4f} Y{pts[0][1]:.4f}\n")

    if ramp_len > 0:
        remaining = ramp_len
        p0 = pts[0]
        ramp_pt = None
        ramp_index = 1

        for i in range(1, len(pts)):
            p1 = pts[i]
            seg = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if seg <= 1e-12:
                p0 = p1
                continue
            if seg >= remaining:
                t = remaining / seg
                ramp_pt = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                ramp_index = i
                break
            remaining -= seg
            p0 = p1

        if ramp_pt is None:
            ramp_pt = pts[1]
            ramp_index = 1

        g.write(f"G1 X{ramp_pt[0]:.4f} Y{ramp_pt[1]:.4f} Z{-z:.4f} F{feed_xy}\n")

        # Finish that segment (if ramp_pt is mid-segment)
        end_seg = pts[ramp_index]
        if abs(end_seg[0] - ramp_pt[0]) > 1e-9 or abs(end_seg[1] - ramp_pt[1]) > 1e-9:
            g.write(f"G1 X{end_seg[0]:.4f} Y{end_seg[1]:.4f} F{feed_xy}\n")

        for i in range(ramp_index + 1, len(pts)):
            x, y = pts[i]
            g.write(f"G1 X{x:.4f} Y{y:.4f} F{feed_xy}\n")
    else:
        g.write(f"G1 Z{-z:.4f} F{feed_z}\n")
        for x, y in pts[1:]:
            g.write(f"G1 X{x:.4f} Y{y:.4f} F{feed_xy}\n")

    g.write(f"G0 Z{SAFE_Z:.3f}\n")


def _slot_offsets(slot_w, tool_d):
    slot_w = float(slot_w)
    tool_d = float(tool_d)
    if tool_d >= slot_w * 0.999:
        return [0.0]
    off = (slot_w - tool_d) / 2.0
    step = tool_d * 0.60
    if step <= 0:
        return [0.0]
    offsets = []
    r = 0.0
    while r < off - 1e-9:
        r = min(r + step, off)
        offsets.append(r)
    out = [0.0]
    for r in offsets:
        out.append(+r)
        out.append(-r)
    return out


def _mill_slot(g, p1, p2, slot_w, full_depth, bit, ramp_len=0.0):
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return

    nx = -dy / L
    ny = dx / L

    tool_d = float(bit["diameter"])
    offsets = _slot_offsets(slot_w, tool_d)
    depths = _stepdown_list(full_depth, bit.get("stepdown", DEFAULT_STEPDOWN))

    for z in depths:
        for o in offsets:
            sx1 = x1 + nx * o
            sy1 = y1 + ny * o
            sx2 = x2 + nx * o
            sy2 = y2 + ny * o
            _write_polyline(g, [(sx1, sy1), (sx2, sy2)], z, bit, ramp_len=ramp_len)


def _circle_points(cx, cy, r):
    pts = []
    for i in range(CIRCLE_SEGMENTS + 1):
        a = (2.0 * math.pi) * (i / CIRCLE_SEGMENTS)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _mill_hole(g, cx, cy, hole_d, full_depth, bit, ramp_len=0.0):
    tool_d = float(bit["diameter"])
    hole_d = float(hole_d)

    # If hole is basically drill-size, don't try to pocket it here.
    if hole_d <= tool_d * 1.02:
        return

    r = (hole_d - tool_d) / 2.0
    if r <= 0:
        return

    rings = []
    step = tool_d * 0.60
    rr = r
    while rr > tool_d * 0.25:
        rings.append(rr)
        rr -= step
        if rr <= 0:
            break

    depths = _stepdown_list(full_depth, bit.get("stepdown", DEFAULT_STEPDOWN))
    for z in depths:
        for rr in rings:
            pts = _circle_points(cx, cy, rr)
            _write_polyline(g, pts, z, bit, ramp_len=ramp_len)


def run_outline(bit, combined, prefix, tabs_enabled=False):
    copper = load_copper(prefix + "-TopLayer.gbr")
    outline = load_tracks(prefix + "-BoardOutLine.gbr")
    outline = normalize_to_ref(outline, copper)

    outline = outline.buffer(bit["diameter"] / 2.0).exterior

    pcb_thickness = load_pcb_thickness()
    full_depth = pcb_thickness
    tab_depth = pcb_thickness * 0.75

    out = out_nc("all.nc") if combined else out_nc("board_outline.nc")
    ensure_header(out)

    ramp_len = float(bit.get("ramp_len", _job_getfloat("job", "ramp_len", 0.0)) or 0.0)

    length = outline.length
    tab_spacing = length * 0.20
    tab_width = 1.0
    tab_half = tab_width / 2.0

    tab_positions = []
    if tabs_enabled and length > 0:
        d = tab_spacing
        while d < length:
            tab_positions.append(d)
            d += tab_spacing

    def is_in_tab(dist):
        for t in tab_positions:
            if abs(dist - t) <= tab_half:
                return True
        return False

    minx, miny, _, _ = copper.bounds
    holes_raw, slots_raw = load_drills_and_slots(prefix)

    slots = [((x1 - minx, y1 - miny), (x2 - minx, y2 - miny), float(w)) for ((x1, y1), (x2, y2), w) in slots_raw]

    mill_over = load_mill_holes_over()
    big_holes = [(x - minx, y - miny, float(d)) for (x, y, d) in holes_raw if float(d) >= float(mill_over)]

    extra_mill_holes = []
    drill_mode = load_drill_mode()
    if drill_mode == "single_plus_mill":
        target = float(load_single_drill_diam())
        tol = float(load_hole_match_tol())
        for x, y, d in holes_raw:
            d = float(d)
            if d >= float(mill_over):
                continue
            if abs(d - target) <= tol:
                continue
            extra_mill_holes.append((x - minx, y - miny, d))

    # ----- Step 5 ordering for slots and holes -----

    # Slots: order by nearest with reversal
    slot_segments = [[p1, p2] for (p1, p2, _w) in slots]
    slot_segments_ord = _order_segments_nearest(slot_segments, start_xy=(0.0, 0.0))

    # Rebuild ordered slots robustly via rounded key
    def _seg_key(a, b, nd=6):
        return (round(a[0], nd), round(a[1], nd), round(b[0], nd), round(b[1], nd))

    slot_map = {}
    for (p1, p2, w) in slots:
        slot_map[_seg_key(p1, p2)] = (p1, p2, w)
        slot_map[_seg_key(p2, p1)] = (p1, p2, w)

    slots_ord = []
    for pts in slot_segments_ord:
        k = _seg_key(tuple(pts[0]), tuple(pts[1]))
        if k in slot_map:
            p1, p2, w = slot_map[k]
            # Keep direction as ordered
            if abs(p1[0] - pts[0][0]) < 1e-9 and abs(p1[1] - pts[0][1]) < 1e-9:
                slots_ord.append((p1, p2, w))
            else:
                slots_ord.append((p2, p1, w))

    # Holes: FIXED ordering (points, not 1-point "segments")
    hole_items = big_holes + extra_mill_holes
    hole_pts = [(x, y) for (x, y, _d) in hole_items]

    hole_order = _order_points_nearest(hole_pts, start_xy=(0.0, 0.0))

    # Map (x,y) -> diameter (rounded key to avoid float mismatch)
    hole_d_map = {}
    for x, y, d in hole_items:
        hole_d_map[(round(float(x), 6), round(float(y), 6))] = float(d)

    def _hole_d(x, y):
        return hole_d_map.get((round(float(x), 6), round(float(y), 6)))

    # ----- Write G-code -----
    with open(out, "a") as g:
        toolchange_sequence(g, bit, "Through cuts: slots/holes/outline")

        # Mill slots
        for (p1, p2, w) in slots_ord:
            _mill_slot(g, p1, p2, w, full_depth, bit, ramp_len=ramp_len)

        # Mill big + extra holes
        for (x, y) in hole_order:
            d = _hole_d(x, y)
            if d is not None:
                _mill_hole(g, x, y, d, full_depth, bit, ramp_len=ramp_len)

        # Outline with optional tabs
        step = 0.5
        dist = 0.0
        coords = list(outline.coords)
        if not coords:
            end_sequence(g, end_program=not combined)
            return

        g.write(f"G0 Z{SAFE_Z:.3f}\n")
        g.write(f"G0 X{coords[0][0]:.4f} Y{coords[0][1]:.4f}\n")

        # Initial ramp along first segment if enabled
        if ramp_len > 0 and len(coords) >= 2:
            first_depth = tab_depth if is_in_tab(0.0) else full_depth

            remaining = ramp_len
            p0 = coords[0]
            ramp_pt = None
            ramp_dist = 0.0
            ramp_seg_i = 1

            for i in range(1, len(coords)):
                p1 = coords[i]
                seg = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                if seg <= 1e-12:
                    p0 = p1
                    continue
                if seg >= remaining:
                    t = remaining / seg
                    ramp_pt = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                    ramp_dist = remaining
                    ramp_seg_i = i
                    break
                remaining -= seg
                p0 = p1

            if ramp_pt is None:
                ramp_pt = coords[1]
                ramp_dist = math.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
                ramp_seg_i = 1

            g.write(f"G1 X{ramp_pt[0]:.4f} Y{ramp_pt[1]:.4f} Z{-first_depth:.4f} F{bit['feed_xy']}\n")

            # Finish the segment to its endpoint (if ramp was mid-segment)
            end_seg = coords[ramp_seg_i]
            if abs(end_seg[0] - ramp_pt[0]) > 1e-9 or abs(end_seg[1] - ramp_pt[1]) > 1e-9:
                g.write(f"G1 X{end_seg[0]:.4f} Y{end_seg[1]:.4f} F{bit['feed_xy']}\n")

            dist = ramp_dist
        else:
            dist = 0.0

        while dist < length:
            depth = tab_depth if is_in_tab(dist) else full_depth
            g.write(f"G1 Z{-depth:.4f} F{bit['feed_z']}\n")

            next_dist = min(dist + step, length)
            seg = substring(outline, dist, next_dist)

            if isinstance(seg, LineString):
                for x, y in list(seg.coords)[1:]:
                    g.write(f"G1 X{x:.4f} Y{y:.4f} F{bit['feed_xy']}\n")

            dist = next_dist

        g.write(f"G0 Z{SAFE_Z:.3f}\n")
        end_sequence(g, end_program=not combined)

    if tabs_enabled:
        print("[OUTLINE] Board outline with tabs generated")
    else:
        print("[OUTLINE] Board outline generated (no tabs)")

    if slots_ord:
        print(f"[OUTLINE] Routed {len(slots_ord)} slot(s)")
    if big_holes:
        print(f"[OUTLINE] Milled {len(big_holes)} large hole(s)")
    if extra_mill_holes:
        print(f"[OUTLINE] Milled {len(extra_mill_holes)} non-matching small hole(s) (single+mill)")
