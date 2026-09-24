"""后台任务：把业务层的流程放到子线程里跑，通过信号回报进度。

为什么要单独一个线程：一次转写要十几分钟到几十分钟。如果直接在界面线程里
调用，整个窗口会假死，Windows 还会在标题栏显示"无响应"。

设计要点：
  - 界面层通过信号接收进度，业务层完全不知道界面的存在
  - 取消采用协作式：在文件/阶段边界检查标志位。转写是单个阻塞调用，
    中途打断不了，这一点必须在界面上如实告知用户
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from mmtools.config import Config
from mmtools.pipeline import run_pipeline
from mmtools.progress import CancelledError, Progress
from mmtools.transcribers import prepare_runtime_env


class ProcessWorker(QThread):
    """在子线程里执行"转写 + 生成纪要"的完整流程。"""

    log_line = Signal(str)          # 一行日志文本
    progress = Signal(dict)         # 结构化进度事件
    succeeded = Signal(list)        # 全部完成，携带 PipelineResult 列表
    failed = Signal(str)            # 出错，携带错误文本
    cancelled = Signal()            # 用户取消（已完成的数量由 progress 事件给出）

    def __init__(
        self,
        cfg: Config,
        audios: list[Path],
        form_template: Path | None = None,
        known: dict | None = None,
        backend: str | None = None,
        provider: str | None = None,
        skip_summary: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.audios = list(audios)
        self.form_template = form_template
        self.known = known or {}
        self.backend = backend
        self.provider = provider
        self.skip_summary = skip_summary
        self._cancel = threading.Event()

    # ---------- 对外接口 ----------

    def cancel(self) -> None:
        """请求取消。在下一个检查点生效，不是立刻停止。"""
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    # ---------- 线程主体 ----------

    def run(self) -> None:  # noqa: D102
        def callback(event: str, payload: dict) -> None:
            if event == "log":
                self.log_line.emit(payload.get("message", ""))
            else:
                self.progress.emit(payload)

        progress = Progress(
            callback=callback,
            echo=False,
            cancel_check=self._cancel.is_set,
        )

        try:
            results = run_pipeline(
                self.cfg,
                self.audios,
                backend=self.backend,
                provider=self.provider,
                skip_summary=self.skip_summary,
                verbose=False,
                form_template=self.form_template,
                known=self.known,
                progress=progress,
            )
        except CancelledError:
            # 已完成文件的产物已经落盘，这里只需通知界面
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001
            detail = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            self.failed.emit(detail)
            return

        self.succeeded.emit(results)


class CheckWorker(QThread):
    """在子线程里跑环境自检，避免联网检测时界面卡顿。"""

    finished_ok = Signal(str)   # 自检文本
    failed = Signal(str)

    def __init__(self, cfg: Config, online: bool = False, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.online = online

    def run(self) -> None:  # noqa: D102
        lines: list[str] = []
        try:
            prepare_runtime_env(self.cfg)
            lines.append("【目录】")
            for p in self.cfg.ensure_dirs():
                lines.append(f"  {p}")

            lines.append("")
            lines.append("【转写引擎】")
            from mmtools.transcribers import available_backends

            for name, (ok, note) in available_backends().items():
                mark = "可用" if ok else "不可用"
                current = "  ← 当前选用" if name == self.cfg.backend_name else ""
                lines.append(f"  [{mark}] {name}{current}")
                if not ok:
                    lines.append(f"          {note}")

            lines.append("")
            lines.append("【录音模块】")
            from mmtools.recorder import check_recording_ready

            ok, note = check_recording_ready()
            lines.append(f"  [{'可用' if ok else '不可用'}] {note}")

            lines.append("")
            lines.append("【云端平台】")
            key, prov = self.cfg.provider()
            lines.append(f"  当前平台：{prov.get('label', key)}")
            lines.append(f"  接口地址：{prov.get('base_url')}")
            lines.append(f"  模型名称：{prov.get('model')}")
            lines.append(f"  API Key ：{self.cfg.api_key_source()}")
            lines.append(f"  合规说明：{prov.get('compliance', '（未注明）')}")

            lines.append("")
            lines.append("【模板】")
            tpl = (self.cfg.get("template", "path") or "").strip()
            if tpl:
                exists = Path(tpl).exists()
                lines.append(f"  [{'就绪' if exists else '不存在'}] {tpl}")
            else:
                lines.append("  未配置模板（将使用通用排版）")

            if self.online:
                lines.append("")
                lines.append("【在线连通性测试】")
                from mmtools.summarizer import CloudLLM

                try:
                    reply = CloudLLM(self.cfg).probe()
                    lines.append(f"  [通过] 模型回复：{reply[:30]}")
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"  [失败] {exc}")

            self.finished_ok.emit("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
