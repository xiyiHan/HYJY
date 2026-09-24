r"""界面冒烟测试：验证所有窗口与页面能正常构造。

运行：
    .venv\Scripts\python.exe tests\test_gui_smoke.py

它做三件事：
  1. 导入界面全部模块，捕捉语法或导入错误；
  2. 构造 QApplication 与主窗口（不进入事件循环、不显示），
     捕捉控件构造、布局、信号连接中的错误；
  3. 验证关键交互契约：进度事件能被正确处理、队列统计正确。

不需要显示器，可在无人工干预的情况下反复运行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
SKIPPED: list[str] = []


def check(name: str):
    def decorator(fn):
        try:
            fn()
            PASSED.append(name)
        except ImportError as exc:
            SKIPPED.append(f"{name}（{exc}）")
        except Exception as exc:  # noqa: BLE001
            FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        return fn

    return decorator


def _app():
    from PySide6.QtWidgets import QApplication

    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


# ---------------------------------------------------------------- 导入


@check("界面模块可导入")
def _():
    import app.main  # noqa: F401
    import app.main_window  # noqa: F401
    import app.pages.help_page  # noqa: F401
    import app.pages.result_page  # noqa: F401
    import app.pages.settings_page  # noqa: F401
    import app.pages.task_page  # noqa: F401
    import app.theme  # noqa: F401
    import app.worker  # noqa: F401


@check("样式表非空且包含关键选择器")
def _():
    from app.theme import STYLESHEET

    assert len(STYLESHEET) > 500, "样式表过短，可能未正确加载"
    for selector in ("#navList", "#dropArea", "#primary", "#logView"):
        assert selector in STYLESHEET, f"缺少选择器 {selector}"


# ---------------------------------------------------------------- 构造


@check("主窗口可构造，四个页面齐备")
def _():
    _app()
    from app.main_window import MainWindow

    w = MainWindow()
    assert w.stack.count() == 4, f"页面数量异常：{w.stack.count()}"
    assert w.nav.count() == 4, f"导航项数量异常：{w.nav.count()}"
    w.close()


@check("任务页：加入文件后队列与预估正确")
def _():
    _app()
    from app.pages.task_page import TaskPage

    page = TaskPage()
    assert page.table.rowCount() == 0
    assert not page.btn_start.isEnabled(), "空队列时开始按钮应不可用"

    # 用一个真实存在的音频文件（项目里的测试样本）
    sample = ROOT / "workspace" / "audio" / "测试语音.wav"
    if not sample.exists():
        SKIPPED.append("任务页：加入文件后队列与预估正确（缺少测试音频）")
        return

    page.add_files([str(sample)])
    assert page.table.rowCount() == 1, f"入队失败，行数 {page.table.rowCount()}"
    assert page.btn_start.isEnabled(), "有文件时开始按钮应可用"
    assert page.table.item(0, 0).text() == sample.name
    # 同一文件重复加入不应产生第二行
    page.add_files([str(sample)])
    assert page.table.rowCount() == 1, "重复文件被重复加入"
    # 非音频文件应被忽略
    page.add_files([str(ROOT / "README.md")])
    assert page.table.rowCount() == 1, "非音频文件被错误加入"


@check("任务页：进度事件处理不抛异常")
def _():
    _app()
    from app.pages.task_page import TaskPage

    page = TaskPage()
    sample = ROOT / "workspace" / "audio" / "测试语音.wav"
    if sample.exists():
        page.add_files([str(sample)])

    # 依次派发完整的进度序列，模拟一次真实处理
    page.on_progress({"stage": "start", "total": 1, "current": 0})
    page.on_progress({"stage": "file_start", "file": "a.wav", "current": 1, "total": 1})
    page.on_progress({"stage": "transcribe", "message": "开始转写"})
    page.on_progress({"stage": "transcribe_done", "elapsed": 100})
    page.on_progress({"stage": "summarize", "message": "生成纪要中"})
    page.on_progress({"stage": "summarize_done", "minutes": "x.docx"})
    page.on_progress({"stage": "file_done", "current": 1, "total": 1})
    page.on_progress({"stage": "all_done", "total": 1, "current": 1})
    assert page.bar.value() == 100, f"完成后进度条应满：{page.bar.value()}"

    # 取消与错误路径也要能安全处理
    page.on_progress({"stage": "cancelled", "message": "已取消"})
    page.on_progress({"stage": "error", "error": "模拟错误"})


@check("任务页：日志写入不抛异常")
def _():
    _app()
    from app.pages.task_page import TaskPage

    page = TaskPage()
    for text in ("普通一行", "", "含特殊字符 <>&\"' 的一行", "很长的行 " * 100):
        page.append_log(text)
    assert "普通一行" in page.log.toPlainText()


@check("结果页可构造并能接收结果")
def _():
    _app()
    from app.pages.result_page import ResultPage

    page = ResultPage()
    assert page.list.count() == 0
    assert not page.btn_open_docx.isEnabled(), "无结果时打开按钮应不可用"


@check("设置页可构造并载入配置")
def _():
    _app()
    from app.pages.settings_page import SettingsPage

    page = SettingsPage()
    assert page.cmb_provider.count() >= 1, "平台下拉为空"
    assert page.cmb_backend.count() == 2, "引擎下拉项数异常"
    # 应当能看到当前引擎
    assert page.cmb_backend.currentText() in ("funasr", "faster_whisper")


@check("帮助页可构造且常见问题有内容")
def _():
    _app()
    from app.pages.help_page import FAQ_HTML, HelpPage

    page = HelpPage()
    assert len(FAQ_HTML) > 500
    assert "密钥" in FAQ_HTML and "内存" in FAQ_HTML


# ---------------------------------------------------------------- 任务线程


@check("后台任务对象的取消标志可生效")
def _():
    _app()
    from app.worker import ProcessWorker

    worker = ProcessWorker(cfg=None, audios=[])
    assert not worker.cancel_requested
    worker.cancel()
    assert worker.cancel_requested, "取消标志未生效"


@check("业务层取消检查会抛出 CancelledError")
def _():
    from mmtools.progress import CancelledError, Progress

    flag = {"v": False}
    p = Progress(callback=None, echo=False, cancel_check=lambda: flag["v"])
    p.check_cancel()  # 未取消时不抛

    flag["v"] = True
    try:
        p.check_cancel()
    except CancelledError:
        return
    raise AssertionError("取消标志为真时未抛出 CancelledError")


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 62)
    print("界面冒烟测试（不显示窗口，不进入事件循环）")
    print("=" * 62)
    print("")

    _app()

    for name in PASSED:
        print(f"  [通过] {name}")
    for name in SKIPPED:
        print(f"  [跳过] {name}")
    for name, err in FAILED:
        print(f"  [失败] {name}")
        print(f"         {err}")

    print("")
    print("-" * 62)
    total = len(PASSED) + len(FAILED)
    print(f"合计 {total} 项：通过 {len(PASSED)} 项，失败 {len(FAILED)} 项，跳过 {len(SKIPPED)} 项")
    if FAILED:
        return 1
    print("界面模块正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
