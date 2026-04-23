"""PySide6 main window for the MKV Commentary Sync tool."""

import os
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.track_utils import AudioTrack, check_tool, identify_tracks
from core.tool_paths import sibling_tool_path
from gui.worker import PipelineWorker, WorkerParams


# ── Drag-and-drop QLineEdit ───────────────────────────────────────────────────

class MkvLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setPlaceholderText("Path to .mkv file…")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile())
        else:
            super().dropEvent(event)


# ── Collapsible section ───────────────────────────────────────────────────────

class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._toggle = QToolButton(text=f"▶  {title}", checkable=True, checked=False)
        self._toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self._toggle.clicked.connect(self._on_toggle)
        self._content = QWidget()
        self._content.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._toggle)
        layout.addWidget(self._content)

    def setContentLayout(self, layout) -> None:
        self._content.setLayout(layout)

    def _on_toggle(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle.setText(f"{'▼' if checked else '▶'}  {self._title}")


# ── Log panel ─────────────────────────────────────────────────────────────────

_LEVEL_COLORS = {
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error":   "#ef4444",
    "info":    "#e2e8f0",
}


class LogPanel(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas, Courier New, monospace", 9))
        self.setStyleSheet(
            "QTextEdit { background: #1e293b; color: #e2e8f0; border-radius: 4px; padding: 6px; }"
        )
        self.setMinimumHeight(160)

    def append_message(self, msg: str, level: str = "info") -> None:
        color = _LEVEL_COLORS.get(level, _LEVEL_COLORS["info"])
        msg = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.append(f'<span style="color:{color};">{msg}</span>')
        self.ensureCursorVisible()

    def clear_log(self) -> None:
        self.clear()


# ── ffmpeg download thread ────────────────────────────────────────────────────

class _FfmpegDownloadThread(QThread):
    progress_pct = Signal(int)
    progress_msg = Signal(str)
    finished = Signal(bool, str)  # (success, ffmpeg_path or error message)

    def run(self) -> None:
        from core.downloader import download_ffmpeg
        try:
            ffmpeg, _ = download_ffmpeg(
                progress=self.progress_msg.emit,
                progress_pct=self.progress_pct.emit,
            )
            self.finished.emit(True, ffmpeg)
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MKV Commentary Sync")
        self.setMinimumWidth(800)

        self._worker: QThread | None = None
        self._src_tracks: List[AudioTrack] = []
        self._tgt_tracks: List[AudioTrack] = []
        self._src_ref_group = QButtonGroup(self)
        self._tgt_ref_group = QButtonGroup(self)
        self._mux_checkboxes: List[QCheckBox] = []
        self._settings = QSettings("mkvsyncdub", "mkvsyncdub")

        self._build_ui()
        self._restore_settings()
        self._check_tools()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Warning banner — populated dynamically
        self._banner = QFrame()
        self._banner.setStyleSheet("QFrame { background: #7c2d12; border-radius: 4px; }")
        self._banner_layout = QVBoxLayout(self._banner)
        self._banner_layout.setContentsMargins(10, 6, 10, 6)
        self._banner_layout.setSpacing(4)
        self._banner.hide()
        root.addWidget(self._banner)

        # ── Section 1: Files ──────────────────────────────────────────────────
        files_box = QGroupBox("Files")
        files_layout = QGridLayout(files_box)
        files_layout.setColumnStretch(1, 1)

        files_layout.addWidget(QLabel("Source file (has the commentary):"), 0, 0)
        self._source_edit = MkvLineEdit()
        files_layout.addWidget(self._source_edit, 0, 1)
        src_browse = QPushButton("Browse")
        src_browse.clicked.connect(lambda: self._browse_mkv(self._source_edit))
        files_layout.addWidget(src_browse, 0, 2)

        files_layout.addWidget(QLabel("Target file (to mux into):"), 1, 0)
        self._target_edit = MkvLineEdit()
        files_layout.addWidget(self._target_edit, 1, 1)
        tgt_browse = QPushButton("Browse")
        tgt_browse.clicked.connect(lambda: self._browse_mkv(self._target_edit))
        files_layout.addWidget(tgt_browse, 1, 2)

        self._source_edit.textChanged.connect(self._on_files_changed)
        self._target_edit.textChanged.connect(self._on_files_changed)

        root.addWidget(files_box)

        # ── Section 2: Reference Tracks ───────────────────────────────────────
        ref_box = QGroupBox("Reference Tracks (used for sync detection)")
        ref_layout = QHBoxLayout(ref_box)
        ref_layout.setSpacing(8)

        src_ref_sub = QGroupBox("Source")
        src_ref_sub_layout = QVBoxLayout(src_ref_sub)
        src_ref_sub_layout.setContentsMargins(6, 6, 6, 6)
        self._src_ref_table = self._make_track_table()
        src_ref_sub_layout.addWidget(self._src_ref_table)
        ref_layout.addWidget(src_ref_sub)

        tgt_ref_sub = QGroupBox("Target")
        tgt_ref_sub_layout = QVBoxLayout(tgt_ref_sub)
        tgt_ref_sub_layout.setContentsMargins(6, 6, 6, 6)
        self._tgt_ref_table = self._make_track_table()
        tgt_ref_sub_layout.addWidget(self._tgt_ref_table)
        ref_layout.addWidget(tgt_ref_sub)

        root.addWidget(ref_box)

        # ── Section 3: Tracks to Mux ──────────────────────────────────────────
        mux_box = QGroupBox("Tracks to Mux (from source)")
        mux_layout = QVBoxLayout(mux_box)
        mux_layout.setContentsMargins(6, 6, 6, 6)
        self._mux_table = self._make_track_table(select_header="Mux")
        mux_layout.addWidget(self._mux_table)
        root.addWidget(mux_box)

        # ── Section 4: Output & Options ───────────────────────────────────────
        output_box = QGroupBox("Output & Options")
        output_layout = QVBoxLayout(output_box)

        out_row = QGridLayout()
        out_row.setColumnStretch(1, 1)
        out_row.addWidget(QLabel("Output file:"), 0, 0)
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("Output .mkv path…")
        out_row.addWidget(self._output_edit, 0, 1)
        out_browse = QPushButton("Browse")
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_browse, 0, 2)
        output_layout.addLayout(out_row)

        # Collapsible advanced
        adv = CollapsibleSection("Advanced")
        adv_form = QGridLayout()
        adv_form.setContentsMargins(12, 4, 4, 4)
        adv_form.setColumnStretch(1, 1)

        self._sample_start = QSpinBox()
        self._sample_start.setRange(0, 7200)
        self._sample_start.setValue(120)
        self._sample_start.setSuffix(" s")

        self._sample_duration = QSpinBox()
        self._sample_duration.setRange(10, 3600)
        self._sample_duration.setValue(300)
        self._sample_duration.setSuffix(" s")

        self._sample_rate = QSpinBox()
        self._sample_rate.setRange(4000, 44100)
        self._sample_rate.setValue(8000)
        self._sample_rate.setSuffix(" Hz")

        self._ffmpeg_edit = QLineEdit("ffmpeg")
        self._mkvmerge_edit = QLineEdit("mkvmerge")
        self._ffmpeg_edit.editingFinished.connect(self._check_tools)
        self._mkvmerge_edit.editingFinished.connect(self._on_mkvmerge_path_changed)

        self._min_ncc = QDoubleSpinBox()
        self._min_ncc.setRange(0.001, 1.0)
        self._min_ncc.setSingleStep(0.005)
        self._min_ncc.setDecimals(3)
        self._min_ncc.setValue(0.02)
        self._min_ncc.setToolTip(
            "Minimum NCC score to accept a sample point.\n"
            "Lower this (e.g. 0.01) if all points are rejected — stereo vs surround\n"
            "or different audio masters can suppress NCC even for matching content."
        )

        for row, (label, widget) in enumerate([
            ("Sample start:", self._sample_start),
            ("Sample duration:", self._sample_duration),
            ("Sample rate:", self._sample_rate),
            ("Min. NCC:", self._min_ncc),
            ("ffmpeg path:", self._ffmpeg_edit),
            ("mkvmerge path:", self._mkvmerge_edit),
        ]):
            adv_form.addWidget(QLabel(label), row, 0)
            adv_form.addWidget(widget, row, 1)

        adv.setContentLayout(adv_form)
        output_layout.addWidget(adv)
        root.addWidget(output_box)

        # ── Section 5: Action & Progress ─────────────────────────────────────
        action_box = QGroupBox()
        action_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        action_layout = QVBoxLayout(action_box)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Analyze && Mux")
        self._run_btn.setMinimumHeight(36)
        self._run_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:disabled { background: #475569; color: #94a3b8; }"
        )
        self._run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self._run_btn, stretch=1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setMinimumHeight(36)
        self._cancel_btn.setStyleSheet(
            "QPushButton { background: #7f1d1d; color: white; border-radius: 4px; }"
            "QPushButton:hover { background: #991b1b; }"
            "QPushButton:disabled { background: #475569; color: #94a3b8; }"
        )
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.hide()
        btn_row.addWidget(self._cancel_btn)
        action_layout.addLayout(btn_row)

        self._offset_label = QLabel()
        self._offset_label.setAlignment(Qt.AlignCenter)
        self._offset_label.setStyleSheet(
            "QLabel { font-size: 13px; font-weight: bold; color: #22c55e; padding: 4px; }"
        )
        self._offset_label.hide()
        action_layout.addWidget(self._offset_label)

        self._mux_progress_bar = QProgressBar()
        self._mux_progress_bar.setRange(0, 100)
        self._mux_progress_bar.setTextVisible(True)
        self._mux_progress_bar.setFormat("Muxing… %p%")
        self._mux_progress_bar.hide()
        action_layout.addWidget(self._mux_progress_bar)

        self._log_panel = LogPanel()
        self._log_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._log_panel.hide()
        action_layout.addWidget(self._log_panel)

        self._open_folder_btn = QPushButton("Open output folder")
        self._open_folder_btn.hide()
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        action_layout.addWidget(self._open_folder_btn)

        root.addWidget(action_box)

    def _make_track_table(self, select_header: str = "") -> QTableWidget:
        t = QTableWidget(0, 6)
        t.setHorizontalHeaderLabels([select_header, "ID", "Language", "Codec", "Ch", "Name"])
        t.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setEnabled(False)
        return t

    # ── Settings persistence ──────────────────────────────────────────────────

    def _restore_settings(self) -> None:
        self._ffmpeg_edit.setText(self._settings.value("ffmpeg_path", "ffmpeg"))
        self._mkvmerge_edit.setText(self._settings.value("mkvmerge_path", "mkvmerge"))
        self._sample_start.setValue(int(self._settings.value("sample_start", 120)))
        self._sample_duration.setValue(int(self._settings.value("sample_duration", 300)))
        self._sample_rate.setValue(int(self._settings.value("sample_rate", 8000)))
        self._min_ncc.setValue(float(self._settings.value("min_ncc", 0.02)))

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._settings.setValue("ffmpeg_path", self._ffmpeg_edit.text())
        self._settings.setValue("mkvmerge_path", self._mkvmerge_edit.text())
        self._settings.setValue("sample_start", self._sample_start.value())
        self._settings.setValue("sample_duration", self._sample_duration.value())
        self._settings.setValue("sample_rate", self._sample_rate.value())
        self._settings.setValue("min_ncc", self._min_ncc.value())
        super().closeEvent(event)

    # ── Tool startup check ────────────────────────────────────────────────────

    def _check_tools(self) -> None:
        while self._banner_layout.count():
            item = self._banner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        ffmpeg_path = self._ffmpeg_edit.text().strip() or "ffmpeg"
        mkvmerge_path = self._mkvmerge_edit.text().strip() or "mkvmerge"

        rows = []
        if not check_tool(ffmpeg_path, "ffmpeg"):
            rows.append(("ffmpeg", "Download ffmpeg", self._on_download_ffmpeg))
        if not check_tool(mkvmerge_path, "mkvmerge"):
            rows.append(("mkvmerge", "Get MKVToolNix →", self._on_open_mkvtoolnix))

        if rows:
            for tool, btn_label, handler in rows:
                row = QWidget()
                row.setStyleSheet("background: transparent;")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                lbl = QLabel(f"⚠  {tool} not found on PATH — set path in Advanced, or:")
                lbl.setStyleSheet("color: #fed7aa;")
                rl.addWidget(lbl)
                rl.addStretch()
                btn = QPushButton(btn_label)
                btn.setStyleSheet(
                    "QPushButton { background: #c2410c; color: white; border-radius: 3px;"
                    "  padding: 2px 10px; border: none; }"
                    "QPushButton:hover { background: #ea580c; }"
                )
                btn.clicked.connect(handler)
                rl.addWidget(btn)
                self._banner_layout.addWidget(row)
            self._banner.show()
        else:
            self._banner.hide()

    # ── Binary download handlers ──────────────────────────────────────────────

    def _on_download_ffmpeg(self) -> None:
        dlg = QProgressDialog("Downloading ffmpeg (~75 MB)…", None, 0, 100, self)
        dlg.setWindowTitle("Downloading ffmpeg")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        self._ffmpeg_dl_thread = _FfmpegDownloadThread(self)
        self._ffmpeg_dl_thread.progress_pct.connect(dlg.setValue)
        self._ffmpeg_dl_thread.progress_msg.connect(dlg.setLabelText)
        self._ffmpeg_dl_thread.finished.connect(
            lambda ok, result: self._on_ffmpeg_download_done(ok, result, dlg)
        )
        self._ffmpeg_dl_thread.start()

    def _on_ffmpeg_download_done(self, success: bool, result: str, dlg: QProgressDialog) -> None:
        dlg.close()
        if success:
            self._ffmpeg_edit.setText(result)
            self._check_tools()
        else:
            while self._banner_layout.count():
                item = self._banner_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            lbl = QLabel(f"✗  ffmpeg download failed: {result}")
            lbl.setStyleSheet("color: #fca5a5;")
            lbl.setWordWrap(True)
            self._banner_layout.addWidget(lbl)
            self._banner.show()

    def _on_open_mkvtoolnix(self) -> None:
        from core.downloader import open_mkvtoolnix_page
        open_mkvtoolnix_page()

    # ── File pickers ──────────────────────────────────────────────────────────

    def _browse_mkv(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select MKV file", "", "MKV files (*.mkv);;All files (*)"
        )
        if path:
            edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save output as", "", "MKV files (*.mkv)"
        )
        if path:
            if not path.lower().endswith(".mkv"):
                path += ".mkv"
            self._output_edit.setText(path)

    # ── Files-changed handler ─────────────────────────────────────────────────

    def _on_files_changed(self, _text: str = "") -> None:
        source = self._source_edit.text().strip()
        target = self._target_edit.text().strip()

        if source and os.path.isfile(source):
            if source != getattr(self, "_loaded_source", None):
                self._load_source_tracks(source)
        else:
            self._clear_source_tracks()

        if target and os.path.isfile(target):
            if target != getattr(self, "_loaded_target", None):
                self._load_target_tracks(target)
            if not self._output_edit.text().strip():
                base, _ = os.path.splitext(target)
                self._output_edit.setText(base + "_with_commentary.mkv")
        else:
            self._clear_target_tracks()

    def _load_source_tracks(self, source_path: str) -> None:
        mkvmerge = self._mkvmerge_edit.text().strip() or "mkvmerge"
        try:
            tracks = identify_tracks(source_path, mkvmerge)
        except Exception as exc:
            self._clear_source_tracks()
            self._show_banner_error(f"Could not identify source tracks: {exc}")
            return
        self._loaded_source = source_path
        self._src_tracks = tracks
        self._populate_ref_table(self._src_ref_table, tracks, self._src_ref_group)
        self._populate_mux_table(tracks)

    def _load_target_tracks(self, target_path: str) -> None:
        mkvmerge = self._mkvmerge_edit.text().strip() or "mkvmerge"
        try:
            tracks = identify_tracks(target_path, mkvmerge)
        except Exception as exc:
            self._clear_target_tracks()
            self._show_banner_error(f"Could not identify target tracks: {exc}")
            return
        self._loaded_target = target_path
        self._tgt_tracks = tracks
        self._populate_ref_table(self._tgt_ref_table, tracks, self._tgt_ref_group)

    def _clear_source_tracks(self) -> None:
        for btn in self._src_ref_group.buttons():
            self._src_ref_group.removeButton(btn)
        self._src_ref_table.setRowCount(0)
        self._src_ref_table.setEnabled(False)
        self._mux_table.setRowCount(0)
        self._mux_table.setEnabled(False)
        self._src_tracks = []
        self._mux_checkboxes = []
        self._loaded_source = None

    def _clear_target_tracks(self) -> None:
        for btn in self._tgt_ref_group.buttons():
            self._tgt_ref_group.removeButton(btn)
        self._tgt_ref_table.setRowCount(0)
        self._tgt_ref_table.setEnabled(False)
        self._tgt_tracks = []
        self._loaded_target = None

    def _populate_ref_table(
        self,
        table: QTableWidget,
        tracks: List[AudioTrack],
        group: QButtonGroup,
    ) -> None:
        for btn in group.buttons():
            group.removeButton(btn)
        table.setRowCount(len(tracks))
        table.setEnabled(bool(tracks))
        for row, track in enumerate(tracks):
            radio = QRadioButton()
            if row == 0:
                radio.setChecked(True)
            group.addButton(radio, row)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.addWidget(radio)
            cl.setAlignment(Qt.AlignCenter)
            cl.setContentsMargins(0, 0, 0, 0)
            table.setCellWidget(row, 0, cell)
            for col, val in enumerate(
                [str(track.track_id), track.language, track.codec,
                 str(track.channels) if track.channels else "", track.name], 1
            ):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)

    def _populate_mux_table(self, tracks: List[AudioTrack]) -> None:
        self._mux_checkboxes = []
        self._mux_table.setRowCount(len(tracks))
        self._mux_table.setEnabled(bool(tracks))
        for row, track in enumerate(tracks):
            cb = QCheckBox()
            cb.setChecked(row == 0)
            self._mux_checkboxes.append(cb)
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.addWidget(cb)
            cl.setAlignment(Qt.AlignCenter)
            cl.setContentsMargins(0, 0, 0, 0)
            self._mux_table.setCellWidget(row, 0, cell)
            for col, val in enumerate(
                [str(track.track_id), track.language, track.codec,
                 str(track.channels) if track.channels else "", track.name], 1
            ):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self._mux_table.setItem(row, col, item)
        self._mux_table.resizeColumnsToContents()
        self._mux_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)

    def _show_banner_error(self, msg: str) -> None:
        while self._banner_layout.count():
            item = self._banner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        lbl = QLabel(f"⚠  {msg}")
        lbl.setStyleSheet("color: #fed7aa;")
        lbl.setWordWrap(True)
        self._banner_layout.addWidget(lbl)
        self._banner.show()

    # ── Selection helpers ─────────────────────────────────────────────────────

    def _src_ref_audio_index(self) -> int:
        idx = self._src_ref_group.checkedId()
        return max(0, idx)

    def _tgt_ref_audio_index(self) -> int:
        idx = self._tgt_ref_group.checkedId()
        return max(0, idx)

    def _selected_mux_track_ids(self) -> List[int]:
        return [
            self._src_tracks[i].track_id
            for i, cb in enumerate(self._mux_checkboxes)
            if cb.isChecked()
        ]

    # ── Run pipeline ──────────────────────────────────────────────────────────

    def _on_run(self) -> None:
        source = self._source_edit.text().strip()
        target = self._target_edit.text().strip()
        output = self._output_edit.text().strip()

        errors = []
        if not source or not os.path.isfile(source):
            errors.append("Source file not found.")
        if not target or not os.path.isfile(target):
            errors.append("Target file not found.")
        if not output:
            errors.append("Output path is required.")
        if not self._src_tracks:
            errors.append("No audio tracks loaded from source file.")
        if not self._tgt_tracks:
            errors.append("No audio tracks loaded from target file.")

        mux_ids = self._selected_mux_track_ids()
        if not mux_ids:
            errors.append("No tracks selected to mux.")

        if errors:
            self._log_panel.show()
            self._log_panel.clear_log()
            for e in errors:
                self._log_panel.append_message(f"✗ {e}", "error")
            return

        ffmpeg_path = self._ffmpeg_edit.text().strip() or "ffmpeg"
        params = WorkerParams(
            source_path=source,
            target_path=target,
            track_ids=mux_ids,
            output_path=output,
            sample_start=self._sample_start.value(),
            sample_duration=self._sample_duration.value(),
            sample_rate=self._sample_rate.value(),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=sibling_tool_path(ffmpeg_path, "ffmpeg", "ffprobe"),
            mkvmerge_path=self._mkvmerge_edit.text().strip() or "mkvmerge",
            src_ref_audio_index=self._src_ref_audio_index(),
            tgt_ref_audio_index=self._tgt_ref_audio_index(),
            min_ncc=self._min_ncc.value(),
        )

        self._log_panel.show()
        self._log_panel.clear_log()
        self._open_folder_btn.hide()
        self._mux_progress_bar.setValue(0)
        self._mux_progress_bar.hide()
        self._offset_label.hide()
        self._offset_label.setText("")
        self._run_btn.setEnabled(False)
        self._run_btn.setText("Processing…")
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Cancel")
        self._cancel_btn.show()

        self._worker = PipelineWorker(params, parent=self)
        self._worker.log.connect(self._on_log)
        self._worker.mux_progress.connect(self._on_mux_progress)
        self._worker.offset_detected.connect(self._on_offset_detected)
        self._worker.large_offset_query.connect(self._on_large_offset_query)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_mkvmerge_path_changed(self) -> None:
        self._check_tools()
        source = self._source_edit.text().strip()
        target = self._target_edit.text().strip()
        if source and os.path.isfile(source):
            self._loaded_source = None
        if target and os.path.isfile(target):
            self._loaded_target = None
        self._on_files_changed()

    def _on_cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")

    def _on_log(self, msg: str, level: str) -> None:
        self._log_panel.append_message(msg, level)

    def _on_mux_progress(self, pct: int) -> None:
        if not self._mux_progress_bar.isVisible():
            self._mux_progress_bar.show()
        self._mux_progress_bar.setValue(pct)

    def _on_offset_detected(self, offset_ms: int) -> None:
        sign = "+" if offset_ms >= 0 else ""
        self._offset_label.setText(f"Detected offset: {sign}{offset_ms} ms")
        self._offset_label.show()

    def _on_large_offset_query(self, offset_ms: int) -> None:
        reply = QMessageBox.question(
            self,
            "Large offset detected",
            f"The detected offset is {offset_ms:+d} ms ({abs(offset_ms) // 1000}s).\n\n"
            "This may indicate the wrong files were selected or an unusual edition "
            "difference. Proceed with muxing anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        ok = reply == QMessageBox.StandardButton.Yes
        if self._worker:
            self._worker.set_large_offset_response(ok)

    def _on_finished(self, success: bool, info: str) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("Analyze && Mux")
        self._cancel_btn.hide()
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setText("Cancel")
        self._mux_progress_bar.hide()
        self._mux_progress_bar.setValue(0)
        if success:
            self._output_path = info
            self._open_folder_btn.show()
        self._worker = None

    def _open_output_folder(self) -> None:
        path = getattr(self, "_output_path", None)
        if not path:
            return
        folder = os.path.dirname(os.path.abspath(path))
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
