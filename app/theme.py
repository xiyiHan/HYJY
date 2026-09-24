"""界面样式。

刻意保持克制：只用少量强调色，主体沿用系统原生外观。
理由是这个工具的使用场景是"快速处理完关掉"，不需要花哨的视觉，
而且过度自定义样式在不同 Windows 版本上容易出现显示异常。
"""

from __future__ import annotations

ACCENT = "#2f6fb5"
ACCENT_HOVER = "#255a94"
DANGER = "#b5452f"
MUTED = "#7a7a7a"
SUCCESS = "#3d7a3d"
BORDER = "#d0d0d0"

STYLESHEET = f"""
QWidget {{
    font-size: 13px;
}}

#appTitle {{
    font-size: 15px;
    font-weight: 500;
}}

#navList {{
    background: #f7f7f7;
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    min-width: 108px;
    max-width: 108px;
    padding-top: 8px;
}}

#navList::item {{
    padding: 9px 14px;
    color: #444;
}}

#navList::item:selected {{
    background: #ffffff;
    color: {ACCENT};
    border-left: 2px solid {ACCENT};
}}

#dropArea {{
    border: 1px dashed #b8b8b8;
    border-radius: 8px;
    background: #fbfbfb;
}}

#dropTitle {{
    font-size: 14px;
    color: #333;
}}

#dropHint {{
    color: {MUTED};
    font-size: 12px;
}}

#primary {{
    background: {ACCENT};
    color: #ffffff;
    border: none;
    padding: 6px 18px;
    border-radius: 4px;
}}

#primary:hover {{
    background: {ACCENT_HOVER};
}}

#primary:disabled {{
    background: #c8c8c8;
    color: #f0f0f0;
}}

QPushButton {{
    padding: 5px 14px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: #ffffff;
}}

QPushButton:hover {{
    background: #f2f2f2;
}}

QPushButton:disabled {{
    color: #b0b0b0;
    background: #fafafa;
}}

#muted {{
    color: {MUTED};
    font-size: 12px;
}}

#statusLine {{
    color: #333;
    padding: 2px 0;
}}

#logView {{
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    background: #fbfbfb;
    border: 1px solid {BORDER};
    border-radius: 4px;
}}

#sectionTitle {{
    font-size: 14px;
    font-weight: 500;
    padding: 2px 0;
}}

#hint {{
    color: {MUTED};
    font-size: 12px;
}}

#warn {{
    color: {DANGER};
    font-size: 12px;
}}

#ok {{
    color: {SUCCESS};
    font-size: 12px;
}}

QTableWidget {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    gridline-color: #ededed;
}}

QHeaderView::section {{
    background: #f7f7f7;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 5px 8px;
    font-weight: 500;
}}

QProgressBar {{
    border: none;
    border-radius: 3px;
    background: #ececec;
    height: 6px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 10px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #444;
}}
"""
