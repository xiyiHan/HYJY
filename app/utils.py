"""界面公用小工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_in_explorer(path: Path, select: bool = False) -> None:
    """在系统文件管理器中打开文件或目录。

    select=True 时定位并选中该文件（而不是单纯打开所在目录），
    便于用户立刻找到刚生成的那一份。
    """
    path = Path(path)
    try:
        if sys.platform == "win32":
            if select and path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                target = path if path.is_dir() else path.parent
                os.startfile(str(target))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target if not select else path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
    except Exception:
        pass


def open_file(path: Path) -> None:
    """用系统默认程序打开文件。"""
    path = Path(path)
    if not path.exists():
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def format_size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except Exception:
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 ** 2:.1f} MB"
