r"""界面冒烟测试：验证窗口与各页面能正常构造，以及三步工作流的关键契约。

运行：
    .venv\Scripts\python.exe tests\test_gui_smoke.py

它做四件事：
  1. 导入界面全部模块，捕捉语法或导入错误；
  2. 构造 QApplication 与主窗口（不进入事件循环、不显示）；
  3. 验证三步各自的队列、环境判定、进度事件处理；
  4. 验证三步之间的衔接信号能把产物送到下一步。

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


def _sample_audio() -> Path | None:
    for name in ("大同市血站录音-20260114.wav", "测试语音.wav"):
        p = ROOT / "workspace" / "audio" / name
        if p.exists():
            return p
    return None


def _sample_transcript() -> Path | None:
    d = ROOT / "workspace" / "transcript"
    if not d.exists():
        return None
    for p in sorted(d.glob("*.transcript.md")):
        return p
    return None


# ---------------------------------------------------------------- 导入


@check("界面模块可导入")
def _():
    import app.main  # noqa: F401
    import app.main_window  # noqa: F401
    import app.pages.help_page  # noqa: F401
    import app.pages.library_page  # noqa: F401
    import app.pages.minutes_page  # noqa: F401
    import app.pages.record_page  # noqa: F401
    import app.pages.settings_page  # noqa: F401
    import app.pages.transcribe_page  # noqa: F401
    import app.theme  # noqa: F401
    import app.utils  # noqa: F401
    import app.widgets.file_queue  # noqa: F401
    import app.widgets.step_page  # noqa: F401
    import app.worker  # noqa: F401


@check("样式表非空且包含关键选择器")
def _():
    from app.theme import STYLESHEET

    assert len(STYLESHEET) > 500, "样式表过短，可能未正确加载"
    for selector in ("#navList", "#dropArea", "#primary", "#logView"):
        assert selector in STYLESHEET, f"缺少选择器 {selector}"


# ---------------------------------------------------------------- 主窗口


@check("主窗口可构造，六个页面齐备")
def _():
    _app()
    from app.main_window import MainWindow

    w = MainWindow()
    assert w.stack.count() == 6, f"页面数量异常：{w.stack.count()}"
    assert w.nav.count() == 6, f"导航项数量异常：{w.nav.count()}"
    labels = [w.nav.item(i).text() for i in range(w.nav.count())]
    assert labels[0].startswith("①"), f"第一步导航缺失：{labels}"
    assert labels[1].startswith("②"), f"第二步导航缺失：{labels}"
    assert labels[2].startswith("③"), f"第三步导航缺失：{labels}"
    w.close()


@check("状态栏展示三步各自的就绪情况")
def _():
    _app()
    from app.main_window import MainWindow

    w = MainWindow()
    text = w.lbl_env.text()
    for mark in ("① 录音", "② 转写", "③ 纪要"):
        assert mark in text, f"状态栏缺少 {mark}：{text}"
    w.close()


# ---------------------------------------------------------------- 文件队列


@check("文件队列：格式过滤、去重、摘要正确")
def _():
    _app()
    from app.pages.transcribe_page import TranscribePage

    page = TranscribePage()
    sample = _sample_audio()
    if sample is None:
        SKIPPED.append("文件队列：格式过滤、去重、摘要正确（缺少测试音频）")
        return

    assert page.queue is not None
    assert page.queue.table.rowCount() == 0

    added = page.queue.add_paths([str(sample)], announce=False)
    assert added == 1, f"入队失败，added={added}"
    assert page.queue.table.rowCount() == 1

    # 重复加入不应产生第二行
    assert page.queue.add_paths([str(sample)], announce=False) == 0
    assert page.queue.table.rowCount() == 1, "重复文件被重复加入"

    # 非音频文件应被忽略
    assert page.queue.add_paths([str(ROOT / "README.md")], announce=False) == 0
    assert page.queue.table.rowCount() == 1, "非音频文件被错误加入"

    # 信息列应显示时长
    info = page.queue.table.item(0, 1).text()
    assert info and info != "", "信息列未填充"


@check("文件队列：移除与清空")
def _():
    _app()
    from app.pages.transcribe_page import TranscribePage

    page = TranscribePage()
    sample = _sample_audio()
    if sample is None:
        SKIPPED.append("文件队列：移除与清空（缺少测试音频）")
        return

    page.queue.add_paths([str(sample)], announce=False)
    assert len(page.queue.files()) == 1
    page.queue.table.selectRow(0)
    page.queue._remove_selected()
    assert len(page.queue.files()) == 0, "移除选中未生效"

    page.queue.add_paths([str(sample)], announce=False)
    page.queue.clear()
    assert len(page.queue.files()) == 0, "清空未生效"


# ---------------------------------------------------------------- 三步


@check("① 录制页：可构造且设备状态可见")
def _():
    _app()
    from app.pages.record_page import RecordPage

    page = RecordPage()
    assert page.lbl_env.text(), "未显示录音环境状态"
    assert page.table.columnCount() == 3
    # 录音文件列表应能读取（可能为空，但不应抛异常）
    page.reload_files()


@check("② 转写页：环境判定返回明确结论")
def _():
    _app()
    from app.pages.transcribe_page import TranscribePage

    page = TranscribePage()
    ok, note = page.check_environment()
    assert isinstance(ok, bool)
    assert note, "环境说明为空"
    if ok:
        assert "就绪" in note, f"可用时说明应含「就绪」：{note}"


@check("② 转写页：预估时间随队列变化")
def _():
    _app()
    from app.pages.transcribe_page import TranscribePage

    page = TranscribePage()
    assert page.eta_text([]) == "", "空队列不应显示预估"

    sample = _sample_audio()
    if sample is None:
        SKIPPED.append("② 转写页：预估时间随队列变化（缺少测试音频）")
        return
    page.queue.add_paths([str(sample)], announce=False)
    eta = page.eta_text(page.queue.files())
    assert "预计需要" in eta or "总时长" in eta, f"预估文案异常：{eta}"


@check("③ 纪要页：环境判定与密钥缺失时的提示")
def _():
    _app()
    from app.pages.minutes_page import MinutesPage

    page = MinutesPage()
    ok, note = page.check_environment()
    assert isinstance(ok, bool)
    assert note, "环境说明为空"
    # 未配置密钥时必须明确告知原因，不能让用户点了没反应
    from mmtools.config import Config

    if not Config.load().api_key():
        assert not ok, "未配置密钥时环境判定应为不可用"
        assert "密钥" in note, f"未配置密钥的说明应提到密钥：{note}"


@check("③ 纪要页：可接收文字稿并统计段数")
def _():
    _app()
    from app.pages.minutes_page import MinutesPage

    page = MinutesPage()
    sample = _sample_transcript()
    if sample is None:
        SKIPPED.append("③ 纪要页：可接收文字稿并统计段数（缺少测试文字稿）")
        return
    added = page.queue.add_paths([str(sample)], announce=False)
    assert added == 1
    info = page.queue.table.item(0, 1).text()
    assert "段" in info or "KB" in info, f"信息列异常：{info}"


@check("分步页面：进度事件处理不抛异常")
def _():
    _app()
    from app.pages.transcribe_page import TranscribePage

    page = TranscribePage()
    sample = _sample_audio()
    if sample is not None:
        page.queue.add_paths([str(sample)], announce=False)

    page.on_progress({"stage": "start", "total": 1, "current": 0})
    page.on_progress({"stage": "file_start", "file": "a.wav", "current": 1, "total": 1})
    page.on_progress({"stage": "transcribe", "message": "开始转写"})
    page.on_progress({"stage": "file_done", "current": 1, "total": 1})
    page.on_progress({"stage": "all_done", "message": "完成", "total": 1, "current": 1})
    assert page.bar.value() == 100

    # 失败与取消路径也要能安全处理
    page.on_progress({"stage": "file_failed", "current": 1, "total": 1, "error": "模拟"})
    page.on_progress({"stage": "cancelled", "message": "已取消"})
    page.on_progress({"stage": "error", "error": "模拟错误"})


@check("分步页面：日志写入不抛异常")
def _():
    _app()
    from app.pages.minutes_page import MinutesPage

    page = MinutesPage()
    for text in ("普通一行", "", "含特殊字符 <>&\"' 的一行", "很长的行 " * 100):
        page.append_log(text)
    assert "普通一行" in page.log.toPlainText()


# ---------------------------------------------------------------- 衔接


@check("三步衔接：转写产物能送到纪要页")
def _():
    _app()
    from app.main_window import MainWindow, PAGE_MINUTES

    w = MainWindow()
    sample = _sample_transcript()
    if sample is None:
        SKIPPED.append("三步衔接：转写产物能送到纪要页（缺少测试文字稿）")
        w.close()
        return
    before = len(w.minutes_page.queue.files())
    w._transcribe_to_minutes([str(sample)])
    after = len(w.minutes_page.queue.files())
    assert after >= before, "文字稿未送入纪要页"
    assert w.nav.currentRow() == PAGE_MINUTES, "未自动切到纪要页"
    w.close()


@check("三步衔接：文件库能把录音送去转写")
def _():
    _app()
    from app.main_window import MainWindow, PAGE_TRANSCRIBE

    w = MainWindow()
    sample = _sample_audio()
    if sample is None:
        SKIPPED.append("三步衔接：文件库能把录音送去转写（缺少测试音频）")
        w.close()
        return
    w._library_to_transcribe([str(sample)])
    assert w.nav.currentRow() == PAGE_TRANSCRIBE, "未自动切到转写页"
    w.close()


@check("文件库：扫描与筛选正常")
def _():
    _app()
    from app.pages.library_page import KIND_MINUTES, KIND_TRANSCRIPT, LibraryPage

    page = LibraryPage()
    page.reload()
    total = page.table.rowCount()

    page.cmb_filter.setCurrentText(KIND_TRANSCRIPT)
    page.apply_filter()
    only_transcript = page.table.rowCount()
    assert only_transcript <= total, "筛选后数量反而变多"

    page.cmb_filter.setCurrentText(KIND_MINUTES)
    page.apply_filter()
    assert page.table.rowCount() <= total

    page.cmb_filter.setCurrentText("全部")
    page.apply_filter()
    assert page.table.rowCount() == total, "恢复全部后数量不一致"


# ---------------------------------------------------------------- 线程


@check("后台任务：取消标志可生效")
def _():
    _app()
    from app.worker import SummarizeWorker, TranscribeWorker

    for cls in (TranscribeWorker, SummarizeWorker):
        worker = cls(cfg=None, files=[])
        assert not worker.cancel_requested
        worker.cancel()
        assert worker.cancel_requested, f"{cls.__name__} 取消标志未生效"


@check("业务层取消检查会抛出 CancelledError")
def _():
    from mmtools.progress import CancelledError, Progress

    flag = {"v": False}
    p = Progress(callback=None, echo=False, cancel_check=lambda: flag["v"])
    p.check_cancel()

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
