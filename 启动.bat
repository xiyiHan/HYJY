@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title 会议纪要工具

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   未找到运行环境。
    echo.
    echo   请先双击运行 安装.bat 完成环境准备。
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m app.main
exit /b 0
