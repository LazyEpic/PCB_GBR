# common_gerber.py
#
# Compatibility façade.
# Keeps old imports working while the implementation is split into:
# - job_config.py
# - geom_utils.py
# - gerber_parser.py
# - gcode_writer.py

from job_config import (
    SAFE_Z,
    TRAVEL_Z,
    TOOLCHANGE_Z,
    PARK_X,
    PARK_Y,
    job_file_prefix,
    out_nc,
    get_safe_z,
    get_travel_z,
    get_toolchange_z,
    get_park_xy,
    get_spindle_warmup_s,
    get_probe_on_start,
    get_probe_gcode,
    job_getfloat,
    job_getbool,
    job_getstr,
)

from geom_utils import (
    cleanup_geometry,
    order_lines_nearest,
    geom_to_ordered_lines,
    write_geom_paths,
)

from gerber_parser import (
    GerberParseError,
    GerberFull,
    parse_gerber_full,
    parse_gerber,
    pad_from_ap,
    load_copper,
    load_pads,
    load_tracks,
)

from gcode_writer import (
    write_header,
    ensure_header,
    toolchange_sequence,
    end_sequence,
)

from shapely.affinity import translate


def normalize_to_ref(g, ref):
    minx, miny, _, _ = ref.bounds
    return translate(g, -minx, -miny)
