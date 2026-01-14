# soldermask_clear.py

import configparser
from shapely.geometry import LineString
from shapely.ops import unary_union

from common_gerber import (
    load_pads,
    load_copper,
    normalize_to_ref,
    ensure_header,
    toolchange_sequence,
    end_sequence,
    out_nc,
)

SAFE_Z = 5.0
DEFAULT_CLEAR_DEPTH = 0.01
MAX_OUTSIDE = 0.10
STEPOVER_RATIO = 0.45


def load_clear_depth():
    cfg = configparser.ConfigParser()
    cfg.read("job_settings.ini")
    try:
        return cfg.getfloat("job", "soldermask_depth")
    except Exception:
        return DEFAULT_CLEAR_DEPTH


def _order_lines(lines, start_xy=(0.0, 0.0)):
    """
    Step 5: nearest-neighbor ordering with reversal.
    """
    if not lines:
        return []

    rem = [ls for ls in lines if ls is not None and not ls.is_empty and len(ls.coords) >= 2]
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
        for i, ls in enumerate(rem):
            c = list(ls.coords)
            s = (float(c[0][0]), float(c[0][1]))
            e = (float(c[-1][0]), float(c[-1][1]))
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

        ls = rem.pop(best_i)
        if best_flip:
            ls = LineString(list(ls.coords)[::-1])

        out.append(ls)
        end = list(ls.coords)[-1]
        cur = (float(end[0]), float(end[1]))

    return out


def clear_pad(poly, bit_d):
    paths = []

    safe = poly.buffer(-(bit_d / 2.0 - MAX_OUTSIDE))
    if safe.is_empty:
        safe = poly.buffer(bit_d / 2.0)

    minx, miny, maxx, maxy = safe.bounds
    w = maxx - minx
    h = maxy - miny
    step = bit_d * STEPOVER_RATIO

    cx, cy = poly.centroid.x, poly.centroid.y

    if w < bit_d * 1.1 or h < bit_d * 1.1:
        paths.append(((cx - bit_d, cy), (cx + bit_d, cy)))
        paths.append(((cx, cy - bit_d), (cx, cy + bit_d)))
        return paths

    y = miny
    flip = False
    while y <= maxy:
        x0, x1 = (minx, maxx) if not flip else (maxx, minx)
        paths.append(((x0, y), (x1, y)))
        y += step
        flip = not flip

    return paths


def run_mask(bit, combined, prefix):
    copper = load_copper(prefix + "-TopLayer.gbr")
    pads = load_pads(prefix + "-TopLayer.gbr")

    if pads is None or pads.is_empty:
        print("[MASK] No pads found")
        return

    pads = normalize_to_ref(pads, copper)

    bit_d = bit["diameter"]
    depth = load_clear_depth()

    geoms = pads.geoms if hasattr(pads, "geoms") else [pads]

    # Build lines
    lines = []
    for p in geoms:
        for (x0, y0), (x1, y1) in clear_pad(p, bit_d):
            lines.append(LineString([(x0, y0), (x1, y1)]))

    if not lines:
        print("[MASK] No clearing paths generated")
        return

    # Step 5: order to reduce rapids
    ordered = _order_lines(lines, start_xy=(0.0, 0.0))

    out = out_nc("all.nc") if combined else out_nc("soldermask_clear.nc")
    ensure_header(out)

    with open(out, "a") as g:
        toolchange_sequence(g, bit, "Soldermask clearing")
        for ls in ordered:
            (x0, y0), (x1, y1) = list(ls.coords)
            g.write(f"G0 Z{SAFE_Z:.3f}\n")
            g.write(f"G0 X{x0:.4f} Y{y0:.4f}\n")
            g.write(f"G1 Z{-depth:.4f} F{bit['feed_z']}\n")
            g.write(f"G1 X{x1:.4f} Y{y1:.4f} F{bit['feed_xy']}\n")
            g.write(f"G0 Z{SAFE_Z:.3f}\n")
        end_sequence(g, end_program=not combined)

    print(f"[MASK] Cleared {len(geoms)} pads (Z=0 → Z=-{depth:.3f} mm)")
