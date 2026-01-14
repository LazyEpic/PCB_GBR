# silkscreen_mill.py
# FULL FILE REPLACEMENT
#
# Fixes:
# - Uses raw silkscreen draw segments (centerlines) from parse_gerber_full()
#   instead of buffered polygons from load_tracks(), which can explode geometry
#   and cause freezes/hangs on complex silkscreen layers.

import math
from shapely.geometry import LineString, MultiLineString
from shapely.affinity import translate

from common_gerber import (
    parse_gerber_full,
    load_copper,
    ensure_header,
    toolchange_sequence,
    end_sequence,
    out_nc,
    cleanup_geometry,
    get_safe_z,
)

DEFAULT_DEPTH = 0.05
MIN_SEGMENT = 0.001


def _order_lines(lines, start_xy=(0.0, 0.0)):
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


def run_silk(bit, combined, prefix):
    # Use copper only as a reference for normalization (0,0 shift)
    copper = load_copper(prefix + "-TopLayer.gbr")
    if copper is None or copper.is_empty:
        print("[SILK] Copper layer missing/empty; cannot normalize")
        return

    silk_fn = prefix + "-TopSilkLayer.gbr"

    # Parse raw draw segments (centerlines)
    gf = parse_gerber_full(silk_fn, strict=False, logger=None)
    if not gf.tracks:
        print("[SILK] No silkscreen draw segments found")
        return

    minx, miny, _, _ = copper.bounds

    segs = []
    for _ap, p1, p2 in gf.tracks:
        try:
            ls = LineString([p1, p2])
            if ls.length > MIN_SEGMENT:
                segs.append(ls)
        except Exception:
            continue

    if not segs:
        print("[SILK] No silkscreen paths generated")
        return

    geom = MultiLineString(segs)
    geom = translate(geom, xoff=-minx, yoff=-miny)

    # Cleanup (mostly removes tiny degenerate bits); safe for lines too.
    geom = cleanup_geometry(geom)

    # Flatten into list of lines
    lines = []
    try:
        if hasattr(geom, "geoms"):
            for g in geom.geoms:
                if isinstance(g, LineString) and not g.is_empty and g.length > MIN_SEGMENT:
                    lines.append(g)
        elif isinstance(geom, LineString) and geom.length > MIN_SEGMENT:
            lines.append(geom)
    except Exception:
        pass

    if not lines:
        print("[SILK] No silkscreen paths after cleanup")
        return

    # Order segments
    lines = _order_lines(lines, start_xy=(0.0, 0.0))

    out = out_nc("all.nc") if combined else out_nc("silkscreen.nc")
    ensure_header(out)

    safe_z = float(get_safe_z())
    depth = float(bit.get("depth", DEFAULT_DEPTH))
    if depth <= 0:
        depth = DEFAULT_DEPTH

    with open(out, "a") as g:
        toolchange_sequence(g, bit, "Silkscreen engraving")
        for ls in lines:
            coords = list(ls.coords)
            if len(coords) < 2:
                continue

            x0, y0 = coords[0]
            g.write(f"G0 Z{safe_z:.3f}\n")
            g.write(f"G0 X{x0:.4f} Y{y0:.4f}\n")
            g.write(f"G1 Z{-depth:.4f} F{bit['feed_z']}\n")
            for (x1, y1) in coords[1:]:
                g.write(f"G1 X{x1:.4f} Y{y1:.4f} F{bit['feed_xy']}\n")
            g.write(f"G0 Z{safe_z:.3f}\n")

        end_sequence(g, end_program=not combined)

    print(f"[SILK] Silkscreen engraved ({len(lines)} paths)")
