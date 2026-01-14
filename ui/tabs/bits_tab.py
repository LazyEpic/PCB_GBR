# ui/tabs/bits_tab.py

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
)
from PySide6.QtCore import Signal, Qt
from bitlib import save_settings, bit_dict


OPS = [
    "copper_isolation",
    "soldermask_clear",
    "drilling",
    "board_outline",
    "silkscreen",
]


def _type_tag(bit_type: str) -> str:
    t = (bit_type or "").strip().lower()
    if "drill" in t:
        return "drill"
    if "v" in t or "engrave" in t or "conic" in t:
        return "vbit"
    if "flat" in t or "end" in t or "mill" in t or "router" in t:
        return "mill"
    return "unknown"


def _is_reasonable_for_op(op: str, btype: str) -> bool:
    tag = _type_tag(btype)
    if op == "drilling":
        return tag == "drill"
    if op in ("board_outline", "soldermask_clear"):
        return tag == "mill"
    if op in ("copper_isolation", "silkscreen"):
        return tag in ("vbit", "mill")  # allow small endmills for silk if user wants
    return True


class BitsTab(QWidget):
    bitsChanged = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state

        self.boxes = {}          # op -> QComboBox
        self.drill_list = None   # QListWidget
        self.drill_info = None   # QLabel

        layout = QVBoxLayout()

        # Filtering toggle
        self.show_all = QCheckBox("Show ALL bits for each operation (no filtering)")
        self.show_all.setChecked(bool(getattr(state, "show_all_bits", False)))
        self.show_all.stateChanged.connect(self._toggle_show_all)
        layout.addWidget(self.show_all)

        # Per-operation selectors
        for op in OPS:
            layout.addWidget(QLabel(op.replace("_", " ").title()))

            if op == "drilling":
                self.drill_info = QLabel("")
                self.drill_info.setStyleSheet("color: gray;")
                layout.addWidget(self.drill_info)

                self.drill_list = QListWidget()
                self.drill_list.itemChanged.connect(self._drill_item_changed)
                layout.addWidget(self.drill_list)
            else:
                box = QComboBox()
                box.currentTextChanged.connect(lambda v, o=op: self.set_bit(o, v))
                self.boxes[op] = box
                layout.addWidget(box)

        # Save defaults
        save = QPushButton("Save defaults")
        save.clicked.connect(self._save_defaults)
        layout.addWidget(save)

        self.setLayout(layout)

        self.refresh_bits_list()
        self._apply_drill_mode_ui()

    def _toggle_show_all(self, _=None):
        self.state.set_show_all_bits(self.show_all.isChecked())
        self.refresh_bits_list()
        self.bitsChanged.emit()

    def _save_defaults(self):
        save_settings(self.state.settings)
        self.bitsChanged.emit()

    def _filtered_names_for_op(self, op: str):
        names = list(self.state.bits.sections())
        if self.show_all.isChecked():
            return names

        out = []
        for n in names:
            try:
                b = bit_dict(self.state.bits, n)
                if _is_reasonable_for_op(op, b.get("type", "")):
                    out.append(n)
            except Exception:
                continue
        return out

    def refresh_bits_list(self):
        # Non-drill ops: combo boxes
        for op, box in self.boxes.items():
            names = self._filtered_names_for_op(op)

            current = box.currentText().strip()
            box.blockSignals(True)
            box.clear()
            box.addItems(names)

            desired = self.state.settings.get(op, "bit", fallback=current) or current
            if desired in names:
                box.setCurrentText(desired)
            elif names:
                box.setCurrentIndex(0)
                if not self.state.settings.has_section(op):
                    self.state.settings.add_section(op)
                self.state.settings.set(op, "bit", names[0])

            box.blockSignals(False)

        # Drilling: multi-select list
        if self.drill_list is not None:
            names = self._filtered_names_for_op("drilling")

            # Load selected drill names from settings: [drilling] bits = a,b,c
            sel = self.state.settings.get("drilling", "bits", fallback="").strip()
            selected = [s.strip() for s in sel.split(",") if s.strip()]

            self.drill_list.blockSignals(True)
            self.drill_list.clear()

            for n in names:
                item = QListWidgetItem(n)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if n in selected else Qt.Unchecked)
                self.drill_list.addItem(item)

            self.drill_list.blockSignals(False)

        self._apply_drill_mode_ui()

    def _apply_drill_mode_ui(self):
        mode = getattr(self.state, "drill_control", "auto")
        max_n = int(getattr(self.state, "max_drills", 3))

        if self.drill_info is None or self.drill_list is None:
            return

        if mode == "auto":
            self.drill_info.setText("AUTO SELECTED (Job Options → Drill control)")
            self.drill_list.setEnabled(False)
        else:
            self.drill_info.setText(f"Select up to {max_n} drill bits (MANUAL mode)")
            self.drill_list.setEnabled(True)

    def set_drill_auto_plan(self, planned_names):
        # Called by MainWindow to show what AUTO picked, without changing manual selections.
        if self.drill_info is None:
            return
        if getattr(self.state, "drill_control", "auto") != "auto":
            return
        if planned_names:
            self.drill_info.setText("AUTO SELECTED: " + ", ".join(planned_names))
        else:
            self.drill_info.setText("AUTO SELECTED (no drills available / no drills needed)")

    def _drill_item_changed(self, _item):
        if getattr(self.state, "drill_control", "auto") == "auto":
            return

        max_n = int(getattr(self.state, "max_drills", 3))

        selected = []
        for i in range(self.drill_list.count()):
            it = self.drill_list.item(i)
            if it.checkState() == Qt.Checked:
                selected.append(it.text())

        # Enforce max selection
        if len(selected) > max_n:
            # Uncheck the last toggled item by trimming extras from the end
            # (Qt doesn't tell us reliably which one is last across signals)
            # So we uncheck from bottom until within limit.
            self.drill_list.blockSignals(True)
            for i in range(self.drill_list.count() - 1, -1, -1):
                it = self.drill_list.item(i)
                if it.checkState() == Qt.Checked and len(selected) > max_n:
                    it.setCheckState(Qt.Unchecked)
                    selected.remove(it.text())
            self.drill_list.blockSignals(False)

        if not self.state.settings.has_section("drilling"):
            self.state.settings.add_section("drilling")
        self.state.settings.set("drilling", "bits", ",".join(selected))

        self.bitsChanged.emit()

    def set_bit(self, op, bit):
        if not self.state.settings.has_section(op):
            self.state.settings.add_section(op)
        self.state.settings.set(op, "bit", bit)
        self.bitsChanged.emit()
