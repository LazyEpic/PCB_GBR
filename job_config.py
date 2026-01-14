# job_config.py
#
# Job/settings access + output naming.
# Single responsibility: read job_settings.ini and expose normalized values.

import os
import configparser
from typing import Tuple

# Defaults (can be overridden in job_settings.ini [job])
SAFE_Z = 5.0
TRAVEL_Z = 10.0
TOOLCHANGE_Z = 30.0
PARK_X = 0.0
PARK_Y = 0.0


def _normalize_file_prefix(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return ""
    safe = []
    for ch in p:
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
    p = "".join(safe)
    if not p:
        return ""
    if not (p.endswith("_") or p.endswith("-")):
        p += "_"
    return p


def _job_settings_paths():
    paths = []

    env = os.environ.get("JOB_SETTINGS_INI", "").strip()
    if env:
        paths.append(env)

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        paths.append(os.path.join(here, "job_settings.ini"))
    except Exception:
        pass

    paths.append(os.path.join(os.getcwd(), "job_settings.ini"))

    out = []
    seen = set()
    for p in paths:
        p = os.path.abspath(p)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _job_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    for p in _job_settings_paths():
        try:
            if os.path.exists(p):
                cfg.read(p)
                if cfg.sections():
                    break
        except Exception:
            continue
    return cfg


def job_getfloat(section: str, key: str, default: float) -> float:
    try:
        cfg = _job_cfg()
        return float(cfg.getfloat(section, key, fallback=default))
    except Exception:
        return float(default)


def job_getbool(section: str, key: str, default: bool) -> bool:
    try:
        cfg = _job_cfg()
        return cfg.getboolean(section, key, fallback=default)
    except Exception:
        return bool(default)


def job_getstr(section: str, key: str, default: str = "") -> str:
    try:
        cfg = _job_cfg()
        v = cfg.get(section, key, fallback=default)
        return (v or default).strip()
    except Exception:
        return (default or "").strip()


def job_file_prefix() -> str:
    try:
        cfg = _job_cfg()
        return _normalize_file_prefix(cfg.get("job", "file_prefix", fallback=""))
    except Exception:
        return ""


def out_nc(name: str) -> str:
    return f"{job_file_prefix()}{name}"


# ---- Machine/job Z + park settings ----

def get_safe_z() -> float:
    return job_getfloat("job", "safe_z", SAFE_Z)


def get_travel_z() -> float:
    return job_getfloat("job", "travel_z", TRAVEL_Z)


def get_toolchange_z() -> float:
    return job_getfloat("job", "toolchange_z", TOOLCHANGE_Z)


def get_park_xy() -> Tuple[float, float]:
    x = job_getfloat("job", "park_x", PARK_X)
    y = job_getfloat("job", "park_y", PARK_Y)
    return x, y


def get_spindle_warmup_s() -> float:
    v = job_getfloat("job", "spindle_warmup_s", 0.0)
    return max(0.0, float(v))


def get_probe_on_start() -> bool:
    return job_getbool("job", "probe_on_start", False)


def get_probe_gcode() -> str:
    return job_getstr("job", "probe_gcode", "")
