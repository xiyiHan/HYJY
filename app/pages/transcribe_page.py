"""第二步：录音转文字。

纯本地操作，不需要联网。可以在没有网络的环境里先把文字稿全部转出来，
之后再联网生成纪要。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel

from mmtools.config import Config
from mmtools.transcribers import available_backends, format_clock, probe_duration

from ..widgets.file_queue import FileQueuePanel
from ..widgets.step_page import StepPage
from ..worker import TranscribeWorker

AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".mp4", ".amr"}

# 实测约 2.8 倍速；模型加载的固定开销约 100 秒，与音频长短无关
SPEED_FACTOR = 2.8
MODEL_LOAD_SECONDS = 100


class TranscribePage(StepPage):
    """转写页。"""

    step_title = "② 录音转文字"
    step_subtitle = (
        "在本机把录音转成文字稿，音频全程不出本机。这一步不需要联网，"
        "可以在离线环境下先把所有录音转完，之后再统一生成纪要。"
    )

    def run_label(self) -> str:
        return "开始转写"

    def __init__(self, parent=None):
        self._durations: dict[str, float | None] = {}
        super().__init__(parent)

    def build_queue(self):
        return FileQueuePanel(
            suffixes=AUDIO_SUFFIXES,
            drop_title="把录音文件拖到这里",
            drop_hint="支持 m4a / mp3 / wav 等格式，可拖入多个文件或整个文件夹",
            pick_title="选择录音文件",
            pick_filter="音频文件 (*.m4a *.mp3 *.wav *.aac *.flac *.ogg *.wma *.mp4 *.amr);;所有文件 (*)",
            info_provider=self._probe,
        )

    def _probe(self, path: Path) -> str:
        try:
            secs = probe_duration(path)
        except Exception:
            secs = None
        self._durations[str(path)] = secs
        return format_clock(secs) if secs else "—"

    def check_environment(self) -> tuple[bool, str]:
        try:
            cfg = Config.load()
        except Exception as exc:  # noqa: BLE001
            return False, f"配置读取失败：{exc}"

        backend = cfg.backend_name
        backends = available_backends()
        ok, note = backends.get(backend, (False, "未知引擎"))

        if not ok:
            return False, (
                f"当前选的转写引擎 {backend} 不可用：{note}\n"
                f"可在「设置」页切换引擎，或按提示安装依赖。"
            )

        conf = cfg.backend_conf(backend)
        detail = f"转写引擎就绪：{backend}（模型 {conf.get('model', '?')}）"
        if conf.get("spk_model"):
            detail += "，已启用说话人分离"
        if backend == "funasr" and not (conf.get("hotword") or "").strip():
            detail += "\n提示：热词为空。按会议主题填热词能明显减少专有名词识别错误。"
        return True, detail

    def create_worker(self, files: list[Path]):
        return TranscribeWorker(Config.load(), files, parent=self)

    def eta_text(self, files: list[Path]) -> str:
        total = 0.0
        unknown = 0
        for f in files:
            secs = self._durations.get(str(f))
            if secs:
                total += secs
            else:
                unknown += 1
        if not total:
            return ""
        estimate = MODEL_LOAD_SECONDS + total / SPEED_FACTOR
        text = f"总时长 {format_clock(total)}，预计需要 {format_clock(estimate)}"
        if unknown:
            text += f"（{unknown} 个文件时长未知，未计入）"
        return text

    def add_files(self, paths: list[str]) -> int:
        """供外部（录制页「送到转写」）调用。"""
        if self.queue is None:
            return 0
        added = self.queue.add_paths(paths)
        if added:
            self.refresh_environment()
        return added
