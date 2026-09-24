"""图形界面入口。

启动方式：
    双击 启动.bat
    或  .venv\\Scripts\\python.exe -m app.main

也可以从命令行直接运行：python -m app.main
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

# 允许以脚本方式直接运行（python app/main.py）时也能找到 mmtools
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_excepthook() -> None:
    """未捕获异常时弹窗提示，而不是让窗口静默消失。

    GUI 程序出错时如果只往 stderr 打印，用户看到的就是"点了没反应"，
    完全无从排查。这里兜住并把信息显示出来。
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    def hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        sys.stderr.write(text)
        if QApplication.instance() is not None:
            QMessageBox.critical(
                None,
                "程序出现异常",
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"详情已输出到终端。若反复出现，请把下面的信息一并反馈：\n\n"
                f"{text[-800:]}",
            )

    sys.excepthook = hook


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    # 高分屏下图标与文字不发虚
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("会议纪要工具")

    _install_excepthook()

    from .theme import STYLESHEET

    app.setStyleSheet(STYLESHEET)

    try:
        from mmtools.config import Config

        Config.load()
    except Exception as exc:  # noqa: BLE001
        QMessageBox.critical(
            None,
            "配置读取失败",
            f"无法读取 config.yaml：\n{exc}\n\n"
            f"请确认程序目录完整，且 config.yaml 存在。",
        )
        return 2

    from .main_window import MainWindow

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
