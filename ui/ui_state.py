# ui/ui_state.py

import os
from bitlib import load_bits, load_settings


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


def _normalize_drill_control(m: str) -> str:
    m = (m or "").strip().lower()
    if m in ("auto", "automatic"):
        return "auto"
    if m in ("manual", "man"):
        return "manual"
    return "auto"


class UIState:
    def __init__(self):
        self.bits = load_bits()
        self.settings = load_settings()

        self.prefix = None
        self.gerber_dir = None
        self.output_dir = os.getcwd()

        self.selected_ops = []
        self.combined = True

        # ----------------------------
        # Job options (basic)
        # ----------------------------
        self.iso_passes = self.settings.getint("copper_isolation", "passes", fallback=1)
        self.pcb_thickness = self.settings.getfloat("job", "pcb_thickness", fallback=1.6)
        self.copper_thickness = self.settings.getfloat("job", "copper_thickness", fallback=0.035)
        self.outline_tabs_enabled = self.settings.getboolean("job", "outline_tabs_enabled", fallback=False)

        # ----------------------------
        # Drill planning
        # ----------------------------
        self.drill_control = _normalize_drill_control(
            self.settings.get("job", "drill_control", fallback="auto")
        )
        self.max_drills = self.settings.getint("job", "max_drills", fallback=3)
        self.hole_match_tol = self.settings.getfloat("job", "hole_match_tol", fallback=0.05)
        self.hole_dedupe_tol = self.settings.getfloat("job", "hole_dedupe_tol", fallback=0.10)
        self.mill_holes_over = self.settings.getfloat("job", "mill_holes_over", fallback=1.2)

        # Bits tab filtering
        self.show_all_bits = self.settings.getboolean("job", "show_all_bits", fallback=False)

        # ----------------------------
        # Step 8: Advanced CAM knobs
        # ----------------------------
        self.path_ordering = self.settings.getboolean("job", "path_ordering", fallback=True)
        self.geom_simplify_tol = self.settings.getfloat("job", "geom_simplify_tol", fallback=0.0005)
        self.geom_min_area = self.settings.getfloat("job", "geom_min_area", fallback=1e-8)
        self.geom_min_length = self.settings.getfloat("job", "geom_min_length", fallback=1e-5)
        self.ramp_len = self.settings.getfloat("job", "ramp_len", fallback=0.0)

        # Machine / safety defaults (Step 7, now UI-exposed)
        self.safe_z = self.settings.getfloat("job", "safe_z", fallback=5.0)
        self.travel_z = self.settings.getfloat("job", "travel_z", fallback=10.0)
        self.toolchange_z = self.settings.getfloat("job", "toolchange_z", fallback=30.0)
        self.park_x = self.settings.getfloat("job", "park_x", fallback=0.0)
        self.park_y = self.settings.getfloat("job", "park_y", fallback=0.0)
        self.spindle_warmup_s = self.settings.getfloat("job", "spindle_warmup_s", fallback=0.0)

        self.probe_on_start = self.settings.getboolean("job", "probe_on_start", fallback=False)
        self.probe_gcode = self.settings.get("job", "probe_gcode", fallback="") or ""

        # Preflight / UI display
        self.preflight_text = ""
        self.preflight_level = "info"  # ok / warn / error / info
        self.planned_drill_names = []

        # Output naming
        self.file_prefix = _normalize_file_prefix(
            self.settings.get("job", "file_prefix", fallback="")
        )

    # ----------------------------
    # Bits + settings reload helpers
    # ----------------------------

    def reload_bits(self):
        self.bits = load_bits()

    # ----------------------------
    # Small setters used by tabs
    # ----------------------------

    def set_file_prefix(self, p: str):
        self.file_prefix = _normalize_file_prefix(p)

    def set_drill_control(self, m: str):
        self.drill_control = _normalize_drill_control(m)

    def set_max_drills(self, v: int):
        try:
            self.max_drills = max(1, int(v))
        except Exception:
            self.max_drills = 3

    def set_hole_match_tol(self, v: float):
        try:
            self.hole_match_tol = max(0.0, float(v))
        except Exception:
            self.hole_match_tol = 0.05

    def set_hole_dedupe_tol(self, v: float):
        try:
            self.hole_dedupe_tol = max(0.0, float(v))
        except Exception:
            self.hole_dedupe_tol = 0.10

    def set_mill_holes_over(self, v: float):
        try:
            self.mill_holes_over = max(0.1, float(v))
        except Exception:
            self.mill_holes_over = 1.2

    def set_show_all_bits(self, v: bool):
        self.show_all_bits = bool(v)

    # ---- Step 8 setters ----

    def set_path_ordering(self, v: bool):
        self.path_ordering = bool(v)

    def set_geom_simplify_tol(self, v: float):
        try:
            self.geom_simplify_tol = max(0.0, float(v))
        except Exception:
            self.geom_simplify_tol = 0.0005

    def set_geom_min_area(self, v: float):
        try:
            self.geom_min_area = max(0.0, float(v))
        except Exception:
            self.geom_min_area = 1e-8

    def set_geom_min_length(self, v: float):
        try:
            self.geom_min_length = max(0.0, float(v))
        except Exception:
            self.geom_min_length = 1e-5

    def set_ramp_len(self, v: float):
        try:
            self.ramp_len = max(0.0, float(v))
        except Exception:
            self.ramp_len = 0.0

    def set_safe_z(self, v: float):
        try:
            self.safe_z = max(0.0, float(v))
        except Exception:
            self.safe_z = 5.0

    def set_travel_z(self, v: float):
        try:
            self.travel_z = max(0.0, float(v))
        except Exception:
            self.travel_z = 10.0

    def set_toolchange_z(self, v: float):
        try:
            self.toolchange_z = max(0.0, float(v))
        except Exception:
            self.toolchange_z = 30.0

    def set_park_x(self, v: float):
        try:
            self.park_x = float(v)
        except Exception:
            self.park_x = 0.0

    def set_park_y(self, v: float):
        try:
            self.park_y = float(v)
        except Exception:
            self.park_y = 0.0

    def set_spindle_warmup_s(self, v: float):
        try:
            self.spindle_warmup_s = max(0.0, float(v))
        except Exception:
            self.spindle_warmup_s = 0.0

    def set_probe_on_start(self, v: bool):
        self.probe_on_start = bool(v)

    def set_probe_gcode(self, s: str):
        self.probe_gcode = s or ""
