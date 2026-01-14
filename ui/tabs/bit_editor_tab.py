# ui/tabs/bit_editor_tab.py

from PySide6.QtWidgets import (
    QWidget,
    QListWidget,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
)
from PySide6.QtCore import Signal, Qt


class BitEditorTab(QWidget):
    bitsFileChanged = Signal()

    # Step 8: expose optional CAM helpers for bits.ini
    FIELDS = [
        "type",
        "diameter",
        "angle",
        "flute_length",
        "feed_xy",
        "feed_z",
        "rpm",
        "stepdown",
        "ramp_len",
    ]

    def __init__(self, state):
        super().__init__()
        self.state = state

        layout = QHBoxLayout()

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.load_bit)

        self.name_edit = QLineEdit()

        self.fields = {}
        form = QFormLayout()

        form.addRow("name", self.name_edit)

        for key in self.FIELDS:
            edit = QLineEdit()
            self.fields[key] = edit
            form.addRow(key, edit)

        save = QPushButton("Save / Update")
        add = QPushButton("Add New (Copy Selected)")
        delete = QPushButton("Delete")

        save.clicked.connect(self.save_bit)
        add.clicked.connect(self.add_bit)
        delete.clicked.connect(self.delete_bit)

        form.addRow(save)
        form.addRow(add)
        form.addRow(delete)

        layout.addWidget(self.list, 1)
        layout.addLayout(form, 2)
        self.setLayout(layout)

        self.refresh()

    def _unique_name(self, base: str) -> str:
        base = (base or "").strip()
        if not base:
            base = "new_bit"
        if not self.state.bits.has_section(base):
            return base
        i = 1
        while True:
            cand = f"{base}_{i}"
            if not self.state.bits.has_section(cand):
                return cand
            i += 1

    def refresh(self, select_name: str = None):
        cur = select_name
        if cur is None and self.list.currentItem():
            cur = self.list.currentItem().text()

        self.list.blockSignals(True)
        self.list.clear()

        names = list(self.state.bits.sections())
        names.sort(key=lambda s: s.lower())
        for name in names:
            self.list.addItem(name)

        self.list.blockSignals(False)

        if cur and cur in names:
            items = self.list.findItems(cur, Qt.MatchExactly)
            if items:
                self.list.setCurrentItem(items[0])
                return

        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def load_bit(self, item):
        if not item:
            self.name_edit.setText("")
            for field in self.fields.values():
                field.setText("")
            return

        name = item.text()
        self.name_edit.setText(name)
        for k, field in self.fields.items():
            field.setText(self.state.bits.get(name, k, fallback=""))

    def _write_bits_ini(self):
        with open("bits.ini", "w") as f:
            self.state.bits.write(f)
        self.state.reload_bits()
        self.bitsFileChanged.emit()

    def save_bit(self):
        item = self.list.currentItem()
        if not item:
            return

        old_name = item.text()
        desired = (self.name_edit.text() or "").strip()
        if not desired:
            QMessageBox.warning(self, "Invalid name", "Name cannot be empty")
            return

        # Rename if needed
        new_name = old_name
        if desired != old_name:
            if self.state.bits.has_section(desired):
                desired = self._unique_name(desired)
            new_name = desired

            self.state.bits.add_section(new_name)
            for k in self.FIELDS:
                self.state.bits.set(new_name, k, self.state.bits.get(old_name, k, fallback=""))
            self.state.bits.remove_section(old_name)

        # Update fields
        for k, field in self.fields.items():
            self.state.bits.set(new_name, k, field.text())

        self._write_bits_ini()
        self.refresh(select_name=new_name)

    def add_bit(self):
        # Copy currently selected bit (if any)
        src_item = self.list.currentItem()
        if src_item:
            src = src_item.text()
            new_name = self._unique_name(src)
            self.state.bits.add_section(new_name)
            for k in self.FIELDS:
                self.state.bits.set(new_name, k, self.state.bits.get(src, k, fallback=""))
        else:
            new_name = self._unique_name("new_bit")
            self.state.bits.add_section(new_name)
            for k in self.FIELDS:
                self.state.bits.set(new_name, k, "")

        self._write_bits_ini()
        self.refresh(select_name=new_name)

    def delete_bit(self):
        item = self.list.currentItem()
        if not item:
            return
        name = item.text()

        if QMessageBox.question(self, "Delete", f"Delete bit '{name}'?") != QMessageBox.Yes:
            return

        self.state.bits.remove_section(name)
        self._write_bits_ini()
        self.refresh()
