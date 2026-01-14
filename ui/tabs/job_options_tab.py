# ui/tabs/job_options_tab.py

from PySide6.QtWidgets import (
    QWidget,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
)
from PySide6.QtCore import Qt, Signal


class JobOptionsTab(QWidget):
    optionsChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state

        self.tabs_widget = QTabWidget()

        # Create pages
        self.page_basic = QWidget()
        self.page_drill = QWidget()
        self.page_toolpath = QWidget()
        self.page_safety = QWidget()

        self.tabs_widget.addTab(self.page_basic, "Basic")
        self.tabs_widget.addTab(self.page_drill, "Drilling planner")
        self.tabs_widget.addTab(self.page_toolpath, "Toolpath generation")
        self.tabs_widget.addTab(self.page_safety, "Machine safty")  # keep user's spelling

        # Layouts for each page
        basic_form = QFormLayout()
        drill_form = QFormLayout()
        toolpath_form = QFormLayout()
        safety_form = QFormLayout()

        self.page_basic.setLayout(basic_form)
        self.page_drill.setLayout(drill_form)
        self.page_toolpath.setLayout(toolpath_form)
        self.page_safety.setLayout(safety_form)

        # ----------------------------
        # Basic (default)
        # ----------------------------
        self.iso = QSpinBox()
        self.iso.setRange(1, 10)
        self.iso.setValue(state.iso_passes)
        self.iso.valueChanged.connect(self.set_passes)
        basic_form.addRow("Copper isolation passes", self.iso)

        self.pcb = QDoubleSpinBox()
        self.pcb.setRange(0.2, 10.0)
        self.pcb.setDecimals(2)
        self.pcb.setSingleStep(0.1)
        self.pcb.setValue(state.pcb_thickness)
        self.pcb.valueChanged.connect(self.set_pcb_thickness)
        basic_form.addRow("PCB thickness (mm)", self.pcb)

        self.cu = QDoubleSpinBox()
        self.cu.setRange(0.005, 0.2)
        self.cu.setDecimals(3)
        self.cu.setSingleStep(0.005)
        self.cu.setValue(state.copper_thickness)
        self.cu.valueChanged.connect(self.set_copper_thickness)
        basic_form.addRow("Copper thickness (mm)", self.cu)

        self.outline_tabs_cb = QCheckBox("Enable board outline tabs")
        self.outline_tabs_cb.setChecked(state.outline_tabs_enabled)
        self.outline_tabs_cb.stateChanged.connect(self.set_tabs)
        basic_form.addRow(self.outline_tabs_cb)

        # ----------------------------
        # Drilling planner
        # ----------------------------
        self.drill_control = QComboBox()
        self.drill_control.addItem("AUTO (select drills for you)", "auto")
        self.drill_control.addItem("MANUAL (you pick drills)", "manual")
        self._set_combo_by_data(self.drill_control, state.drill_control, default="auto")
        self.drill_control.currentIndexChanged.connect(self.set_drill_control)
        drill_form.addRow("Drill control", self.drill_control)

        self.max_drills = QSpinBox()
        self.max_drills.setRange(1, 12)
        self.max_drills.setValue(int(getattr(state, "max_drills", 3)))
        self.max_drills.valueChanged.connect(self.set_max_drills)
        drill_form.addRow("Max drills (AUTO / MANUAL)", self.max_drills)

        self.tol = QDoubleSpinBox()
        self.tol.setRange(0.0, 0.50)
        self.tol.setDecimals(3)
        self.tol.setSingleStep(0.01)
        self.tol.setValue(float(getattr(state, "hole_match_tol", 0.05)))
        self.tol.valueChanged.connect(self.set_tol)
        drill_form.addRow("Max drill match tolerance (mm)", self.tol)

        self.dedupe = QDoubleSpinBox()
        self.dedupe.setRange(0.0, 2.0)
        self.dedupe.setDecimals(3)
        self.dedupe.setSingleStep(0.01)
        self.dedupe.setValue(float(getattr(state, "hole_dedupe_tol", 0.10)))
        self.dedupe.valueChanged.connect(self.set_dedupe)
        drill_form.addRow("Hole de-duplication tolerance (mm)", self.dedupe)

        self.mill_over = QDoubleSpinBox()
        self.mill_over.setRange(0.10, 50.0)
        self.mill_over.setDecimals(3)
        self.mill_over.setSingleStep(0.10)
        self.mill_over.setValue(float(getattr(state, "mill_holes_over", 1.2)))
        self.mill_over.valueChanged.connect(self.set_mill_over)
        drill_form.addRow("Mill holes >= (mm)", self.mill_over)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        drill_form.addRow("Drill/router check", self.status)

        # ----------------------------
        # Toolpath generation
        # ----------------------------
        self.path_ordering = QCheckBox("Enable path ordering (reduce travel moves)")
        self.path_ordering.setChecked(bool(getattr(state, "path_ordering", True)))
        self.path_ordering.stateChanged.connect(self.set_path_ordering)
        toolpath_form.addRow(self.path_ordering)

        self.simplify = QDoubleSpinBox()
        self.simplify.setRange(0.0, 1.0)
        self.simplify.setDecimals(6)
        self.simplify.setSingleStep(0.0001)
        self.simplify.setValue(float(getattr(state, "geom_simplify_tol", 0.0005)))
        self.simplify.valueChanged.connect(self.set_geom_simplify_tol)
        toolpath_form.addRow("Geometry simplify tol (mm)", self.simplify)

        self.min_area = QDoubleSpinBox()
        self.min_area.setRange(0.0, 10.0)
        self.min_area.setDecimals(10)
        self.min_area.setSingleStep(0.00000001)
        self.min_area.setValue(float(getattr(state, "geom_min_area", 1e-8)))
        self.min_area.valueChanged.connect(self.set_geom_min_area)
        toolpath_form.addRow("Drop polygons smaller than (mm²)", self.min_area)

        self.min_len = QDoubleSpinBox()
        self.min_len.setRange(0.0, 10.0)
        self.min_len.setDecimals(6)
        self.min_len.setSingleStep(0.0001)
        self.min_len.setValue(float(getattr(state, "geom_min_length", 1e-5)))
        self.min_len.valueChanged.connect(self.set_geom_min_length)
        toolpath_form.addRow("Drop lines shorter than (mm)", self.min_len)

        self.ramp = QDoubleSpinBox()
        self.ramp.setRange(0.0, 50.0)
        self.ramp.setDecimals(3)
        self.ramp.setSingleStep(0.5)
        self.ramp.setValue(float(getattr(state, "ramp_len", 0.0)))
        self.ramp.valueChanged.connect(self.set_ramp_len)
        toolpath_form.addRow("Ramp-in length (mm) (0=off)", self.ramp)

        # ----------------------------
        # Machine safty
        # ----------------------------
        self.safe_z = QDoubleSpinBox()
        self.safe_z.setRange(0.0, 100.0)
        self.safe_z.setDecimals(3)
        self.safe_z.setSingleStep(1.0)
        self.safe_z.setValue(float(getattr(state, "safe_z", 5.0)))
        self.safe_z.valueChanged.connect(self.set_safe_z)
        safety_form.addRow("Safe Z (mm) (between cuts)", self.safe_z)

        self.travel_z = QDoubleSpinBox()
        self.travel_z.setRange(0.0, 200.0)
        self.travel_z.setDecimals(3)
        self.travel_z.setSingleStep(1.0)
        self.travel_z.setValue(float(getattr(state, "travel_z", 10.0)))
        self.travel_z.valueChanged.connect(self.set_travel_z)
        safety_form.addRow("Travel Z (mm) (moves/toolchange)", self.travel_z)

        self.toolchange_z = QDoubleSpinBox()
        self.toolchange_z.setRange(0.0, 300.0)
        self.toolchange_z.setDecimals(3)
        self.toolchange_z.setSingleStep(1.0)
        self.toolchange_z.setValue(float(getattr(state, "toolchange_z", 30.0)))
        self.toolchange_z.valueChanged.connect(self.set_toolchange_z)
        safety_form.addRow("Toolchange Z (mm)", self.toolchange_z)

        self.park_x = QDoubleSpinBox()
        self.park_x.setRange(-10000.0, 10000.0)
        self.park_x.setDecimals(3)
        self.park_x.setSingleStep(1.0)
        self.park_x.setValue(float(getattr(state, "park_x", 0.0)))
        self.park_x.valueChanged.connect(self.set_park_x)
        safety_form.addRow("Park X (mm)", self.park_x)

        self.park_y = QDoubleSpinBox()
        self.park_y.setRange(-10000.0, 10000.0)
        self.park_y.setDecimals(3)
        self.park_y.setSingleStep(1.0)
        self.park_y.setValue(float(getattr(state, "park_y", 0.0)))
        self.park_y.valueChanged.connect(self.set_park_y)
        safety_form.addRow("Park Y (mm)", self.park_y)

        self.warmup = QDoubleSpinBox()
        self.warmup.setRange(0.0, 60.0)
        self.warmup.setDecimals(2)
        self.warmup.setSingleStep(0.5)
        self.warmup.setValue(float(getattr(state, "spindle_warmup_s", 0.0)))
        self.warmup.valueChanged.connect(self.set_spindle_warmup_s)
        safety_form.addRow("Spindle warmup dwell (s)", self.warmup)

        self.probe_on = QCheckBox("Run probe routine in header (optional)")
        self.probe_on.setChecked(bool(getattr(state, "probe_on_start", False)))
        self.probe_on.stateChanged.connect(self.set_probe_on_start)
        safety_form.addRow(self.probe_on)

        self.probe_gcode = QPlainTextEdit()
        self.probe_gcode.setPlaceholderText(
            "One GRBL command per line, e.g.\nG38.2 Z-10 F50\nG92 Z0\nG0 Z5"
        )
        self.probe_gcode.setPlainText(getattr(state, "probe_gcode", "") or "")
        self.probe_gcode.setFixedHeight(110)
        self.probe_gcode.textChanged.connect(self.set_probe_gcode)
        safety_form.addRow("Probe gcode", self.probe_gcode)

        self._apply_probe_enabled()

        # Root layout (single widget: the tabs)
        root = QFormLayout()
        root.addRow(self.tabs_widget)
        self.setLayout(root)

    def _set_combo_by_data(self, combo: QComboBox, data_value: str, default: str):
        target = data_value or default
        for i in range(combo.count()):
            if combo.itemData(i) == target:
                combo.setCurrentIndex(i)
                return
        for i in range(combo.count()):
            if combo.itemData(i) == default:
                combo.setCurrentIndex(i)
                return

    def _apply_probe_enabled(self):
        enabled = self.probe_on.isChecked()
        self.probe_gcode.setEnabled(enabled)

    def update_status(self, level: str, text: str):
        level = (level or "info").lower()
        if level == "ok":
            self.status.setStyleSheet("color: #000000;")
            prefix = "✅ "
        elif level == "error":
            self.status.setStyleSheet("color: #ff4444;")
            prefix = "❌ "
        elif level == "warn":
            self.status.setStyleSheet("color: #ffaa00;")
            prefix = "⚠️ "
        else:
            self.status.setStyleSheet("color: gray;")
            prefix = ""
        self.status.setText((prefix + (text or "")).strip())

    # ---- State setters ----
    def set_passes(self, v):
        self.state.iso_passes = int(v)
        self.optionsChanged.emit()

    def set_pcb_thickness(self, v):
        self.state.pcb_thickness = float(v)
        self.optionsChanged.emit()

    def set_copper_thickness(self, v):
        self.state.copper_thickness = float(v)
        self.optionsChanged.emit()

    def set_tabs(self, state):
        self.state.outline_tabs_enabled = bool(state)
        self.optionsChanged.emit()

    def set_drill_control(self, _=None):
        mode = self.drill_control.currentData()
        self.state.set_drill_control(mode)
        self.optionsChanged.emit()

    def set_max_drills(self, v):
        self.state.set_max_drills(int(v))
        self.optionsChanged.emit()

    def set_tol(self, v):
        self.state.set_hole_match_tol(float(v))
        self.optionsChanged.emit()

    def set_dedupe(self, v):
        self.state.set_hole_dedupe_tol(float(v))
        self.optionsChanged.emit()

    def set_mill_over(self, v):
        self.state.set_mill_holes_over(float(v))
        self.optionsChanged.emit()

    # ---- Toolpath setters ----
    def set_path_ordering(self, state):
        self.state.set_path_ordering(bool(state))
        self.optionsChanged.emit()

    def set_geom_simplify_tol(self, v):
        self.state.set_geom_simplify_tol(float(v))
        self.optionsChanged.emit()

    def set_geom_min_area(self, v):
        self.state.set_geom_min_area(float(v))
        self.optionsChanged.emit()

    def set_geom_min_length(self, v):
        self.state.set_geom_min_length(float(v))
        self.optionsChanged.emit()

    def set_ramp_len(self, v):
        self.state.set_ramp_len(float(v))
        self.optionsChanged.emit()

    # ---- Safety setters ----
    def set_safe_z(self, v):
        self.state.set_safe_z(float(v))
        self.optionsChanged.emit()

    def set_travel_z(self, v):
        self.state.set_travel_z(float(v))
        self.optionsChanged.emit()

    def set_toolchange_z(self, v):
        self.state.set_toolchange_z(float(v))
        self.optionsChanged.emit()

    def set_park_x(self, v):
        self.state.set_park_x(float(v))
        self.optionsChanged.emit()

    def set_park_y(self, v):
        self.state.set_park_y(float(v))
        self.optionsChanged.emit()

    def set_spindle_warmup_s(self, v):
        self.state.set_spindle_warmup_s(float(v))
        self.optionsChanged.emit()

    def set_probe_on_start(self, state):
        self.state.set_probe_on_start(bool(state))
        self._apply_probe_enabled()
        self.optionsChanged.emit()

    def set_probe_gcode(self):
        self.state.set_probe_gcode(self.probe_gcode.toPlainText())
        self.optionsChanged.emit()
