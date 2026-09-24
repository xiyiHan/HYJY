"""主窗口：左侧导航 + 三步工作流。

界面按用户的实际工作顺序组织：先录、再转写、最后生成纪要。
三步之间通过「送到下一步」衔接，但也可以单独使用 ——
例如手上已有录音文件时直接进第二步，已有文字稿时直接进第三步。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from mmtools.config import Config

from .pages.help_page import HelpPage
from .pages.library_page import LibraryPage
from .pages.minutes_page import MinutesPage
from .pages.record_page import RecordPage
from .pages.settings_page import SettingsPage
from .pages.transcribe_page import TranscribePage

PAGE_RECORD = 0
PAGE_TRANSCRIBE = 1
PAGE_MINUTES = 2
PAGE_LIBRARY = 3
PAGE_SETTINGS = 4
PAGE_HELP = 5


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("会议纪要工具")
        self.resize(1060, 740)
        self.setMinimumSize(880, 600)

        self.cfg = Config.load()
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
        for label in ("① 录制", "② 转写", "③ 纪要", "文件库", "设置", "帮助"):
            self.nav.addItem(QListWidgetItem(label))
        self.nav.setCurrentRow(PAGE_RECORD)
        self.nav.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.record_page = RecordPage()
        self.transcribe_page = TranscribePage()
        self.minutes_page = MinutesPage()
        self.library_page = LibraryPage()
        self.settings_page = SettingsPage()
        self.help_page = HelpPage()
        for page in (
            self.record_page,
            self.transcribe_page,
            self.minutes_page,
            self.library_page,
            self.settings_page,
            self.help_page,
        ):
            self.stack.addWidget(page)

        # 三步之间的衔接
        self.record_page.send_to_next.connect(self._record_to_transcribe)
        self.transcribe_page.go_next_requested.connect(self._transcribe_to_minutes)

        # 文件库可以直接把文件送进对应步骤
        self.library_page.send_to_transcribe.connect(self._library_to_transcribe)
        self.library_page.send_to_minutes.connect(self._library_to_minutes)

        # 任意一步完成后刷新文件库与状态栏
        self.transcribe_page.go_next_requested.connect(lambda *_: self.library_page.reload())
        self.minutes_page.go_next_requested.connect(lambda *_: self.library_page.reload())

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

            parts = [
                f"① 录音：{'就绪' if self._recorder_ready() else '不可用'}",
                f"② 转写：{engine}",
                f"③ 纪要：{prov.get('label', '')}（{'已配置密钥' if has_key else '未配置密钥'}）",
            ]
            self.lbl_env.setText("    ".join(parts))
            self.lbl_env.setObjectName("muted" if has_key else "warn")
            self.lbl_env.style().unpolish(self.lbl_env)
            self.lbl_env.style().polish(self.lbl_env)
        except Exception as exc:  # noqa: BLE001
            self.lbl_env.setText(f"配置异常：{exc}")

    def _recorder_ready(self) -> bool:
        try:
            from mmtools.recorder import check_recording_ready

            ok, _ = check_recording_ready()
            return ok
        except Exception:
            return False

    def _on_nav_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        # 这几页每次进入都重新读配置与刷新，避免显示过期状态
        if row == PAGE_SETTINGS:
            self.settings_page.cfg = Config.load()
            self.settings_page.load_values()
        elif row == PAGE_LIBRARY:
            self.library_page.reload()
        elif row == PAGE_HELP:
            self._refresh_status_bar()
        if hasattr(page, "refresh_environment"):
            try:
                page.refresh_environment()
            except Exception:
                pass
        if hasattr(page, "reload_files"):
            try:
                page.reload_files()
            except Exception:
                pass

    # ---------------------------------------------------------------- 衔接

    def _record_to_transcribe(self, paths: list[str]) -> None:
        added = self.transcribe_page.add_files(paths)
        self.nav.setCurrentRow(PAGE_TRANSCRIBE)
        self.transcribe_page.append_log(f"已从「录制」页接收 {added} 个文件")

    def _library_to_transcribe(self, paths: list[str]) -> None:
        added = self.transcribe_page.add_files(paths)
        self.nav.setCurrentRow(PAGE_TRANSCRIBE)
        self.transcribe_page.append_log(f"已从文件库接收 {added} 个录音文件")

    def _library_to_minutes(self, paths: list[str]) -> None:
        added = self.minutes_page.add_files(paths)
        self.nav.setCurrentRow(PAGE_MINUTES)
        self.minutes_page.append_log(f"已从文件库接收 {added} 份文字稿")

    def _transcribe_to_minutes(self, outputs: list[str]) -> None:
        transcripts = [p for p in outputs if str(p).endswith((".transcript.md", ".md"))]
        if not transcripts:
            return
        self.minutes_page.add_files(transcripts)
        self.minutes_page.append_log(
            f"转写完成，{len(transcripts)} 份文字稿已就绪，可直接生成纪要。"
        )
        self.nav.setCurrentRow(PAGE_MINUTES)

    # ---------------------------------------------------------------- 关闭

    def closeEvent(self, event) -> None:  # noqa: N802
        busy: list[str] = []
        worker = getattr(self.record_page, "worker", None)
        if worker is not None and worker.isRunning():
            busy.append("录音")
        for page in (self.transcribe_page, self.minutes_page):
            w = getattr(page, "_worker", None)
            if w is not None and w.isRunning():
                busy.append(page.step_title)

        if not busy:
            event.accept()
            return

        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            "任务进行中",
            f"以下任务正在运行：{'、'.join(busy)}。\n\n"
            f"现在退出会中断它们。已完成的产物会保留，未完成的需要重新开始。\n"
            f"确定退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(5000)
        for page in (self.transcribe_page, self.minutes_page):
            w = getattr(page, "_worker", None)
            if w is not None and w.isRunning():
                w.cancel()
                w.wait(5000)
        event.accept()
