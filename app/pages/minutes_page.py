"""第三步：文字稿生成会议纪要文件。

这一步需要联网与云端 API Key，是三步中唯一依赖外部服务的环节。
与转写分开之后有两个好处：
  1. 离线环境下可以先完成前两步，联网后再做这一步；
  2. 同一份文字稿可以反复重跑（换模型、改提示词、补全已知信息），
     而不必重新转写 —— 转写一次可能要几十分钟。
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QLabel

from mmtools.config import Config
from mmtools.pipeline import resolve_form_template

from ..widgets.file_queue import FileQueuePanel
from ..widgets.step_page import StepPage
from ..worker import SummarizeWorker

TRANSCRIPT_SUFFIXES = {".md"}

# 转写稿里每段的表头形如 **[00:12]** **说话人1**
_SEGMENT_RE = re.compile(r"^\*\*\[\d{1,2}:\d{2}(?::\d{2})?\]\*\*")


class MinutesPage(StepPage):
    """纪要页。"""

    step_title = "③ 录音文字生成会议文件"
    step_subtitle = (
        "把文字稿交给云端大模型整理成会议纪要，并按单位模板生成 Word 文件。"
        "这一步需要联网。文字稿会发送到云端，音频不会。"
    )

    def run_label(self) -> str:
        return "开始生成纪要"

    def build_queue(self):
        return FileQueuePanel(
            suffixes=TRANSCRIPT_SUFFIXES,
            drop_title="把文字稿拖到这里",
            drop_hint="通常是 workspace/transcript/ 下的 .transcript.md 文件",
            pick_title="选择文字稿",
            pick_filter="文字稿 (*.transcript.md *.md);;所有文件 (*)",
            info_provider=self._count_segments,
        )

    def _count_segments(self, path: Path) -> str:
        """统计文字稿的段落数，让用户对输入规模有概念。"""
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        count = sum(1 for line in text.splitlines() if _SEGMENT_RE.match(line.strip()))
        if count:
            return f"{count} 段"
        size_kb = path.stat().st_size / 1024
        return f"{size_kb:.0f} KB"

    def check_environment(self) -> tuple[bool, str]:
        try:
            cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            return False, f"配置读取失败：{exc}"

        problems: list[str] = []

        # 密钥：这一步唯一的外部依赖
        try:
            key, prov = cfg.provider()
            token = cfg.api_key()
            if token:
                key_line = f"云端平台就绪：{prov.get('label', key)}（{cfg.api_key_source()}）"
            else:
                problems.append(
                    f"尚未配置 {prov.get('label', key)} 的 API Key，"
                    f"无法生成纪要。可在「设置」页配置密钥文件路径。"
                )
                key_line = ""
        except Exception as exc:  # noqa: BLE001
            problems.append(f"云端平台配置有误：{exc}")
            key_line = ""

        # 模板
        try:
            template = resolve_form_template(cfg)
            if template:
                tpl_line = f"将按单位模板生成：{template.name}"
            else:
                tpl_line = "未配置单位模板，将生成通用排版的 Word 纪要。"
        except Exception as exc:  # noqa: BLE001
            problems.append(f"模板不可用：{exc}，将改用通用排版。")
            tpl_line = ""

        known = cfg.get("template", "known") or {}
        filled = [k for k, v in known.items() if v]
        if not filled:
            tpl_line += (
                "\n提示：未填写项目名称与单位全称，纪要里这些字段会是「（文字稿未提及）」。"
                "在「设置」页填好可显著提升可用性。"
            )

        if problems:
            return False, "\n".join(problems)

        return True, "\n".join(x for x in (key_line, tpl_line) if x)

    def create_worker(self, files: list[Path]):
        cfg = Config.load()
        try:
            template = resolve_form_template(cfg)
        except Exception:
            template = None
        return SummarizeWorker(
            cfg,
            files,
            parent=self,
            provider=None,
            known=dict(cfg.get("template", "known") or {}),
            form_template=template,
        )

    def eta_text(self, files: list[Path]) -> str:
        if not files:
            return ""
        return f"共 {len(files)} 份文字稿，每份约 20 秒"

    def add_files(self, paths: list[str]) -> int:
        """供外部（转写页「送到纪要」）调用。"""
        if self.queue is None:
            return 0
        added = self.queue.add_paths(paths)
        if added:
            self.refresh_environment()
        return added
