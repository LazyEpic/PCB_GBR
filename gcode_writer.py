# gcode_writer.py
#
# G-code writing + GRBL job policy (header/toolchange/end).
# Depends only on job_config.

import os

from job_config import (
    get_travel_z,
    get_toolchange_z,
    get_park_xy,
    get_spindle_warmup_s,
    get_probe_on_start,
    get_probe_gcode,
)


def write_header(
    o,
    *,
    job_name: str = "",
    units: str = "mm",
    absolute: bool = True,
):
    o.write("; ----------------------------\n")
    o.write("; CNC_PCB job\n")
    if job_name:
        o.write(f"; Job: {job_name}\n")
    o.write("; Units: mm\n")
    o.write("; ----------------------------\n")

    o.write("G21\n" if units == "mm" else "G20\n")
    o.write("G90\n" if absolute else "G91\n")
    o.write("G17\n")
    o.write("G94\n")
    o.write("G54\n")
    o.write("G92.1\n")

    o.write(f"G0 Z{get_travel_z():.3f}\n")

    if get_probe_on_start():
        pg = get_probe_gcode()
        if pg:
            o.write("; Probe on start (user-provided)\n")
            for ln in pg.splitlines():
                ln = ln.strip()
                if ln:
                    o.write(f"{ln}\n")
            o.write(f"G0 Z{get_travel_z():.3f}\n")
        else:
            o.write("; Probe on start requested, but probe_gcode is empty.\n")
            o.write("M0 ; Run your probe routine now, then resume\n")


def ensure_header(fn: str, *, job_name: str = ""):
    if (not os.path.exists(fn)) or os.path.getsize(fn) == 0:
        with open(fn, "w") as g:
            write_header(g, job_name=job_name, units="mm", absolute=True)


def toolchange_sequence(o, bit, message: str):
    tc_z = get_toolchange_z()
    px, py = get_park_xy()

    o.write(f"\nG0 Z{tc_z:.3f}\n")
    o.write("M5\n")
    o.write(f"G0 X{px:.3f} Y{py:.3f}\n")
    o.write(f"; {message}\n")
    o.write("M0\n")

    rpm = int(bit.get("rpm", 0))
    if rpm > 0:
        o.write(f"S{rpm} M3\n")
    else:
        o.write("M3\n")

    warm = get_spindle_warmup_s()
    if warm > 0:
        o.write(f"G4 P{warm:.3f}\n")

    o.write(f"G0 Z{get_travel_z():.3f}\n")


def end_sequence(o, end_program: bool):
    px, py = get_park_xy()
    o.write(f"\nG0 Z{get_travel_z():.3f}\n")
    o.write("M5\n")
    o.write(f"G0 X{px:.3f} Y{py:.3f}\n")
    if end_program:
        o.write("M2\n")
