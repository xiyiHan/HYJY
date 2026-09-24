"""主窗口：左侧导航 + 页面容器 + 任务编排。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from mmtools.config import Config

from .pages.help_page import HelpPage
from .pages.result_page import ResultPage
from .pages.settings_page import SettingsPage
from .pages.task_page import TaskPage
from .worker import ProcessWorker


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("会议纪要工具")
        self.resize(1000, 700)
        self.setMinimumSize(820, 560)

        self.cfg = Config.load()
        self.worker: ProcessWorker | None = None

        self._build_ui()
        self._refresh_status_bar()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("navList")
        for label in ("任务", "结果", "设置", "帮助"):
            self.nav.addItem(QListWidgetItem(label))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.task_page = TaskPage()
        self.result_page = ResultPage()
        self.settings_page = SettingsPage()
        self.help_page = HelpPage()
        for page in (self.task_page, self.result_page, self.settings_page, self.help_page):
            self.stack.addWidget(page)

        self.task_page.start_requested.connect(self.start_processing)
        self.task_page.cancel_requested.connect(self.cancel_processing)

        self._build_status_bar()

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.lbl_env = QLabel("")
        bar.addPermanentWidget(self.lbl_env)

    def _refresh_status_bar(self) -> None:
        try:
            self.cfg = Config.load()
            engine = self.cfg.backend_name
            _, prov = self.cfg.provider()
            has_key = bool(self.cfg.api_key())
            key_state = "已配置" if has_key else "未配置密钥"
            self.lbl_env.setText(
                f"转写：{engine}    纪要：{prov.get('label', '')}（{key_state}）"
            )
            if not has_key:
                self.lbl_env.setObjectName("warn")
            else:
                self.lbl_env.setObjectName("muted")
            self.lbl_env.style().unpolish(self.lbl_env)
            self.lbl_env.style().polish(self.lbl_env)
        except Exception as exc:  # noqa: BLE001
            self.lbl_env.setText(f"配置异常：{exc}")

    def _on_nav_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        if row == 2:  # 设置页每次进入都重新载入，避免显示过期值
            self.settings_page.cfg = Config.load()
            self.settings_page.load_values()
        if row in (2, 3):
            self._refresh_status_bar()

    # ---------------------------------------------------------------- 处理

    def start_processing(self, paths: list[str]) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "已有任务在处理中。")
            return

        try:
            self.cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "配置错误", str(exc))
            return

        if not self.cfg.api_key():
            answer = QMessageBox.question(
                self,
                "尚未配置密钥",
                "还没有配置云端平台的 API Key，无法生成会议纪要。\n\n"
                "是否只做转写（先得到文字稿，稍后再生成纪要）？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            skip_summary = True
        else:
            skip_summary = False

        template_value = (self.cfg.get("template", "path") or "").strip()
        form_template = Path(template_value) if template_value else None
        if form_template is not None and not form_template.exists():
            QMessageBox.warning(
                self, "模板不存在",
                f"配置中的模板文件不存在：\n{form_template}\n\n将改用通用排版生成。",
            )
            form_template = None

        known = dict(self.cfg.get("template", "known") or {})

        self.task_page.append_log("")
        if form_template:
            self.task_page.append_log(f"将按模板生成：{form_template.name}")
        else:
            self.task_page.append_log("未配置模板，将生成通用排版的 Word 纪要")

        self.worker = ProcessWorker(
            cfg=self.cfg,
            audios=[Path(p) for p in paths],
            form_template=form_template,
            known=known,
            skip_summary=skip_summary,
            parent=self,
        )
        self.worker.log_line.connect(self.task_page.append_log)
        self.worker.progress.connect(self.task_page.on_progress)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_worker_finished)

        self.task_page.set_running(True)
        self.worker.start()

    def cancel_processing(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()

    def _on_succeeded(self, results: list) -> None:
        self.result_page.add_results(results)
        self.task_page.set_running(False)
        self.task_page.append_log("")
        self.task_page.append_log("全部完成。")
        self.nav.setCurrentRow(1)

    def _on_failed(self, message: str) -> None:
        self.task_page.set_running(False)
        self.task_page.append_log("")
        self.task_page.append_log(f"处理失败：{message}")
        QMessageBox.critical(self, "处理失败", message)

    def _on_cancelled(self) -> None:
        self.task_page.set_running(False)
        self.task_page.append_log("")
        self.task_page.append_log("任务已取消，已完成的文字稿与纪要会保留。")

    def _on_worker_finished(self) -> None:
        self.worker = None
        self._refresh_status_bar()

    # ---------------------------------------------------------------- 关闭

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "任务进行中",
                "正在处理录音，现在退出会中断处理。\n\n"
                "已完成的文件会保留，剩余文件需要重新开始。确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        event.accept()
