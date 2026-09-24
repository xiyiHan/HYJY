"""文件库：汇总本机已有的录音、文字稿与会议纪要。

三步分开之后，用户需要有一个地方统览"我手上已经有什么"——
比如想确认某场会议转写完了没有、只看过文字稿还没生成纪要。
这一页就是干这个的，同时提供"送入下一步"的入口。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mmtools.config import Config

from ..utils import format_size, open_file, open_in_explorer

# 类型标识：用于筛选与后续分派
KIND_AUDIO = "录音"
KIND_TRANSCRIPT = "文字稿"
KIND_MINUTES = "会议纪要"
KIND_OTHER = "其他"

_AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".wma", ".amr"}


class LibraryPage(QWidget):
    """文件库。"""

    send_to_transcribe = Signal(list)   # 把录音送去转写
    send_to_minutes = Signal(list)      # 把文字稿送去生成纪要
    send_to_edit = Signal(list)         # 送去编辑结构化数据（预留）

    COL_NAME = 0
    COL_KIND = 1
    COL_SIZE = 2
    COL_TIME = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[Path, str]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("文件库")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)

        head.addWidget(QLabel("类型"))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(["全部", KIND_AUDIO, KIND_TRANSCRIPT, KIND_MINUTES])
        self.cmb_filter.currentTextChanged.connect(lambda _: self.apply_filter())
        head.addWidget(self.cmb_filter)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.reload)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("muted")
        self.lbl_summary.setWordWrap(True)
        root.addWidget(self.lbl_summary)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件", "类型", "大小", "修改时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.doubleClicked.connect(lambda _: self._open_file())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (self.COL_KIND, self.COL_SIZE, self.COL_TIME):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        action = QHBoxLayout()
        self.btn_open = QPushButton("打开文件")
        self.btn_open.clicked.connect(self._open_file)
        self.btn_folder = QPushButton("打开所在文件夹")
        self.btn_folder.clicked.connect(self._open_folder)
        self.btn_transcribe = QPushButton("送去转写")
        self.btn_transcribe.clicked.connect(self._send_transcribe)
        self.btn_minutes = QPushButton("送去生成纪要")
        self.btn_minutes.clicked.connect(self._send_minutes)
        for b in (self.btn_open, self.btn_folder, self.btn_transcribe, self.btn_minutes):
            action.addWidget(b)
        action.addStretch(1)
        root.addLayout(action)

        self.reload()

    # ---------------------------------------------------------------- 数据

    def reload(self) -> None:
        try:
            cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            self.lbl_summary.setText(f"配置读取失败：{exc}")
            return

        rows: list[tuple[Path, str]] = []
        for key, kind in (
            ("audio", KIND_AUDIO),
            ("transcript", KIND_TRANSCRIPT),
            ("minutes", KIND_MINUTES),
        ):
            try:
                directory = cfg.path(key)
            except Exception:
                continue
            if not directory.exists():
                continue
            for f in directory.iterdir():
                if not f.is_file() or f.name.startswith("_"):
                    continue
                suffix = f.suffix.lower()
                if kind == KIND_AUDIO and suffix not in _AUDIO_SUFFIXES:
                    continue
                if kind == KIND_TRANSCRIPT and not f.name.endswith((".md", ".txt")):
                    continue
                if kind == KIND_MINUTES and suffix not in (".docx", ".md", ".json"):
                    continue
                rows.append((f, kind))

        rows.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
        self._rows = rows
        self.apply_filter()

    def apply_filter(self) -> None:
        wanted = self.cmb_filter.currentText()
        visible = [(p, k) for p, k in self._rows if wanted == "全部" or k == wanted]

        self.table.setRowCount(len(visible))
        for row, (path, kind) in enumerate(visible):
            name = QTableWidgetItem(path.name)
            name.setToolTip(str(path))
            name.setData(Qt.ItemDataRole.UserRole, str(path))
            name.setData(Qt.ItemDataRole.UserRole + 1, kind)
            self.table.setItem(row, self.COL_NAME, name)

            kitem = QTableWidgetItem(kind)
            kitem.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, self.COL_KIND, kitem)

            sitem = QTableWidgetItem(format_size(path))
            sitem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, self.COL_SIZE, sitem)

            mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")
            self.table.setItem(row, self.COL_TIME, QTableWidgetItem(mtime))

        counts = {
            KIND_AUDIO: sum(1 for _, k in self._rows if k == KIND_AUDIO),
            KIND_TRANSCRIPT: sum(1 for _, k in self._rows if k == KIND_TRANSCRIPT),
            KIND_MINUTES: sum(1 for _, k in self._rows if k == KIND_MINUTES),
        }
        self.lbl_summary.setText(
            f"共 {len(self._rows)} 个文件："
            f"录音 {counts[KIND_AUDIO]}、文字稿 {counts[KIND_TRANSCRIPT]}、"
            f"会议纪要 {counts[KIND_MINUTES]}。"
            f"双击打开文件；选中后可送入对应步骤。"
        )

    # ---------------------------------------------------------------- 选择

    def _selected(self) -> list[tuple[Path, str]]:
        out: list[tuple[Path, str]] = []
        for idx in self.table.selectionModel().selectedRows():
            item = self.table.item(idx.row(), self.COL_NAME)
            if item is None:
                continue
            value = item.data(Qt.ItemDataRole.UserRole)
            kind = item.data(Qt.ItemDataRole.UserRole + 1)
            if value:
                out.append((Path(str(value)), str(kind)))
        return out

    def _open_file(self) -> None:
        for path, _ in self._selected():
            open_file(path)
            return  # 只打开第一个，避免一次弹出十几份文档

    def _open_folder(self) -> None:
        selected = self._selected()
        if selected:
            open_in_explorer(selected[0][0], select=True)
        else:
            try:
                open_in_explorer(Config.load().path("minutes"))
            except Exception:
                pass

    def _send_transcribe(self) -> None:
        paths = [str(p) for p, k in self._selected() if k == KIND_AUDIO]
        if not paths:
            self.lbl_summary.setText("请先选中「录音」类型的文件，再点「送去转写」。")
            return
        self.send_to_transcribe.emit(paths)

    def _send_minutes(self) -> None:
        paths = [str(p) for p, k in self._selected() if k == KIND_TRANSCRIPT]
        if not paths:
            self.lbl_summary.setText("请先选中「文字稿」类型的文件，再点「送去生成纪要」。")
            return
        self.send_to_minutes.emit(paths)
