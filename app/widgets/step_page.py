"""分步页面的公共骨架。

「转写」与「纪要」两步的执行结构完全一致：检查环境 → 排队 → 后台执行 →
回报进度 → 展示结果。差异只在三处，由子类覆写：

    build_queue()        输入队列的接受格式与信息列
    check_environment()  这一步依赖什么条件才可用
    create_worker()      实际执行什么

这样三步之间既能独立运行，又不必各写一套进度与日志逻辑。
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mmtools.transcribers import format_clock


class StepPage(QWidget):
    """分步页面的基类。"""

    #: 页标题与一句话说明
    step_title = "步骤"
    step_subtitle = ""

    #: 完成后是否自动切到下一步（由主窗口连接）
    go_next_requested = Signal(list)   # 产物路径列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._started_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._build_base_ui()

    # ---------------------------------------------------------------- 界面

    def _build_base_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.lbl_title = QLabel(self.step_title)
        self.lbl_title.setObjectName("sectionTitle")
        self.lbl_subtitle = QLabel(self.step_subtitle)
        self.lbl_subtitle.setObjectName("hint")
        self.lbl_subtitle.setWordWrap(True)
        titles.addWidget(self.lbl_title)
        titles.addWidget(self.lbl_subtitle)
        head.addLayout(titles, 1)
        self.btn_env = QPushButton("检查环境")
        self.btn_env.clicked.connect(self.refresh_environment)
        head.addWidget(self.btn_env, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        self.lbl_env = QLabel("")
        self.lbl_env.setWordWrap(True)
        self.lbl_env.setObjectName("hint")
        root.addWidget(self.lbl_env)

        self.queue = self.build_queue()
        if self.queue is not None:
            self.queue.changed.connect(self._on_queue_changed)
            root.addWidget(self.queue, 1)

        action = QHBoxLayout()
        self.btn_run = QPushButton(self.run_label())
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._on_run)
        self.btn_run.setEnabled(False)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setEnabled(False)
        action.addWidget(self.btn_run)
        action.addWidget(self.btn_cancel)
        action.addStretch(1)
        self.lbl_eta = QLabel("")
        self.lbl_eta.setObjectName("muted")
        action.addWidget(self.lbl_eta)
        root.addLayout(action)

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

        self.refresh_environment()

    # ---------------------------------------------------------------- 子类接口

    def build_queue(self):
        """返回输入队列控件，或 None（表示这一步没有文件队列，例如录音）。"""
        return None

    def run_label(self) -> str:
        return "开始"

    def check_environment(self) -> tuple[bool, str]:
        """返回 (是否可用, 说明文本)。"""
        return True, "就绪"

    def create_worker(self, files: list[Path]):
        """创建并返回要执行的 QThread。"""
        raise NotImplementedError

    def on_finished(self, outcomes: list) -> None:
        """全部完成后的钩子，子类可扩展。"""

    def eta_text(self, files: list[Path]) -> str:
        return ""

    # ---------------------------------------------------------------- 环境

    def refresh_environment(self) -> None:
        try:
            ok, note = self.check_environment()
        except Exception as exc:  # noqa: BLE001
            ok, note = False, f"环境检查失败：{exc}"
        self.lbl_env.setText(note)
        self.lbl_env.setObjectName("ok" if ok else "warn")
        self.lbl_env.style().unpolish(self.lbl_env)
        self.lbl_env.style().polish(self.lbl_env)
        self._env_ok = ok
        self._update_run_button()

    def _on_queue_changed(self) -> None:
        self._update_run_button()
        if self.queue is not None:
            self.lbl_eta.setText(self.eta_text(self.queue.files()))

    def _update_run_button(self) -> None:
        if self._running:
            self.btn_run.setEnabled(False)
            return
        has_files = self.queue is not None and bool(self.queue.files())
        self.btn_run.setEnabled(bool(getattr(self, "_env_ok", False)) and has_files)

    # ---------------------------------------------------------------- 执行

    def _on_run(self) -> None:
        if self._running or self.queue is None:
            return
        files = self.queue.files()
        if not files:
            return
        try:
            worker = self.create_worker(files)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "无法开始", str(exc))
            return

        self._worker = worker
        worker.log_line.connect(self.append_log)
        worker.progress.connect(self.on_progress)
        worker.finished.connect(self._on_worker_finished)
        if hasattr(worker, "outcomes"):
            worker.outcomes.connect(self._on_outcomes)

        self.set_running(True)
        if self.queue is not None:
            self.queue.reset_status()
        self.append_log("")
        self.append_log("=" * 52)
        self.append_log(f"{self.step_title}：共 {len(files)} 个文件")
        self.append_log("=" * 52)
        worker.start()

    def _on_cancel(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText("正在取消……（当前任务结束后生效）")
            worker.cancel()

    def set_running(self, running: bool) -> None:
        self._running = running
        if self.queue is not None:
            self.queue.set_enabled_editing(not running)
        self.btn_cancel.setEnabled(running)
        self._update_run_button()
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

    def _on_worker_finished(self) -> None:
        self.set_running(False)
        self._worker = None

    def _on_outcomes(self, outcomes: list) -> None:
        self.on_finished(outcomes)
        ok = [o for o in outcomes if getattr(o, "ok", True)]
        bad = [o for o in outcomes if not getattr(o, "ok", True)]
        if bad:
            self.append_log("")
            self.append_log(f"完成：成功 {len(ok)} 个，失败 {len(bad)} 个")
            for o in bad:
                self.append_log(f"  失败：{Path(str(o.source)).name} —— {o.error}")
        # 把成功产出的路径交给主窗口，用于「去下一步」
        produced: list[str] = []
        for o in ok:
            produced.extend(str(p) for p in getattr(o, "outputs", []))
        if produced:
            self.go_next_requested.emit(produced)

    # ---------------------------------------------------------------- 进度

    def on_progress(self, payload: dict) -> None:
        stage = payload.get("stage", "")
        current = int(payload.get("current") or 0)
        total = int(payload.get("total") or 0)

        if stage == "file_start" and self.queue is not None:
            idx = max(0, current - 1)
            self.queue.set_status(idx, "处理中")
            self.queue.select_row(idx)
            self.lbl_status.setText(f"处理中：{payload.get('file', '')}")
        elif stage == "file_done" and self.queue is not None:
            self.queue.set_status(max(0, current - 1), "已完成")
            if total:
                self.bar.setValue(int(current / total * 100))
            self._update_eta_after(current, total)
        elif stage == "file_failed" and self.queue is not None:
            self.queue.set_status(max(0, current - 1), "失败")
            if total:
                self.bar.setValue(int(current / total * 100))
        elif stage == "all_done":
            self.bar.setValue(100)
            self.lbl_status.setText(payload.get("message") or "全部完成")
        elif stage == "cancelled":
            self.lbl_status.setText(payload.get("message") or "已取消")
        elif stage == "error":
            self.append_log(f"处理失败：{payload.get('error', '')}")

    def _update_eta_after(self, current: int, total: int) -> None:
        if current >= total:
            return
        elapsed = time.time() - self._started_at
        per_file = elapsed / max(1, current)
        self.lbl_status.setText(
            f"已完成 {current}/{total}  ·  已用 {format_clock(elapsed)}"
            f"  ·  预计还需 {format_clock(per_file * (total - current))}"
        )

    def append_log(self, text: str) -> None:
        if not text:
            self.log.appendPlainText("")
            return
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
