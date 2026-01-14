# ui/main_window.py
# FULL FILE REPLACEMENT
#
# Fix:
# - The drilling planner status text (preflight) now updates immediately when a board/zip is selected.
#   Previously, FilesTab only emitted previewRequested which called update_preview(), but preflight
#   only ran on optionsChanged/bitsChanged. Now previewRequested triggers BOTH.

import os
import glob
import shutil
import time
import traceback
from collections import Counter

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QTabWidget,
    QMessageBox,
)
from PySide6.QtCore import Qt

from shapely.geometry import MultiLineString, LineString
from shapely.affinity import translate

from ui.ui_state import UIState
from ui.tabs.files_tab import FilesTab
from ui.preview_widget import PreviewWidget
from ui.tabs.bits_tab import BitsTab
from ui.tabs.job_options_tab import JobOptionsTab
from ui.tabs.bit_editor_tab import BitEditorTab

from bitlib import bit_dict, save_settings
from copper_isolation import run_copper
from soldermask_clear import run_mask
from drilling import run_drill
from board_outline import run_outline
from silkscreen_mill import run_silk

from common_gerber import (
    load_copper,
    load_pads,
    load_tracks,
    normalize_to_ref,
    write_header,
    out_nc,
    parse_gerber_full,
)

from excellon_parser import load_drills_and_slots


OPS = [
    ("copper_isolation", run_copper),
    ("soldermask_clear", run_mask),
    ("drilling", run_drill),
    ("board_outline", run_outline),
    ("silkscreen", run_silk),
]

DEFAULT_MILL_HOLES_OVER = 1.2  # mm


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
        return tag in ("vbit", "mill")
    return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gerber → G-code (PCB MILLING)")
        self.resize(1000, 650)

        self.state = UIState()
        self.preview = PreviewWidget()

        self.tabs = QTabWidget()

        self.files_tab = FilesTab(self.state)
        # IMPORTANT FIX: refresh preflight + preview when files/layers change
        self.files_tab.previewRequested.connect(self._on_files_changed)
        self.tabs.addTab(self.files_tab, "Files & Layers")

        self.job_options_tab = JobOptionsTab(self.state)
        self.job_options_tab.optionsChanged.connect(self._on_options_changed)
        self.tabs.addTab(self.job_options_tab, "Job Options")

        self.bits_tab = BitsTab(self.state)
        self.bits_tab.bitsChanged.connect(self._on_bits_or_settings_changed)
        self.tabs.addTab(self.bits_tab, "Bits")

        self.bit_editor_tab = BitEditorTab(self.state)
        self.bit_editor_tab.bitsFileChanged.connect(self._on_bits_file_changed)
        self.tabs.addTab(self.bit_editor_tab, "Bits Editor")

        self.tabs.addTab(self.preview, "Preview")

        run_btn = QPushButton("Generate G-code")
        run_btn.clicked.connect(self.run_job)

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        layout.addWidget(run_btn)

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)

        self._heal_missing_bits_in_settings(save=False)
        self._refresh_preflight_ui()

    # ----------------------------
    # NEW: Files changed handler
    # ----------------------------
    def _on_files_changed(self):
        # When a new board is loaded or layer toggles change, update BOTH:
        # - preflight text
        # - preview graphics
        self._refresh_preflight_ui()
        if self.state.prefix and self.state.gerber_dir:
            self.update_preview()

    def _on_bits_file_changed(self):
        self.bits_tab.refresh_bits_list()
        self._on_bits_or_settings_changed()

    def _on_bits_or_settings_changed(self):
        self._heal_missing_bits_in_settings(save=False)
        self._refresh_preflight_ui()
        if self.state.prefix and self.state.gerber_dir:
            self.update_preview()

    def _on_options_changed(self):
        self.bits_tab.refresh_bits_list()
        self._heal_missing_bits_in_settings(save=False)
        self._refresh_preflight_ui()
        if self.state.prefix and self.state.gerber_dir:
            self.update_preview()

    def _refresh_preflight_ui(self):
        if not (self.state.prefix and self.state.gerber_dir):
            self.job_options_tab.update_status("info", "")
            self.bits_tab.set_drill_auto_plan([])
            return

        cwd = os.getcwd()
        os.chdir(self.state.gerber_dir)
        try:
            level, text, planned = self._preflight_compute()
        except Exception as e:
            level = "warn"
            text = f"Preflight error: {e}"
            planned = []
        finally:
            os.chdir(cwd)

        self.state.preflight_level = level
        self.state.preflight_text = text
        self.state.planned_drill_names = planned

        self.job_options_tab.update_status(level, text)
        self.bits_tab.set_drill_auto_plan(planned)

    def _update_selected_ops(self):
        s = self.state
        s.selected_ops = []

        for i in range(self.files_tab.layer_list.count()):
            item = self.files_tab.layer_list.item(i)
            if item.checkState() == Qt.Checked:
                key = item.data(Qt.UserRole)
                if key == "silkscreen_mill":
                    key = "silkscreen"
                s.selected_ops.append(key)

    def _resolve_fallback_bit_name(self, op_key: str):
        names = list(self.state.bits.sections())
        if not names:
            return None

        show_all = bool(getattr(self.state, "show_all_bits", False))
        if show_all:
            return names[0]

        for n in names:
            try:
                b = bit_dict(self.state.bits, n)
                if _is_reasonable_for_op(op_key, b.get("type", "")):
                    return n
            except Exception:
                continue
        return names[0]

    def _get_bit_safe(self, op_key: str, allow_update_settings: bool = True):
        s = self.state
        bit_name = s.settings.get(op_key, "bit", fallback=None)

        if bit_name and s.bits.has_section(bit_name):
            try:
                return bit_dict(s.bits, bit_name)
            except Exception:
                pass

        fallback = self._resolve_fallback_bit_name(op_key)
        if not fallback:
            return None

        if allow_update_settings:
            if not s.settings.has_section(op_key):
                s.settings.add_section(op_key)
            s.settings.set(op_key, "bit", fallback)

        try:
            return bit_dict(s.bits, fallback)
        except Exception:
            return None

    def _heal_missing_bits_in_settings(self, save: bool = False):
        changed = False
        for op_key, _func in OPS:
            desired = self.state.settings.get(op_key, "bit", fallback=None)
            if desired and self.state.bits.has_section(desired):
                continue
            fb = self._resolve_fallback_bit_name(op_key)
            if fb:
                if not self.state.settings.has_section(op_key):
                    self.state.settings.add_section(op_key)
                self.state.settings.set(op_key, "bit", fb)
                changed = True

        if changed and save:
            save_settings(self.state.settings)

    def _available_drill_bits(self):
        out = []
        for name in self.state.bits.sections():
            try:
                b = bit_dict(self.state.bits, name)
                if _type_tag(b.get("type", "")) == "drill":
                    d = float(b.get("diameter", 0.0))
                    if d > 0:
                        out.append(b)
            except Exception:
                continue
        out.sort(key=lambda x: float(x.get("diameter", 0.0)))
        uniq = {}
        for b in out:
            d = round(float(b["diameter"]), 6)
            if d not in uniq:
                uniq[d] = b
        return [uniq[d] for d in sorted(uniq.keys())]

    def _manual_selected_drills(self):
        if not self.state.settings.has_section("drilling"):
            return []
        sel = self.state.settings.get("drilling", "bits", fallback="").strip()
        names = [s.strip() for s in sel.split(",") if s.strip()]
        bits = []
        for n in names:
            try:
                if self.state.bits.has_section(n):
                    bits.append(bit_dict(self.state.bits, n))
            except Exception:
                continue
        bits.sort(key=lambda b: float(b.get("diameter", 0.0)))
        return bits

    def _plan_drills_for_holes(self, hole_ds, candidate_bits, tol, max_bits):
        tol = float(tol)
        max_bits = int(max_bits)

        if not hole_ds:
            return "ok", [], {}, ["No small holes to drill."]

        if not candidate_bits:
            return "error", [], {}, ["No drill bits available."]

        drills = sorted(candidate_bits, key=lambda b: float(b["diameter"]))
        ds = [float(b["diameter"]) for b in drills]

        def best_drill_index_for(hd, allowed_idx=None):
            limit = float(hd) + tol
            best = -1
            for i, d in enumerate(ds):
                if allowed_idx is not None and i not in allowed_idx:
                    continue
                if d <= limit + 1e-9:
                    best = i
                else:
                    break
            return best

        assign_idx = []
        for hd in hole_ds:
            assign_idx.append(best_drill_index_for(hd))

        if any(i < 0 for i in assign_idx):
            bad = [hole_ds[k] for k, i in enumerate(assign_idx) if i < 0]
            return "error", [], {}, [
                f"Impossible: smallest drill is larger than some holes (+tol). Missing holes: min {min(bad):.3f}mm."
            ]

        used = sorted(set(i for i in assign_idx if i >= 0))

        while len(used) > max_bits:
            holes_by = {i: [] for i in used}
            for hd, i in zip(hole_ds, assign_idx):
                holes_by[i].append(hd)

            droppable = []
            used_set = set(used)
            for di in used:
                other = used_set - {di}
                ok = True
                for hd in holes_by.get(di, []):
                    best2 = best_drill_index_for(hd, allowed_idx=other)
                    if best2 < 0:
                        ok = False
                        break
                if ok:
                    droppable.append(di)

            if not droppable:
                return "error", [drills[i] for i in used], {}, [
                    f"Impossible to stay within max drills={max_bits} while covering all holes (+tol)."
                ]

            holes_cnt = {i: 0 for i in used}
            for i in assign_idx:
                holes_cnt[i] = holes_cnt.get(i, 0) + 1

            droppable.sort(key=lambda i: (holes_cnt.get(i, 0), ds[i]))
            drop = droppable[0]
            used.remove(drop)

            used_set = set(used)
            for k, hd in enumerate(hole_ds):
                if assign_idx[k] == drop:
                    assign_idx[k] = best_drill_index_for(hd, allowed_idx=used_set)

        planned = [drills[i] for i in used]
        planned.sort(key=lambda b: float(b["diameter"]), reverse=True)

        counts = {}
        for i in used:
            counts[ds[i]] = 0
        for i in assign_idx:
            counts[ds[i]] = counts.get(ds[i], 0) + 1

        msg = [
            f"Planned drills ({len(planned)}): "
            + ", ".join(f"{b['name']}({b['diameter']:.3f})" for b in planned)
        ]
        return "ok", planned, counts, msg

    def _preflight_compute(self):
        s = self.state

        holes_raw, slots_raw = load_drills_and_slots(
            s.prefix, tol_xy=float(getattr(s, "hole_dedupe_tol", 0.10))
        )

        mill_over = float(getattr(s, "mill_holes_over", DEFAULT_MILL_HOLES_OVER))
        tol = float(getattr(s, "hole_match_tol", 0.05))
        max_drills = int(getattr(s, "max_drills", 3))
        control = getattr(s, "drill_control", "auto")

        outline_bit = self._get_bit_safe("board_outline", allow_update_settings=False) or {}
        router_d = float(outline_bit.get("diameter", 0.0))

        hole_ds = [float(d) for (_, _, d) in holes_raw]
        small_ds = [d for d in hole_ds if d < mill_over]
        big_ds = [d for d in hole_ds if d >= mill_over]
        slot_ws = [float(w) for (_, _, w) in slots_raw]

        if control == "manual":
            candidate = self._manual_selected_drills()
            candidate = sorted(candidate, key=lambda b: float(b.get("diameter", 0.0)))[:max_drills]
        else:
            candidate = self._available_drill_bits()

        level, planned_bits, _counts, plan_lines = self._plan_drills_for_holes(
            small_ds, candidate, tol, max_drills
        )

        planned_names = [b["name"] for b in planned_bits]

        lines = []
        if holes_raw:
            lines.append(
                f"Holes: {len(holes_raw)} total (dedupe XY={float(getattr(s,'hole_dedupe_tol',0.10)):.3f}mm)"
            )
            if small_ds:
                lines.append(f"Small holes (<{mill_over:.3f}): {len(small_ds)}")
            if big_ds:
                lines.append(f"Large holes (>= {mill_over:.3f}): {len(big_ds)} (milled)")
        if slots_raw:
            lines.append(f"Slots: {len(slots_raw)}")

        if router_d > 0 and slot_ws:
            min_slot = min(slot_ws)
            if router_d > min_slot + 1e-9:
                level = "error"
                lines.append(f"Impossible: Router {router_d:.3f}mm > smallest slot {min_slot:.3f}mm")

        if control == "auto":
            lines.append(f"Drill control: AUTO | max drills = {max_drills} | match tol = {tol:.3f}mm")
        else:
            lines.append(f"Drill control: MANUAL | max drills = {max_drills} | match tol = {tol:.3f}mm")

        lines.extend(plan_lines)

        if small_ds:
            most_common = Counter(round(d, 3) for d in small_ds).most_common(1)[0][0]
            lines.append(f"Most common small hole: {most_common:.3f}mm")

        if slot_ws or big_ds:
            min_feat = min(slot_ws + big_ds) if (slot_ws + big_ds) else None
            if min_feat is not None:
                lines.append(f"Recommended router: <= {min_feat * 0.80:.3f}mm")

        return level, "\n".join(lines), planned_names

    def _preflight_dialog(self):
        self._refresh_preflight_ui()

        if self.state.preflight_level != "error":
            return True

        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Critical)
        mb.setWindowTitle("Impossible task")
        mb.setText("Your current drill/router setup cannot complete this board.")
        mb.setDetailedText(self.state.preflight_text)
        mb.setStandardButtons(QMessageBox.Ok)
        mb.exec()
        return False

    def _copy_outputs_to_output_dir(self, gerber_dir: str, output_dir: str, start_time: float):
        if not output_dir:
            return []

        out_dir = os.path.abspath(output_dir)
        src_dir = os.path.abspath(gerber_dir)

        if out_dir == src_dir:
            return []

        os.makedirs(out_dir, exist_ok=True)

        produced = []
        for fn in glob.glob(os.path.join(src_dir, "*.nc")):
            try:
                st = os.stat(fn)
                if st.st_mtime < start_time - 0.25:
                    continue
            except Exception:
                continue

            base = os.path.basename(fn)
            dst = os.path.join(out_dir, base)

            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except Exception:
                root, ext = os.path.splitext(base)
                dst = os.path.join(out_dir, f"{root}_copy{ext}")

            shutil.copy2(fn, dst)
            produced.append(dst)

        return produced

    def run_job(self):
        s = self.state

        if not s.prefix or not s.gerber_dir:
            QMessageBox.warning(self, "No board", "Select a Gerber/ZIP first")
            return

        self._update_selected_ops()

        runnable = {k for (k, _) in OPS}
        ops_to_run = [k for k in s.selected_ops if k in runnable]

        if not ops_to_run:
            QMessageBox.warning(self, "Nothing selected", "Select at least one runnable operation")
            return

        if not self._preflight_dialog():
            return

        # Persist settings
        if not s.settings.has_section("copper_isolation"):
            s.settings.add_section("copper_isolation")
        s.settings.set("copper_isolation", "passes", str(s.iso_passes))

        if not s.settings.has_section("job"):
            s.settings.add_section("job")

        s.settings.set("job", "pcb_thickness", str(s.pcb_thickness))
        s.settings.set("job", "copper_thickness", str(s.copper_thickness))
        s.settings.set("job", "outline_tabs_enabled", str(s.outline_tabs_enabled))
        s.settings.set("job", "file_prefix", getattr(s, "file_prefix", "") or "")

        s.settings.set("job", "drill_control", getattr(s, "drill_control", "auto"))
        s.settings.set("job", "max_drills", str(int(getattr(s, "max_drills", 3))))
        s.settings.set("job", "hole_match_tol", str(float(getattr(s, "hole_match_tol", 0.05))))
        s.settings.set("job", "hole_dedupe_tol", str(float(getattr(s, "hole_dedupe_tol", 0.10))))
        s.settings.set("job", "mill_holes_over", str(float(getattr(s, "mill_holes_over", DEFAULT_MILL_HOLES_OVER))))
        s.settings.set("job", "show_all_bits", "true" if bool(getattr(s, "show_all_bits", False)) else "false")

        # Advanced knobs
        s.settings.set("job", "path_ordering", "true" if bool(getattr(s, "path_ordering", True)) else "false")
        s.settings.set("job", "geom_simplify_tol", str(float(getattr(s, "geom_simplify_tol", 0.0005))))
        s.settings.set("job", "geom_min_area", str(float(getattr(s, "geom_min_area", 1e-8))))
        s.settings.set("job", "geom_min_length", str(float(getattr(s, "geom_min_length", 1e-5))))
        s.settings.set("job", "ramp_len", str(float(getattr(s, "ramp_len", 0.0))))

        s.settings.set("job", "safe_z", str(float(getattr(s, "safe_z", 5.0))))
        s.settings.set("job", "travel_z", str(float(getattr(s, "travel_z", 10.0))))
        s.settings.set("job", "toolchange_z", str(float(getattr(s, "toolchange_z", 30.0))))
        s.settings.set("job", "park_x", str(float(getattr(s, "park_x", 0.0))))
        s.settings.set("job", "park_y", str(float(getattr(s, "park_y", 0.0))))
        s.settings.set("job", "spindle_warmup_s", str(float(getattr(s, "spindle_warmup_s", 0.0))))

        s.settings.set("job", "probe_on_start", "true" if bool(getattr(s, "probe_on_start", False)) else "false")
        s.settings.set("job", "probe_gcode", (getattr(s, "probe_gcode", "") or "").rstrip())

        self._heal_missing_bits_in_settings(save=False)
        save_settings(s.settings)

        planned_drill_bits = []
        try:
            cwd = os.getcwd()
            os.chdir(s.gerber_dir)
            try:
                _level, _text, planned_names = self._preflight_compute()
                for n in planned_names or []:
                    try:
                        if s.bits.has_section(n):
                            planned_drill_bits.append(bit_dict(s.bits, n))
                    except Exception:
                        continue
            finally:
                os.chdir(cwd)
        except Exception:
            planned_drill_bits = []

        start_time = time.time()
        cwd = os.getcwd()
        try:
            os.chdir(s.gerber_dir)

            if s.output_dir:
                os.makedirs(s.output_dir, exist_ok=True)

            if s.combined:
                combined_name = out_nc("all.nc")
                with open(combined_name, "w") as f:
                    write_header(f, job_name="combined")

            for op_key, func in OPS:
                if op_key not in ops_to_run:
                    continue

                bit = self._get_bit_safe(op_key, allow_update_settings=True)
                if bit is None:
                    QMessageBox.critical(
                        self,
                        "Missing bit",
                        f"No valid bit found for '{op_key}'.\nOpen Bits Editor and create/select a suitable bit.",
                    )
                    return

                if op_key == "copper_isolation":
                    func(bit, s.combined, s.prefix, passes=s.iso_passes)
                elif op_key == "board_outline":
                    func(bit, s.combined, s.prefix, tabs_enabled=s.outline_tabs_enabled)
                elif op_key == "drilling":
                    func(
                        bit,
                        s.combined,
                        s.prefix,
                        drill_bits=planned_drill_bits,
                        tol=float(getattr(s, "hole_match_tol", 0.05)),
                    )
                else:
                    func(bit, s.combined, s.prefix)

            if s.combined:
                combined_name = out_nc("all.nc")
                if os.path.exists(combined_name):
                    with open(combined_name, "a") as g:
                        g.write("\nM2\n")

            copied = self._copy_outputs_to_output_dir(s.gerber_dir, s.output_dir, start_time)

            msg = "G-code generated successfully."
            if s.output_dir:
                msg += f"\n\nOutput directory:\n{s.output_dir}"
            if copied:
                shown = "\n".join(os.path.basename(p) for p in copied[:20])
                if len(copied) > 20:
                    shown += f"\n... (+{len(copied)-20} more)"
                msg += f"\n\nCopied:\n{shown}"
            else:
                msg += f"\n\nFiles are in:\n{s.gerber_dir}"

            QMessageBox.information(self, "Done", msg)
            save_settings(s.settings)
            self.bits_tab.refresh_bits_list()

        except Exception as e:
            tb = traceback.format_exc()
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Critical)
            mb.setWindowTitle("Generation failed")
            mb.setText(f"Error: {e}")
            mb.setDetailedText(tb)
            mb.setStandardButtons(QMessageBox.Ok)
            mb.exec()
        finally:
            try:
                os.chdir(cwd)
            except Exception:
                pass

    def _load_silkscreen_centerlines_preview(self, silk_fn: str, *, ref_minx: float, ref_miny: float):
        gf = parse_gerber_full(silk_fn, strict=False, logger=None)
        segs = []
        for _ap, p1, p2 in gf.tracks:
            try:
                ls = LineString([p1, p2])
                if ls.length > 1e-6:
                    segs.append(ls)
            except Exception:
                continue
        if not segs:
            return None
        ml = MultiLineString(segs)
        return translate(ml, xoff=-ref_minx, yoff=-ref_miny)

    def update_preview(self):
        s = self.state

        if not s.prefix or not s.gerber_dir:
            return

        self._update_selected_ops()

        cwd = os.getcwd()
        os.chdir(s.gerber_dir)

        try:
            self.preview.clear()

            copper_raw = load_copper(s.prefix + "-TopLayer.gbr")
            copper_ref = normalize_to_ref(copper_raw, copper_raw)
            minx, miny, _, _ = copper_raw.bounds

            if "copper_isolation" in s.selected_ops:
                bit = self._get_bit_safe("copper_isolation", allow_update_settings=False)
                if bit:
                    tool_r = float(bit["diameter"]) / 2.0
                    passes = int(getattr(s, "iso_passes", 1) or 1)
                    for i in range(1, passes + 1):
                        off = tool_r * i
                        p = copper_ref.buffer(off).boundary
                        if p is not None and not p.is_empty:
                            self.preview.draw_copper_isolation(p)

            if "soldermask_clear" in s.selected_ops:
                try:
                    pads = load_pads(s.prefix + "-TopLayer.gbr")
                    pads = normalize_to_ref(pads, copper_raw)
                    if pads is not None and not pads.is_empty:
                        self.preview.draw_soldermask_clear(pads)
                except Exception:
                    pass

            if "silkscreen" in s.selected_ops:
                try:
                    silk_fn = s.prefix + "-TopSilkLayer.gbr"
                    silk_lines = self._load_silkscreen_centerlines_preview(
                        silk_fn, ref_minx=minx, ref_miny=miny
                    )
                    if silk_lines is not None and not silk_lines.is_empty:
                        self.preview.draw_silkscreen(silk_lines)
                except Exception:
                    pass

            if "board_outline" in s.selected_ops:
                try:
                    outline = load_tracks(s.prefix + "-BoardOutLine.gbr")
                    outline = normalize_to_ref(outline, copper_raw)
                    if outline is not None and not outline.is_empty:
                        self.preview.draw_through_outline(outline)
                except Exception:
                    pass

            if "drilling" in s.selected_ops:
                try:
                    holes_raw, slots_raw = load_drills_and_slots(
                        s.prefix,
                        tol_xy=float(getattr(s, "hole_dedupe_tol", 0.10)),
                    )
                    drills = [(x - minx, y - miny, d) for (x, y, d) in holes_raw]
                    slots = [
                        ((x1 - minx, y1 - miny), (x2 - minx, y2 - miny), w)
                        for ((x1, y1), (x2, y2), w) in slots_raw
                    ]
                    if drills:
                        self.preview.draw_through_holes(drills)
                    if slots:
                        self.preview.draw_through_slots(slots)
                except Exception:
                    pass

            self.preview.draw_origin()
            self.preview.draw_grid()

            if hasattr(self.preview, "fit_to_view"):
                self.preview.fit_to_view()
            else:
                self.preview.fit()

        finally:
            os.chdir(cwd)
