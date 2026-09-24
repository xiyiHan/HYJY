"""结果页：查看已生成的文件，一键打开。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mmtools.transcribers import format_clock


def open_in_explorer(path: Path, select: bool = False) -> None:
    """在资源管理器中打开文件或目录。"""
    path = Path(path)
    try:
        if sys.platform == "win32":
            if select and path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                target = path if path.is_dir() else path.parent
                os.startfile(str(target))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


class ResultPage(QWidget):
    """展示处理结果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._records: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("处理结果")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_open_docx = QPushButton("打开 Word 纪要")
        self.btn_open_docx.clicked.connect(lambda: self._open("docx"))
        self.btn_open_json = QPushButton("打开结构化数据")
        self.btn_open_json.clicked.connect(lambda: self._open("json"))
        self.btn_open_dir = QPushButton("打开所在文件夹")
        self.btn_open_dir.clicked.connect(lambda: self._open("folder"))
        for b in (self.btn_open_docx, self.btn_open_json, self.btn_open_dir):
            b.setEnabled(False)
            head.addWidget(b)
        root.addLayout(head)

        self.hint = QLabel("还没有处理结果。回到任务页处理一个录音后，这里会列出生成的文件。")
        self.hint.setObjectName("muted")
        root.addWidget(self.hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentRowChanged.connect(self._on_select)
        splitter.addWidget(self.list)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("logView")
        self.detail.setPlaceholderText("选中左侧的文件查看详情")
        splitter.addWidget(self.detail)
        splitter.setSizes([260, 520])
        root.addWidget(splitter, 1)

    # ---------------------------------------------------------------- 数据

    def add_results(self, results: list) -> None:
        for res in results:
            audio = Path(getattr(res, "audio", ""))
            transcript = getattr(res, "transcript_path", None)
            minutes = getattr(res, "minutes_path", None)
            docx = getattr(res, "docx_path", None)
            record = {
                "audio": str(audio),
                "transcript": str(transcript) if transcript else "",
                "minutes": str(minutes) if minutes else "",
                "docx": str(docx) if docx else "",
                "json": str(Path(minutes).with_suffix(".json")) if minutes and str(minutes).endswith(".docx") else "",
                "transcript_obj": getattr(res, "transcript", None),
            }
            self._records.append(record)
            self._refresh_list()

    def _refresh_list(self) -> None:
        self.list.clear()
        for rec in self._records:
            name = Path(rec["audio"]).name
            item = QListWidgetItem(name)
            item.setToolTip(rec["audio"])
            self.list.addItem(item)
        if self._records:
            self.hint.setText(f"共 {len(self._records)} 条处理记录")
            self.list.setCurrentRow(0)

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def _on_select(self, row: int) -> None:
        rec = self._current()
        if rec is None:
            return
        lines = [f"音频：{rec['audio']}", ""]
        t = rec.get("transcript_obj")
        if t is not None:
            if getattr(t, "duration", None):
                lines.append(f"音频时长：{format_clock(t.duration)}")
            lines.append(f"转写耗时：{format_clock(getattr(t, 'elapsed', 0))}")
            lines.append(f"转写引擎：{getattr(t, 'backend', '')} / {getattr(t, 'model', '')}")
            speakers = getattr(t, "speakers", []) or []
            lines.append(f"说话人数：{len(speakers)}" + (f"（{'、'.join(speakers)}）" if speakers else ""))
            lines.append("")

        for label, key in (
            ("文字稿", "transcript"),
            ("会议纪要", "minutes"),
            ("Word 版", "docx"),
            ("结构化数据", "json"),
        ):
            path = rec.get(key)
            if not path:
                continue
            p = Path(path)
            exists = p.exists()
            size = f"{p.stat().st_size / 1024:.0f} KB" if exists else "未生成"
            lines.append(f"{label}：{p.name if exists else '（未生成）'}  {size}")
            if exists:
                lines.append(f"    {p}")

        lines.append("")
        lines.append("提醒：纪要为机器生成，归档前请人工复核参会人员、机构全称与金额单位。")
        self.detail.setPlainText("\n".join(lines))

        # 按钮可用性：Word 版优先，没有 docx 时退回可读的 Markdown 纪要
        docx_path = rec.get("docx") or ""
        minutes_path = rec.get("minutes") or ""
        json_path = rec.get("json") or ""
        docx_ok = bool(docx_path) and Path(docx_path).exists()
        minutes_ok = bool(minutes_path) and Path(minutes_path).exists()

        self.btn_open_docx.setEnabled(docx_ok or minutes_ok)
        self.btn_open_docx.setText("打开 Word 纪要" if docx_ok else "打开纪要")
        self.btn_open_json.setEnabled(bool(json_path) and Path(json_path).exists())
        self.btn_open_dir.setEnabled(bool(rec.get("audio")) and Path(rec["audio"]).exists())

    def _open(self, kind: str) -> None:
        rec = self._current()
        if rec is None:
            return
        if kind == "folder":
            target = rec.get("docx") or rec.get("minutes") or rec.get("transcript") or rec.get("audio")
            if target:
                open_in_explorer(Path(target), select=bool(rec.get("docx")))
            return
        if kind == "docx":
            target = rec.get("docx") or rec.get("minutes")
        else:
            target = rec.get(kind)
        if target and Path(target).exists():
            open_in_explorer(Path(target))
