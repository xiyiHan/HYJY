"""第一步：录制会议音频。

只依赖本机声卡与麦克风，不需要联网、不需要模型。
录好的文件可以直接送到第二步做转写，也可以在这里挑选已有的录音文件。
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mmtools.config import Config
from mmtools.recorder import MeetingRecorder, RecorderError, check_recording_ready
from mmtools.transcribers import format_clock, probe_duration

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".mp4", ".amr"}


class RecordWorker(QThread):
    """后台录音线程。

    read_chunk 会阻塞一个分片时长，放在界面线程里会让窗口卡住，
    因此录音必须跑在子线程。
    """

    tick = Signal(float)      # 已录制秒数
    failed = Signal(str)
    saved = Signal(str)       # 输出文件路径

    def __init__(self, cfg: Config, output: Path, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.output = output
        self._stop = False
        self._recorder: MeetingRecorder | None = None

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102
        started = time.time()
        try:
            self._recorder = MeetingRecorder(self.cfg)
            self._recorder.start(self.output)
            while not self._stop:
                self._recorder.read_chunk()
                self.tick.emit(time.time() - started)
        except RecorderError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        finally:
            if self._recorder is not None:
                try:
                    self._recorder.stop()
                except Exception:
                    pass
        self.saved.emit(str(self.output))


class RecordPage(QWidget):
    """录制页。"""

    send_to_next = Signal(list)   # 送去转写的文件路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = Config.load()
        self.worker: RecordWorker | None = None
        self._build_ui()
        self.refresh_environment()
        self.reload_files()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("① 录制会议")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        sub = QLabel(
            "录制系统音频（会议对方的声音）与本机麦克风，自动混音为 16 kHz 单声道。"
            "只依赖本机声卡，不需要联网。"
        )
        sub.setObjectName("hint")
        sub.setWordWrap(True)
        root.addWidget(sub)

        self.lbl_env = QLabel("")
        self.lbl_env.setWordWrap(True)
        self.lbl_env.setObjectName("hint")
        root.addWidget(self.lbl_env)

        # ---- 录音控制 ----
        box = QGroupBox("录音")
        v = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("文件名"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(
            _dt.datetime.now().strftime("%Y-%m-%d_%H%M") + "_会议录音"
        )
        row.addWidget(self.ed_name, 1)
        v.addLayout(row)

        self.lbl_time = QLabel("00:00")
        self.lbl_time.setObjectName("sectionTitle")
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.lbl_time)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("开始录音")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("停止并保存")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        v.addLayout(btns)

        self.lbl_hint = QLabel("请确保已获得参会人对录音的知情同意。")
        self.lbl_hint.setObjectName("hint")
        self.lbl_hint.setWordWrap(True)
        v.addWidget(self.lbl_hint)
        root.addWidget(box)

        # ---- 录音文件 ----
        list_head = QHBoxLayout()
        lbl = QLabel("本机录音文件")
        lbl.setObjectName("sectionTitle")
        list_head.addWidget(lbl)
        list_head.addStretch(1)
        self.btn_reload = QPushButton("刷新列表")
        self.btn_reload.clicked.connect(self.reload_files)
        list_head.addWidget(self.btn_reload)
        root.addLayout(list_head)

        self.lbl_dir = QLabel("")
        self.lbl_dir.setObjectName("hint")
        self.lbl_dir.setWordWrap(True)
        root.addWidget(self.lbl_dir)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "时长", "录制时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        root.addWidget(self.table, 1)

        action = QHBoxLayout()
        self.btn_send = QPushButton("送到第二步：转写")
        self.btn_send.setObjectName("primary")
        self.btn_send.clicked.connect(self._send_selected)
        self.btn_open_dir = QPushButton("打开录音文件夹")
        self.btn_open_dir.clicked.connect(self._open_dir)
        action.addWidget(self.btn_send)
        action.addWidget(self.btn_open_dir)
        action.addStretch(1)
        root.addLayout(action)

    # ---------------------------------------------------------------- 环境

    def refresh_environment(self) -> None:
        try:
            self.cfg = Config.load()
        except Exception:
            pass
        ok, note = check_recording_ready()
        if ok:
            self.lbl_env.setText(f"录音环境就绪：{note}")
            self.lbl_env.setObjectName("ok")
        else:
            self.lbl_env.setText(
                f"录音不可用：{note}\n"
                f"可以在命令行执行 pip install -r requirements-record.txt 安装录音组件；"
                f"也可以直接用手机或其他软件录音，再到第二步导入文件。"
            )
            self.lbl_env.setObjectName("warn")
        self.lbl_env.style().unpolish(self.lbl_env)
        self.lbl_env.style().polish(self.lbl_env)
        self.btn_start.setEnabled(ok)

    def reload_files(self) -> None:
        try:
            audio_dir = self.cfg.path("audio")
        except Exception:
            return
        self.lbl_dir.setText(f"目录：{audio_dir}")
        files: list[Path] = []
        if audio_dir.exists():
            files = sorted(
                [f for f in audio_dir.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        self.table.setRowCount(len(files))
        for row, path in enumerate(files):
            item = QTableWidgetItem(path.name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.table.setItem(row, 0, item)

            secs = probe_duration(path)
            dur = QTableWidgetItem(format_clock(secs) if secs else "—")
            dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, dur)

            mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self.table.setItem(row, 2, QTableWidgetItem(mtime))

    # ---------------------------------------------------------------- 录音

    def _start(self) -> None:
        name = self.ed_name.text().strip() or (
            _dt.datetime.now().strftime("%Y-%m-%d_%H%M") + "_会议录音"
        )
        safe = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name)[:60]
        try:
            self.cfg.ensure_dirs()
            output = self.cfg.path("audio") / f"{safe}.wav"
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "无法创建输出目录", str(exc))
            return

        self.worker = RecordWorker(self.cfg, output, parent=self)
        self.worker.tick.connect(lambda s: self.lbl_time.setText(format_clock(s)))
        self.worker.failed.connect(self._on_failed)
        self.worker.saved.connect(self._on_saved)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.ed_name.setEnabled(False)
        self.lbl_hint.setText("录音进行中，点「停止并保存」结束。")

    def _stop(self) -> None:
        if self.worker is not None:
            self.btn_stop.setEnabled(False)
            self.lbl_hint.setText("正在停止并写入文件……")
            self.worker.stop()
            self.worker.wait(15000)

    def _on_failed(self, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.ed_name.setEnabled(True)
        self.lbl_hint.setText("请确保已获得参会人对录音的知情同意。")
        QMessageBox.critical(self, "录音失败", message)

    def _on_saved(self, path: str) -> None:
        p = Path(path)
        size_mb = p.stat().st_size / 1024 ** 2 if p.exists() else 0
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.ed_name.setEnabled(True)
        self.ed_name.clear()
        self.lbl_hint.setText("请确保已获得参会人对录音的知情同意。")
        self.reload_files()

        if size_mb < 0.01:
            QMessageBox.warning(
                self,
                "没有录到音频",
                "文件已生成但内容为空。请检查系统输出设备是否启用，"
                "以及会议软件的声音是否确实从扬声器输出。",
            )
        else:
            QMessageBox.information(
                self, "录音完成", f"已保存：\n{p}\n\n大小 {size_mb:.1f} MB"
            )

    # ---------------------------------------------------------------- 送下一步

    def _selected_paths(self) -> list[str]:
        paths: list[str] = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), 0)
            if item is not None:
                value = item.data(Qt.ItemDataRole.UserRole)
                if value:
                    paths.append(str(value))
        return paths

    def _send_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            # 没选就全部送去，减少一次点击
            paths = [
                str(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole))
                for r in range(self.table.rowCount())
                if self.table.item(r, 0) is not None
            ]
        if not paths:
            QMessageBox.information(self, "没有可送出的文件", "本机录音目录里还没有音频文件。")
            return
        self.send_to_next.emit(paths)

    def _open_dir(self) -> None:
        from ..utils import open_in_explorer

        try:
            open_in_explorer(self.cfg.path("audio"))
        except Exception:
            pass
