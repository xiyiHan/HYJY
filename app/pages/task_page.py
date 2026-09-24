"""任务页：拖入音频、排队、开始处理、查看进度。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mmtools.transcribers import format_clock, probe_duration

# 支持的输入格式。实际转写时会用 ffmpeg 统一转成 16k 单声道 WAV。
AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".mp4", ".amr"}

# 实测速度：47 分 47 秒音频用 17 分 06 秒转完，约 2.8 倍速。
# 这个值只用于给用户一个大致预期，不代表承诺。
SPEED_FACTOR = 2.8
# 模型加载的固定开销（秒），与音频长短无关
MODEL_LOAD_SECONDS = 100

STATUS_WAITING = "等待中"
STATUS_RUNNING = "处理中"
STATUS_DONE = "已完成"
STATUS_FAILED = "失败"


class DropArea(QFrame):
    """接收拖拽的虚线区域。"""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropArea")
        self.setMinimumHeight(96)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("把录音文件拖到这里")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("支持 m4a / mp3 / wav 等格式，可一次拖入多个文件或整个文件夹")
        hint.setObjectName("dropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(title)
        layout.addWidget(hint)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths: list[str] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES:
                        paths.append(str(f))
            elif p.suffix.lower() in AUDIO_SUFFIXES:
                paths.append(str(p))
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()


class TaskPage(QWidget):
    """任务页。"""

    start_requested = Signal(list)   # 请求开始处理，携带文件路径列表
    cancel_requested = Signal()

    COL_NAME = 0
    COL_DURATION = 1
    COL_STATUS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list[Path] = []
        self._durations: dict[str, float | None] = {}
        self._running = False
        self._started_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._build_ui()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self.add_files)
        root.addWidget(self.drop_area)

        pick_row = QHBoxLayout()
        self.btn_pick = QPushButton("选择文件")
        self.btn_pick.clicked.connect(self._pick_files)
        self.btn_record = QPushButton("录制会议")
        self.btn_record.clicked.connect(self._open_record_dialog)
        self.btn_clear = QPushButton("清空队列")
        self.btn_clear.clicked.connect(self.clear_files)
        pick_row.addWidget(self.btn_pick)
        pick_row.addWidget(self.btn_record)
        pick_row.addWidget(self.btn_clear)
        pick_row.addStretch(1)
        self.lbl_queue = QLabel("队列为空")
        self.lbl_queue.setObjectName("muted")
        pick_row.addWidget(self.lbl_queue)
        root.addLayout(pick_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "时长", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_DURATION, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 3)

        action_row = QHBoxLayout()
        self.btn_start = QPushButton("开始处理")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_start.setEnabled(False)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setEnabled(False)
        action_row.addWidget(self.btn_start)
        action_row.addWidget(self.btn_cancel)
        action_row.addStretch(1)
        self.lbl_eta = QLabel("")
        self.lbl_eta.setObjectName("muted")
        action_row.addWidget(self.lbl_eta)
        root.addLayout(action_row)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        root.addWidget(self.bar)

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("statusLine")
        root.addWidget(self.lbl_status)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("logView")
        self.log.setPlaceholderText("处理日志会显示在这里")
        root.addWidget(self.log, 2)

    # ---------------------------------------------------------------- 队列

    def add_files(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            p = Path(raw)
            if not p.exists() or p.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            if p in self._files:
                continue
            self._files.append(p)
            self._durations[str(p)] = probe_duration(p)
            added += 1
        if added:
            self._refresh_table()
            self.append_log(f"已加入 {added} 个文件")

    def clear_files(self) -> None:
        if self._running:
            QMessageBox.information(self, "提示", "正在处理中，无法清空队列。")
            return
        self._files.clear()
        self._durations.clear()
        self._refresh_table()

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择录音文件",
            "",
            "音频文件 (*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.wma *.mp4 *.amr);;所有文件 (*)",
        )
        if files:
            self.add_files(files)

    def _open_record_dialog(self) -> None:
        """打开录音对话框，录完自动加入队列。"""
        from mmtools.config import Config

        from .record_dialog import RecordDialog

        try:
            cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "配置读取失败", str(exc))
            return

        dialog = RecordDialog(cfg, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.output_path:
            self.add_files([str(dialog.output_path)])
            self.append_log(f"录音已加入队列：{dialog.output_path.name}")

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self._files))
        for row, path in enumerate(self._files):
            name_item = QTableWidgetItem(path.name)
            name_item.setToolTip(str(path))
            self.table.setItem(row, self.COL_NAME, name_item)

            secs = self._durations.get(str(path))
            dur_item = QTableWidgetItem(format_clock(secs) if secs else "—")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_DURATION, dur_item)

            status = STATUS_WAITING
            if self._running and row == self._current_row():
                status = STATUS_RUNNING
            st_item = QTableWidgetItem(status)
            st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_STATUS, st_item)

        count = len(self._files)
        total = sum(v for v in self._durations.values() if v)
        if count == 0:
            self.lbl_queue.setText("队列为空")
        else:
            text = f"共 {count} 个文件"
            if total:
                text += f"，总时长 {format_clock(total)}"
            self.lbl_queue.setText(text)
        self.btn_start.setEnabled(count > 0 and not self._running)
        self._update_eta()

    def _current_row(self) -> int:
        return getattr(self, "_current_index", 0)

    def _set_row_status(self, index: int, status: str) -> None:
        if 0 <= index < self.table.rowCount():
            item = QTableWidgetItem(status)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(index, self.COL_STATUS, item)

    # ---------------------------------------------------------------- 预估

    def _update_eta(self) -> None:
        total = sum(v for v in self._durations.values() if v)
        unknown = sum(1 for v in self._durations.values() if not v)
        if not total:
            self.lbl_eta.setText("")
            return
        estimate = MODEL_LOAD_SECONDS + total / SPEED_FACTOR
        text = f"预计需要 {format_clock(estimate)}"
        if unknown:
            text += f"（{unknown} 个文件时长未知，未计入）"
        self.lbl_eta.setText(text)

    # ---------------------------------------------------------------- 运行

    def _on_start(self) -> None:
        if not self._files:
            return
        self.start_requested.emit([str(p) for p in self._files])

    def _on_cancel(self) -> None:
        if not self._running:
            return
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("正在取消……（当前转写批次结束后生效）")
        self.cancel_requested.emit()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.btn_pick.setEnabled(not running)
        self.btn_record.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.btn_start.setEnabled(not running and bool(self._files))
        self.btn_cancel.setEnabled(running)
        if running:
            self._started_at = time.time()
            self.bar.setValue(0)
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        if not self._running:
            return
        elapsed = time.time() - self._started_at
        base = self.lbl_status.text().split("  ·  ")[0]
        self.lbl_status.setText(f"{base}  ·  已用 {format_clock(elapsed)}")

    # ---------------------------------------------------------------- 事件

    def on_progress(self, payload: dict) -> None:
        stage = payload.get("stage", "")
        current = int(payload.get("current") or 0)
        total = int(payload.get("total") or 0)

        if stage == "start":
            self._current_index = 0
            self.append_log("=" * 52)
            self.append_log(f"开始处理，共 {total} 个文件")
            self.append_log("=" * 52)
            self._refresh_table()

        elif stage == "file_start":
            idx = max(0, current - 1)
            self._current_index = idx
            for r in range(self.table.rowCount()):
                item = self.table.item(r, self.COL_STATUS)
                if item and item.text() == STATUS_RUNNING:
                    self._set_row_status(r, STATUS_WAITING)
            self._set_row_status(idx, STATUS_RUNNING)
            self.table.selectRow(idx)
            self.lbl_status.setText(f"处理中：{payload.get('file', '')}")

        elif stage == "file_done":
            idx = max(0, current - 1)
            self._set_row_status(idx, STATUS_DONE)
            if total:
                self.bar.setValue(int(current / total * 100))
            self._update_status_after_file(current, total)

        elif stage == "cancelled":
            self.lbl_status.setText(f"已取消  ·  {payload.get('message', '')}")
            self.append_log("")
            self.append_log(payload.get("message", "已取消"))

        elif stage == "all_done":
            self.bar.setValue(100)
            self.lbl_status.setText(f"全部完成，共 {total} 个文件")

        elif stage == "error":
            self.append_log("")
            self.append_log(f"处理失败：{payload.get('error', '')}")

    def _update_status_after_file(self, current: int, total: int) -> None:
        elapsed = time.time() - self._started_at
        if current >= total:
            return
        remaining_files = total - current
        # 依据已完成的平均耗时外推，比固定倍速更贴近实际
        per_file = elapsed / max(1, current)
        eta = per_file * remaining_files
        self.lbl_status.setText(
            f"已完成 {current}/{total}  ·  已用 {format_clock(elapsed)}"
            f"  ·  预计还需 {format_clock(eta)}"
        )

    def mark_failed(self, index: int, reason: str = "") -> None:
        self._set_row_status(index, STATUS_FAILED)
        if reason:
            self.append_log(f"第 {index + 1} 个文件失败：{reason}")

    def append_log(self, text: str) -> None:
        if not text:
            self.log.appendPlainText("")
            return
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
