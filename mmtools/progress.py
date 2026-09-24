"""业务层的进度回报出口。

设计意图：业务代码（转写、纪要、模板填充）只调用 ``Progress`` 的方法，
不关心接收方是终端还是图形界面。这样：

  - 命令行版传一个打印回调，行为与之前完全一致；
  - 图形界面版传一个信号发射器，界面实时刷新；
  - 业务层代码里不出现任何界面相关的东西，界面出问题也不影响命令行使用。

阶段名称（stage）约定：
    start          整个任务开始
    transcribe     转写中（开始/结束各报一次）
    summarize      生成纪要中
    fill           填充模板与导出
    done           单个文件处理完成
    all_done       全部文件处理完成
    error          出错
"""

from __future__ import annotations

from typing import Any, Callable

# 回调签名：(事件类型, 数据字典)
# 事件类型为 "log" 或 "progress"
ProgressCallback = Callable[[str, dict], None]


class CancelledError(RuntimeError):
    """用户主动取消任务。

    这是一个"正常"的终止，不是故障 —— 上层应当区分处理：
    已完成的产物要保留，界面上提示"已取消"而不是"处理失败"。
    """


class Progress:
    """把进度同时输出到终端与回调，并承载取消信号。"""

    def __init__(
        self,
        callback: ProgressCallback | None = None,
        echo: bool = True,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self._callback = callback
        self._echo = echo
        self._cancel_check = cancel_check

    # ---------- 文本日志 ----------

    def log(self, message: str = "") -> None:
        """普通日志行。终端版会打印，界面版显示在日志区。"""
        if self._echo:
            print(message)
        if self._callback is not None:
            self._callback("log", {"message": message})

    # ---------- 结构化进度 ----------

    def report(
        self,
        stage: str,
        message: str = "",
        current: int = 0,
        total: int = 0,
        **extra: Any,
    ) -> None:
        """汇报一个结构化进度事件。

        current/total 用于批量任务的批次进度；extra 可携带任意字段
        （例如已完成文件数、预计剩余秒数），界面按需取用。
        """
        if self._callback is None:
            return
        payload: dict[str, Any] = {
            "stage": stage,
            "message": message,
            "current": current,
            "total": total,
        }
        payload.update(extra)
        self._callback("progress", payload)

    # ---------- 取消 ----------

    def should_cancel(self) -> bool:
        return bool(self._cancel_check is not None and self._cancel_check())

    def check_cancel(self) -> None:
        """在安全点检查是否需要取消。

        只能放在可以安全中断的位置（文件之间、阶段之间）。转写是单个阻塞调用，
        中途无法打断，因此一次转写的等待时间无法被取消缩短 —— 这一点应当在
        界面上如实说明，不能让用户以为点了取消就立刻停。
        """
        if self.should_cancel():
            raise CancelledError("任务已被用户取消")

    def child(self, echo: bool | None = None) -> "Progress":
        """派生一个共用同一回调与取消信号、但可单独控制是否打印的 Progress。"""
        return Progress(
            self._callback,
            self._echo if echo is None else echo,
            self._cancel_check,
        )


class NullProgress(Progress):
    """静默版，用于测试或需要完全无输出的场景。"""

    def __init__(self) -> None:
        super().__init__(callback=None, echo=False)
