# files_tab.py
# Makes output behavior predictable:
# - If the user hasn't explicitly chosen an output directory, default it to the selected gerber_dir
#   (prevents "no output" confusion when output_dir was some other working directory)

import os
import glob
import zipfile
import shutil

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QFileDialog,
    QCheckBox,
    QMessageBox,
    QLineEdit,
)
from PySide6.QtCore import Qt, Signal


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


class FilesTab(QWidget):
    previewRequested = Signal()

    def __init__(self, state):
        super().__init__()
        self.state = state

        # Tracks whether user explicitly set output dir
        if not hasattr(self.state, "output_dir_user_set"):
            self.state.output_dir_user_set = False

        layout = QVBoxLayout()

        self.label = QLabel("No Gerber/ZIP selected")

        pick = QPushButton("Select Gerber file or ZIP")
        pick.clicked.connect(self.pick_gerber_or_zip)

        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QListWidget.NoSelection)
        self.layer_list.itemChanged.connect(self.on_layer_toggle)

        self.combined = QCheckBox("Single G-code file with tool changes")
        self.combined.setChecked(True)
        self.combined.stateChanged.connect(self.set_combined)

        # ---- Output file prefix ----
        prefix_row = QHBoxLayout()
        prefix_row.setContentsMargins(0, 0, 0, 0)

        prefix_row.addWidget(QLabel("NC filename prefix:"))

        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("e.g. my_board_")
        self.prefix_edit.setText(getattr(self.state, "file_prefix", "") or "")
        self.prefix_edit.textChanged.connect(self.on_prefix_changed)
        prefix_row.addWidget(self.prefix_edit, 1)

        self.prefix_hint = QLabel("")
        self.prefix_hint.setStyleSheet("color: gray;")
        self._update_prefix_hint()
        prefix_row.addWidget(self.prefix_hint)

        out_btn = QPushButton("Select output directory")
        out_btn.clicked.connect(self.pick_output)

        self.out_label = QLabel(f"Output: {self.state.output_dir}")

        layout.addWidget(pick)
        layout.addWidget(self.label)
        layout.addWidget(QLabel("Layers / operations to preview or run:"))
        layout.addWidget(self.layer_list)
        layout.addWidget(self.combined)
        layout.addLayout(prefix_row)
        layout.addWidget(out_btn)
        layout.addWidget(self.out_label)

        self.setLayout(layout)

    def set_combined(self, state):
        self.state.combined = bool(state)

    def on_prefix_changed(self, _):
        self.state.file_prefix = _normalize_file_prefix(self.prefix_edit.text())
        self._update_prefix_hint()

    def _update_prefix_hint(self):
        p = getattr(self.state, "file_prefix", "") or ""
        sample = f"{p}all.nc" if p else "all.nc"
        self.prefix_hint.setText(sample)

    def on_layer_toggle(self, _):
        self.previewRequested.emit()

    def pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.state.output_dir = d
            self.state.output_dir_user_set = True
            self.out_label.setText(f"Output: {d}")

    def _safe_mkdir(self, d):
        os.makedirs(d, exist_ok=True)
        return d

    def _extract_zip_to_workdir(self, zip_path: str) -> str:
        base_dir = os.path.dirname(zip_path)
        base_name = os.path.splitext(os.path.basename(zip_path))[0]
        work_dir = os.path.join(base_dir, f"_gerber_unzip_{base_name}")
        self._safe_mkdir(work_dir)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(work_dir)

        # If ZIP contains a single top-level folder, use it
        entries = [os.path.join(work_dir, p) for p in os.listdir(work_dir)]
        folders = [p for p in entries if os.path.isdir(p)]
        files = [p for p in entries if os.path.isfile(p)]
        if len(folders) == 1 and len(files) == 0:
            return folders[0]
        return work_dir

    def _copy_if_exists(self, src, dst):
        if src and os.path.exists(src):
            shutil.copyfile(src, dst)
            return True
        return False

    def _find_best_easyeda_file(self, gerber_dir: str, wanted_exts):
        for fn in os.listdir(gerber_dir):
            low = fn.lower()
            for ext in wanted_exts:
                if low.endswith(ext.lower()):
                    return os.path.join(gerber_dir, fn)
        return None

    def _ensure_canonical_easyeda(self, gerber_dir: str, prefix: str):
        targets = {
            "top_copper": os.path.join(gerber_dir, f"{prefix}-TopLayer.gbr"),
            "bot_copper": os.path.join(gerber_dir, f"{prefix}-BottomLayer.gbr"),
            "top_silk": os.path.join(gerber_dir, f"{prefix}-TopSilkLayer.gbr"),
            "bot_silk": os.path.join(gerber_dir, f"{prefix}-BottomSilkLayer.gbr"),
            "top_mask": os.path.join(gerber_dir, f"{prefix}-TopSolderMaskLayer.gbr"),
            "bot_mask": os.path.join(gerber_dir, f"{prefix}-BottomSolderMaskLayer.gbr"),
            "outline": os.path.join(gerber_dir, f"{prefix}-BoardOutLine.gbr"),
            "pth": os.path.join(gerber_dir, f"{prefix}-PTH.drl"),
            "npth": os.path.join(gerber_dir, f"{prefix}-NPTH.drl"),
            "top_paste": os.path.join(gerber_dir, f"{prefix}-TopPasteLayer.gbr"),
            "bot_paste": os.path.join(gerber_dir, f"{prefix}-BottomPasteLayer.gbr"),
            "doc": os.path.join(gerber_dir, f"{prefix}-DocLayer.gbr"),
        }

        def need(path):
            return not os.path.exists(path)

        src_top_copper = self._find_best_easyeda_file(gerber_dir, [".gtl"])
        src_bot_copper = self._find_best_easyeda_file(gerber_dir, [".gbl"])
        src_top_silk = self._find_best_easyeda_file(gerber_dir, [".gto"])
        src_bot_silk = self._find_best_easyeda_file(gerber_dir, [".gbo"])
        src_top_mask = self._find_best_easyeda_file(gerber_dir, [".gts"])
        src_bot_mask = self._find_best_easyeda_file(gerber_dir, [".gbs"])
        src_top_paste = self._find_best_easyeda_file(gerber_dir, [".gtp"])
        src_bot_paste = self._find_best_easyeda_file(gerber_dir, [".gbp"])
        src_outline = self._find_best_easyeda_file(gerber_dir, [".gko", ".gml"])
        src_doc = self._find_best_easyeda_file(gerber_dir, [".gdl"])

        if need(targets["top_copper"]):
            self._copy_if_exists(src_top_copper, targets["top_copper"])
        if need(targets["bot_copper"]):
            self._copy_if_exists(src_bot_copper, targets["bot_copper"])
        if need(targets["top_silk"]):
            self._copy_if_exists(src_top_silk, targets["top_silk"])
        if need(targets["bot_silk"]):
            self._copy_if_exists(src_bot_silk, targets["bot_silk"])
        if need(targets["top_mask"]):
            self._copy_if_exists(src_top_mask, targets["top_mask"])
        if need(targets["bot_mask"]):
            self._copy_if_exists(src_bot_mask, targets["bot_mask"])
        if need(targets["top_paste"]):
            self._copy_if_exists(src_top_paste, targets["top_paste"])
        if need(targets["bot_paste"]):
            self._copy_if_exists(src_bot_paste, targets["bot_paste"])
        if need(targets["outline"]):
            self._copy_if_exists(src_outline, targets["outline"])
        if need(targets["doc"]):
            self._copy_if_exists(src_doc, targets["doc"])

        drill_candidates = []
        for fn in os.listdir(gerber_dir):
            low = fn.lower()
            if low.endswith(".drl") or low.endswith(".txt"):
                drill_candidates.append(os.path.join(gerber_dir, fn))
        drill_candidates.sort()

        if drill_candidates:
            if need(targets["pth"]):
                self._copy_if_exists(drill_candidates[0], targets["pth"])
            if len(drill_candidates) > 1 and need(targets["npth"]):
                self._copy_if_exists(drill_candidates[1], targets["npth"])

    def detect_prefix(self, filename):
        suffixes = [
            "-TopLayer.gbr",
            "-BottomLayer.gbr",
            "-TopSolderMaskLayer.gbr",
            "-BottomSolderMaskLayer.gbr",
            "-TopSilkLayer.gbr",
            "-BottomSilkLayer.gbr",
            "-TopPasteLayer.gbr",
            "-BottomPasteLayer.gbr",
            "-BoardOutLine.gbr",
            "-PTH.drl",
            "-NPTH.drl",
            "-DocLayer.gbr",
        ]
        for suf in suffixes:
            if filename.endswith(suf):
                return filename[: -len(suf)]
        return os.path.splitext(filename)[0]

    def _refresh_layer_list(self):
        s = self.state
        self.layer_list.blockSignals(True)
        self.layer_list.clear()

        def add_item(key, label, checked):
            item = QListWidgetItem(label)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            item.setData(Qt.UserRole, key)
            self.layer_list.addItem(item)

        gd = s.gerber_dir
        p = s.prefix

        top_copper_fn = os.path.join(gd, f"{p}-TopLayer.gbr")
        bot_copper_fn = os.path.join(gd, f"{p}-BottomLayer.gbr")
        top_silk_fn = os.path.join(gd, f"{p}-TopSilkLayer.gbr")
        bot_silk_fn = os.path.join(gd, f"{p}-BottomSilkLayer.gbr")
        outline_fn = os.path.join(gd, f"{p}-BoardOutLine.gbr")
        top_mask_fn = os.path.join(gd, f"{p}-TopSolderMaskLayer.gbr")
        bot_mask_fn = os.path.join(gd, f"{p}-BottomSolderMaskLayer.gbr")
        top_paste_fn = os.path.join(gd, f"{p}-TopPasteLayer.gbr")
        bot_paste_fn = os.path.join(gd, f"{p}-BottomPasteLayer.gbr")
        doc_fn = os.path.join(gd, f"{p}-DocLayer.gbr")

        drill_exists = any(
            fn.lower().endswith((".drl", ".txt"))
            and (p in fn or fn.lower().endswith(("-pth.drl", "-npth.drl")))
            for fn in os.listdir(gd)
        )

        if os.path.exists(top_copper_fn):
            add_item("copper_isolation", "Copper isolation (Top)", True)
        if drill_exists:
            add_item("drilling", "Drilling", True)
        if os.path.exists(outline_fn):
            add_item("board_outline", "Board outline", True)
        if os.path.exists(top_silk_fn):
            add_item("silkscreen", "Silkscreen engraving (Top)", True)
        if os.path.exists(top_mask_fn) or os.path.exists(top_copper_fn):
            add_item("soldermask_clear", "Soldermask clear (Pads, Top)", False)

        if os.path.exists(bot_copper_fn):
            add_item("bottom_copper_preview", "Bottom copper (Preview)", False)
        if os.path.exists(bot_silk_fn):
            add_item("bottom_silkscreen_preview", "Bottom silkscreen (Preview)", False)
        if os.path.exists(top_mask_fn):
            add_item("top_mask_preview", "Top soldermask (Preview)", False)
        if os.path.exists(bot_mask_fn):
            add_item("bottom_mask_preview", "Bottom soldermask (Preview)", False)
        if os.path.exists(top_paste_fn):
            add_item("top_paste_preview", "Top paste (Preview)", False)
        if os.path.exists(bot_paste_fn):
            add_item("bottom_paste_preview", "Bottom paste (Preview)", False)
        if os.path.exists(doc_fn):
            add_item("doc_preview", "Documentation (GDL) (Preview)", False)

        self.layer_list.blockSignals(False)
        self.previewRequested.emit()

    def pick_gerber_or_zip(self):
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Select Gerber file / Drill file / ZIP",
            "",
            "Gerber/ZIP (*.zip *.gbr *.gtl *.gbl *.gko *.gml *.gts *.gbs *.gto *.gbo *.gtp *.gbp *.gdl *.drl *.txt);;All files (*.*)",
        )
        if not fn:
            return

        if fn.lower().endswith(".zip"):
            try:
                gerber_dir = self._extract_zip_to_workdir(fn)
            except Exception as e:
                QMessageBox.critical(self, "ZIP error", f"Could not extract ZIP:\n{e}")
                return
            prefix = os.path.splitext(os.path.basename(fn))[0]
        else:
            gerber_dir = os.path.dirname(fn)
            prefix = self.detect_prefix(os.path.basename(fn))

        self.state.gerber_dir = gerber_dir
        self.state.prefix = prefix

        # Default output dir to the gerber directory unless the user explicitly picked a different one
        if not getattr(self.state, "output_dir_user_set", False):
            self.state.output_dir = gerber_dir
            self.out_label.setText(f"Output: {gerber_dir}")

        try:
            self._ensure_canonical_easyeda(self.state.gerber_dir, self.state.prefix)
        except Exception as e:
            QMessageBox.warning(self, "Gerber rename/copy warning", f"Could not normalize filenames:\n{e}")

        # Update UI
        self.label.setText(f"Gerber folder: {self.state.gerber_dir}\nPrefix: {self.state.prefix}")
        self._refresh_layer_list()
