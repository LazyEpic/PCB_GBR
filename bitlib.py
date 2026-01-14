# bitlib.py
# Robust numeric parsing for bits.ini and convenience helpers.
#
# Step 8 additions:
# - expose extra optional fields in bits.ini: stepdown, ramp_len
#   (safe defaults if missing)

import configparser
import os

BITS_FILE = os.environ.get("BITS_INI", "bits.ini")
SETTINGS_FILE = os.environ.get("JOB_SETTINGS_INI", "job_settings.ini")


def _read_cfg(path: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    # Try as-is
    cfg.read(path)
    if cfg.sections():
        return cfg

    # Fallback to directory of this file
    here = os.path.dirname(os.path.abspath(__file__))
    alt = os.path.join(here, os.path.basename(path))
    if os.path.exists(alt):
        cfg.read(alt)
    return cfg


def load_bits() -> configparser.ConfigParser:
    return _read_cfg(BITS_FILE)


def load_settings() -> configparser.ConfigParser:
    return _read_cfg(SETTINGS_FILE)


def save_settings(cfg: configparser.ConfigParser) -> None:
    # Save next to the running working directory, consistent with existing behavior
    with open(SETTINGS_FILE, "w") as f:
        cfg.write(f)


def _num(val, default=0.0) -> float:
    """Safe numeric conversion:
    - None / "" / invalid -> default
    """
    if val is None:
        return float(default)
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "":
        return float(default)
    try:
        return float(s)
    except Exception:
        return float(default)


def bit_dict(bits_cfg: configparser.ConfigParser, name: str) -> dict:
    """Convert a bit section into a numeric-safe dict."""
    if not bits_cfg.has_section(name):
        raise KeyError(f"Bit '{name}' not found")

    b = bits_cfg[name]

    return {
        "name": name,
        "type": b.get("type", "unknown"),
        "diameter": _num(b.get("diameter"), 0.0),
        "angle": _num(b.get("angle"), 0.0),
        "flute_length": _num(b.get("flute_length"), 0.0),
        "feed_xy": _num(b.get("feed_xy"), 200.0),
        "feed_z": _num(b.get("feed_z"), 80.0),
        "rpm": int(_num(b.get("rpm"), 12000.0)),
        # Optional CAM helpers
        "stepdown": _num(b.get("stepdown"), 0.0),
        "ramp_len": _num(b.get("ramp_len"), 0.0),
    }


def choose_bit_filtered(bits: configparser.ConfigParser, current: str, op_key: str) -> str:
    """CLI helper (kept for compatibility)."""
    names = bits.sections()
    if not names:
        raise RuntimeError("No bits defined")

    print("\nAvailable bits:")
    for i, n in enumerate(names):
        print(f"[{i}] {n}")

    if current in names:
        print(f"Current default: {current}")

    try:
        sel = int(input("Select bit: ").strip())
        return names[sel]
    except Exception:
        return current or names[0]
