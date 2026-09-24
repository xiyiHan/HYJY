"""可复用的文件队列组件。

「转写」和「纪要」两步的界面结构几乎一样：拖入文件、排队、执行、看状态。
把这一块抽出来共用，避免两处各写一遍、之后改一处忘一处。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

STATUS_WAITING = "等待中"
STATUS_RUNNING = "处理中"
STATUS_DONE = "已完成"
STATUS_FAILED = "失败"
STATUS_SKIPPED = "已跳过"


class DropArea(QFrame):
    """接收拖拽的虚线区域。"""

    paths_dropped = Signal(list)

    def __init__(self, title: str, hint: str, suffixes: set[str], parent=None):
        super().__init__(parent)
        self.suffixes = suffixes
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("dropArea")
        self.setMinimumHeight(82)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = QLabel(title)
        t.setObjectName("dropTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h = QLabel(hint)
        h.setObjectName("dropHint")
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.setWordWrap(True)
        layout.addWidget(t)
        layout.addWidget(h)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        found: list[str] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and f.suffix.lower() in self.suffixes:
                        found.append(str(f))
            elif p.suffix.lower() in self.suffixes:
                found.append(str(p))
        if found:
            self.paths_dropped.emit(found)
        event.acceptProposedAction()


class FileQueuePanel(QWidget):
    """拖拽区 + 工具栏 + 文件清单。"""

    changed = Signal()

    COL_NAME = 0
    COL_INFO = 1
    COL_STATUS = 2

    def __init__(
        self,
        suffixes: set[str],
        drop_title: str,
        drop_hint: str,
        pick_title: str,
        pick_filter: str,
        info_provider: Callable[[Path], str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.suffixes = suffixes
        self.pick_title = pick_title
        self.pick_filter = pick_filter
        self.info_provider = info_provider

        self._files: list[Path] = []
        self._info: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.drop = DropArea(drop_title, drop_hint, suffixes)
        self.drop.paths_dropped.connect(self.add_paths)
        root.addWidget(self.drop)

        bar = QHBoxLayout()
        self.btn_pick = QPushButton("选择文件")
        self.btn_pick.clicked.connect(self._pick)
        self.btn_add_dir = QPushButton("从文件夹添加")
        self.btn_add_dir.clicked.connect(self._pick_dir)
        self.btn_remove = QPushButton("移除选中")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear)
        for b in (self.btn_pick, self.btn_add_dir, self.btn_remove, self.btn_clear):
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_summary = QLabel("队列为空")
        self.lbl_summary.setObjectName("muted")
        bar.addWidget(self.lbl_summary)
        root.addLayout(bar)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["文件", "信息", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_INFO, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

    # ---------------------------------------------------------------- 数据

    def add_paths(self, paths: list[str], announce: bool = True) -> int:
        added = 0
        for raw in paths:
            p = Path(raw)
            if not p.exists() or not p.is_file():
                continue
            if p.suffix.lower() not in self.suffixes:
                continue
            if p in self._files:
                continue
            self._files.append(p)
            self._info[str(p)] = self.info_provider(p) if self.info_provider else ""
            added += 1
        if added:
            self.refresh()
            self.changed.emit()
            if announce:
                self._announce(added)
        return added

    def _announce(self, added: int) -> None:
        parent = self.window()
        note = getattr(parent, "append_log", None)
        if callable(note):
            note(f"已加入 {added} 个文件")

    def files(self) -> list[Path]:
        return list(self._files)

    def clear(self) -> None:
        if not self._files:
            return
        self._files.clear()
        self._info.clear()
        self.refresh()
        self.changed.emit()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "提示", "请先在列表中选择要移除的文件。")
            return
        for row in rows:
            if 0 <= row < len(self._files):
                self._info.pop(str(self._files[row]), None)
                self._files.pop(row)
        self.refresh()
        self.changed.emit()

    # ---------------------------------------------------------------- 显示

    def refresh(self) -> None:
        self.table.setRowCount(len(self._files))
        for row, path in enumerate(self._files):
            item = QTableWidgetItem(path.name)
            item.setToolTip(str(path))
            self.table.setItem(row, self.COL_NAME, item)

            info = QTableWidgetItem(self._info.get(str(path), ""))
            info.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_INFO, info)

            st = QTableWidgetItem(STATUS_WAITING)
            st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_STATUS, st)

        count = len(self._files)
        if count == 0:
            self.lbl_summary.setText("队列为空")
        else:
            self.lbl_summary.setText(f"共 {count} 个文件")
            if all(not v for v in self._info.values()):
                self.lbl_summary.setText(f"共 {count} 个文件")

    def set_status(self, index: int, status: str) -> None:
        if 0 <= index < self.table.rowCount():
            item = QTableWidgetItem(status)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(index, self.COL_STATUS, item)

    def reset_status(self) -> None:
        for row in range(self.table.rowCount()):
            self.set_status(row, STATUS_WAITING)

    def select_row(self, index: int) -> None:
        if 0 <= index < self.table.rowCount():
            self.table.selectRow(index)

    def set_enabled_editing(self, enabled: bool) -> None:
        for b in (self.btn_pick, self.btn_add_dir, self.btn_remove, self.btn_clear):
            b.setEnabled(enabled)
        self.drop.setAcceptDrops(enabled)

    # ---------------------------------------------------------------- 选择

    def _pick(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, self.pick_title, "", self.pick_filter)
        if files:
            self.add_paths(files)

    def _pick_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not directory:
            return
        found = [
            str(f)
            for f in sorted(Path(directory).rglob("*"))
            if f.is_file() and f.suffix.lower() in self.suffixes
        ]
        if not found:
            QMessageBox.information(self, "没有匹配的文件", f"该文件夹下没有找到可处理的文件。")
            return
        self.add_paths(found)
