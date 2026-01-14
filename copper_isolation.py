# copper_isolation.py

import math
import configparser

from shapely.ops import unary_union

from common_gerber import (
    load_copper,
    normalize_to_ref,
    ensure_header,
    toolchange_sequence,
    write_geom_paths,
    end_sequence,
    out_nc,
    cleanup_geometry,
)

DEFAULT_COPPER_THICKNESS = 0.035  # mm
EXTRA_CLEARANCE = 0.01            # mm


def load_copper_thickness():
    cfg = configparser.ConfigParser()
    cfg.read("job_settings.ini")
    try:
        return cfg.getfloat("job", "copper_thickness")
    except Exception:
        return DEFAULT_COPPER_THICKNESS


def compute_isolation_depth(bit, copper_thickness):
    # V-bit: depth depends on angle & effective width (bit["diameter"] is treated as target cut width)
    if bit.get("angle", 0) > 0:
        angle_rad = math.radians(bit["angle"] / 2.0)
        depth = (bit["diameter"] / 2.0) / math.tan(angle_rad)
        return min(depth, copper_thickness + EXTRA_CLEARANCE)
    return copper_thickness + EXTRA_CLEARANCE


def run_copper(bit, combined, prefix, passes=1):
    copper_raw = load_copper(prefix + "-TopLayer.gbr")
    copper_ref = normalize_to_ref(copper_raw, copper_raw)

    tool_r = bit["diameter"] / 2.0
    copper_thickness = load_copper_thickness()
    depth = compute_isolation_depth(bit, copper_thickness)

    # Generate per-pass boundaries
    paths = []
    for i in range(1, int(passes) + 1):
        off = tool_r * i
        p = copper_ref.buffer(off).boundary
        if p is not None and not p.is_empty:
            paths.append(p)

    if not paths:
        print("[COPPER] No isolation geometry generated")
        return

    # Step 4: cleanup union to remove tiny slivers before writing
    unioned = unary_union(paths)
    unioned = cleanup_geometry(unioned)

    out = out_nc("all.nc") if combined else out_nc("top_copper_isolation.nc")
    ensure_header(out)

    with open(out, "a") as g:
        toolchange_sequence(g, bit, f"Copper isolation ({passes} pass{'es' if passes != 1 else ''})")
        write_geom_paths(g, unioned, depth, bit)
        end_sequence(g, end_program=not combined)

    print(f"[COPPER] Isolation generated ({passes} pass{'es' if passes > 1 else ''}), depth {depth:.3f} mm")
