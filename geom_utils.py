# geom_utils.py
#
# Geometry utilities:
# - cleanup / simplify / filter
# - line extraction
# - path ordering
# - (optional) ramp entry while writing paths

import math
from typing import Dict, Tuple, List, Any, Iterable

from shapely.geometry import (
    LineString,
    Polygon,
    MultiPolygon,
    MultiLineString,
    GeometryCollection,
)
from shapely.ops import unary_union

from job_config import (
    job_getfloat,
    job_getbool,
    get_safe_z,
    get_park_xy,
)


def _iter_geoms(g):
    if g is None or g.is_empty:
        return
    if isinstance(g, (Polygon, LineString)):
        yield g
        return
    if isinstance(g, (MultiPolygon, MultiLineString, GeometryCollection)) or hasattr(g, "geoms"):
        for gg in g.geoms:
            yield from _iter_geoms(gg)


def _iter_lines_from_geom(geom) -> Iterable[LineString]:
    for g in _iter_geoms(geom):
        if isinstance(g, LineString):
            yield g
        elif isinstance(g, Polygon):
            if g.exterior is not None:
                yield LineString(g.exterior.coords)
            for ring in g.interiors:
                yield LineString(ring.coords)


def _is_polygonal(g) -> bool:
    return isinstance(g, (Polygon, MultiPolygon))


def _safe_buffer0_polygonal(g):
    if g is None or g.is_empty:
        return g
    if not _is_polygonal(g):
        return g
    try:
        return g.buffer(0)
    except Exception:
        return g


def cleanup_geometry(
    geom,
    *,
    simplify_tol: float = 0.0,
    min_area: float = 0.0,
    min_length: float = 0.0,
):
    if geom is None or geom.is_empty:
        return geom

    try:
        u = unary_union(geom)
    except Exception:
        u = geom

    if _is_polygonal(u):
        u = _safe_buffer0_polygonal(u)
    else:
        if isinstance(u, (GeometryCollection,)) or hasattr(u, "geoms"):
            fixed_parts = []
            for gg in u.geoms:
                fixed_parts.append(_safe_buffer0_polygonal(gg) if _is_polygonal(gg) else gg)
            try:
                u = unary_union(fixed_parts)
            except Exception:
                u = u

    if simplify_tol and simplify_tol > 0:
        try:
            u = u.simplify(float(simplify_tol), preserve_topology=True)
        except Exception:
            pass

    kept = []
    for g in _iter_geoms(u):
        if isinstance(g, Polygon):
            if float(g.area) >= float(min_area):
                kept.append(g)
        elif isinstance(g, LineString):
            if float(g.length) >= float(min_length):
                kept.append(g)
        else:
            kept.append(g)

    if not kept:
        return unary_union([])

    try:
        return unary_union(kept)
    except Exception:
        return kept[0]


def _dist2(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return dx * dx + dy * dy


def order_lines_nearest(
    lines: List[LineString],
    *,
    start_xy: Tuple[float, float] = (0.0, 0.0),
    allow_reverse: bool = True,
) -> List[LineString]:
    if not lines:
        return []

    remaining = [ls for ls in lines if ls is not None and not ls.is_empty and len(ls.coords) >= 2]
    if not remaining:
        return []

    ordered: List[LineString] = []
    cur = start_xy

    while remaining:
        best_i = 0
        best_flip = False
        best_d2 = float("inf")

        for i, ls in enumerate(remaining):
            coords = list(ls.coords)
            s = (float(coords[0][0]), float(coords[0][1]))
            e = (float(coords[-1][0]), float(coords[-1][1]))

            d2s = _dist2(cur, s)
            if d2s < best_d2:
                best_d2 = d2s
                best_i = i
                best_flip = False

            if allow_reverse:
                d2e = _dist2(cur, e)
                if d2e < best_d2:
                    best_d2 = d2e
                    best_i = i
                    best_flip = True

        pick = remaining.pop(best_i)
        if best_flip:
            pick = LineString(list(pick.coords)[::-1])

        ordered.append(pick)
        end = list(pick.coords)[-1]
        cur = (float(end[0]), float(end[1]))

    return ordered


def _default_cleanup_params():
    simplify_tol = job_getfloat("job", "geom_simplify_tol", 0.0005)  # mm
    min_area = job_getfloat("job", "geom_min_area", 1e-8)  # mm^2
    min_length = job_getfloat("job", "geom_min_length", 1e-5)  # mm
    return simplify_tol, min_area, min_length


def _default_ordering_enabled():
    return job_getbool("job", "path_ordering", True)


def _default_ramp_len(bit: Dict[str, Any]) -> float:
    try:
        v = float(bit.get("ramp_len", 0.0))
        if v > 0:
            return v
    except Exception:
        pass
    return job_getfloat("job", "ramp_len", 0.0)


def geom_to_ordered_lines(geom, *, start_xy=(0.0, 0.0)) -> List[LineString]:
    simplify_tol, min_area, min_length = _default_cleanup_params()
    cleaned = cleanup_geometry(geom, simplify_tol=simplify_tol, min_area=min_area, min_length=min_length)

    lines = list(_iter_lines_from_geom(cleaned))
    if not lines:
        return []

    if _default_ordering_enabled():
        return order_lines_nearest(lines, start_xy=start_xy, allow_reverse=True)
    return lines


def _write_line_as_gcode(o, ls: LineString, *, depth: float, bit: Dict[str, Any], ramp_len: float):
    coords = list(ls.coords)
    if len(coords) < 2:
        return

    feed_xy = bit["feed_xy"]
    feed_z = bit["feed_z"]

    x0, y0 = coords[0]
    o.write(f"G0 Z{get_safe_z():.3f}\n")
    o.write(f"G0 X{x0:.4f} Y{y0:.4f}\n")

    ramp_len = float(ramp_len or 0.0)
    if ramp_len > 0:
        remaining = ramp_len
        ramp_pt = None
        seg_end_index = 1

        p0 = coords[0]
        for i in range(1, len(coords)):
            p1 = coords[i]
            seg_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if seg_len <= 1e-12:
                p0 = p1
                continue

            if seg_len >= remaining:
                t = remaining / seg_len
                ramp_pt = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                seg_end_index = i
                break

            remaining -= seg_len
            p0 = p1

        if ramp_pt is None:
            ramp_pt = coords[1]
            seg_end_index = 1

        o.write(f"G1 X{ramp_pt[0]:.4f} Y{ramp_pt[1]:.4f} Z{-depth:.4f} F{feed_xy}\n")

        end_seg = coords[seg_end_index]
        if abs(end_seg[0] - ramp_pt[0]) > 1e-9 or abs(end_seg[1] - ramp_pt[1]) > 1e-9:
            o.write(f"G1 X{end_seg[0]:.4f} Y{end_seg[1]:.4f} F{feed_xy}\n")

        for p in coords[seg_end_index + 1 :]:
            o.write(f"G1 X{p[0]:.4f} Y{p[1]:.4f} F{feed_xy}\n")

    else:
        o.write(f"G1 Z{-depth:.4f} F{feed_z}\n")
        for x, y in coords[1:]:
            o.write(f"G1 X{x:.4f} Y{y:.4f} F{feed_xy}\n")

    o.write(f"G0 Z{get_safe_z():.3f}\n")


def write_geom_paths(o, geom, depth, bit):
    if geom is None or geom.is_empty:
        return

    ramp_len = _default_ramp_len(bit)
    start_xy = get_park_xy()
    lines = geom_to_ordered_lines(geom, start_xy=start_xy)

    for ls in lines:
        _write_line_as_gcode(o, ls, depth=float(depth), bit=bit, ramp_len=float(ramp_len))
