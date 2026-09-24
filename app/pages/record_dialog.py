"""录音对话框。

复用 mmtools.recorder 的 WASAPI loopback 采集能力，把命令行版的录音功能
搬到界面上。录音同样放在子线程：read_chunk 会阻塞一个分片时长，
放在界面线程里会让窗口卡住。
"""

from __future__ import annotations

import datetime as _dt
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from mmtools.config import Config
from mmtools.recorder import MeetingRecorder, RecorderError, check_recording_ready
from mmtools.transcribers import format_clock


class RecordWorker(QThread):
    """后台录音线程。"""

    tick = Signal(float)          # 已录制秒数
    failed = Signal(str)
    saved = Signal(str)           # 输出文件路径

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


class RecordDialog(QDialog):
    """录音对话框。"""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.worker: RecordWorker | None = None
        self.output_path: Path | None = None

        self.setWindowTitle("录制会议音频")
        self.setMinimumWidth(460)
        self._build_ui()
        self._check_env()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.lbl_env = QLabel("")
        self.lbl_env.setWordWrap(True)
        self.lbl_env.setObjectName("hint")
        root.addWidget(self.lbl_env)

        row = QHBoxLayout()
        row.addWidget(QLabel("文件名"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText(
            _dt.datetime.now().strftime("%Y-%m-%d_%H%M") + "_会议录音"
        )
        row.addWidget(self.ed_name, 1)
        root.addLayout(row)

        self.lbl_time = QLabel("00:00")
        self.lbl_time.setObjectName("sectionTitle")
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_time)

        self.lbl_hint = QLabel(
            "录制内容为系统音频（会议对方的声音）与本机麦克风，自动混音为 16 kHz 单声道。\n"
            "请确保已获得参会人对录音的知情同意。"
        )
        self.lbl_hint.setObjectName("hint")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

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
        root.addLayout(btns)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

    def _check_env(self) -> None:
        ok, note = check_recording_ready()
        if ok:
            self.lbl_env.setText(f"录音环境就绪：{note}")
        else:
            self.lbl_env.setText(f"录音不可用：{note}")
            self.lbl_env.setObjectName("warn")
            self.btn_start.setEnabled(False)

    # ---------------------------------------------------------------- 控制

    def _start(self) -> None:
        name = self.ed_name.text().strip() or _dt.datetime.now().strftime(
            "%Y-%m-%d_%H%M"
        ) + "_会议录音"
        safe = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in name)[:60]
        try:
            self.cfg.ensure_dirs()
            self.output_path = self.cfg.path("audio") / f"{safe}.wav"
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "无法创建输出目录", str(exc))
            return

        self.worker = RecordWorker(self.cfg, self.output_path, parent=self)
        self.worker.tick.connect(self._on_tick)
        self.worker.failed.connect(self._on_failed)
        self.worker.saved.connect(self._on_saved)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.ed_name.setEnabled(False)
        self.lbl_hint.setText("录音进行中，按「停止并保存」结束。")

    def _stop(self) -> None:
        if self.worker is not None:
            self.btn_stop.setEnabled(False)
            self.lbl_hint.setText("正在停止并写入文件……")
            self.worker.stop()
            self.worker.wait(15000)

    def _on_tick(self, seconds: float) -> None:
        self.lbl_time.setText(format_clock(seconds))

    def _on_failed(self, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.ed_name.setEnabled(True)
        QMessageBox.critical(self, "录音失败", message)

    def _on_saved(self, path: str) -> None:
        p = Path(path)
        size_mb = p.stat().st_size / 1024 ** 2 if p.exists() else 0
        if size_mb < 0.01:
            QMessageBox.warning(
                self, "没有录到音频",
                "文件已生成但内容为空。请检查系统输出设备是否启用，"
                "以及会议软件的声音是否确实从扬声器输出。",
            )
        else:
            QMessageBox.information(
                self, "录音完成", f"已保存：\n{p}\n\n大小 {size_mb:.1f} MB"
            )
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "录音进行中",
                "正在录音，关闭将停止并保存当前内容。确定关闭吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(15000)
        event.accept()
