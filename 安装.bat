@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title 会议纪要工具 - 环境安装

echo ================================================================
echo   会议纪要工具 - 环境安装
echo ================================================================
echo.
echo   这一步会完成以下工作：
echo     1. 在项目目录下创建独立的 Python 运行环境
echo     2. 安装全部依赖
echo     3. 下写模型目录到本盘，避免占用系统盘
echo.
echo   首次安装需要下载约 3 GB 内容，请保持网络畅通。
echo   全部完成大约需要 20 到 40 分钟。
echo.
echo ================================================================
echo.

REM ---------- 1. 定位 Python ----------
set PYTHON_EXE=
where py >nul 2>&1 && set PYTHON_EXE=py -3
if "%PYTHON_EXE%"=="" (
    where python >nul 2>&1 && set PYTHON_EXE=python
)
if "%PYTHON_EXE%"=="" (
    echo   [错误] 没有找到 Python。
    echo.
    echo   请先安装 Python 3.10 以上版本：https://www.python.org/downloads/
    echo   安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)
echo   [1/5] 已找到 Python：
%PYTHON_EXE% --version
echo.

REM ---------- 2. 缓存与临时目录重定向 ----------
REM 模型和 pip 缓存加起来有好几 GB，绝不能落在系统盘
set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
set PIP_CACHE_DIR=%ROOT%\.cache\pip
set TMP=%ROOT%\.cache\tmp
set TEMP=%ROOT%\.cache\tmp
set PIP_DISABLE_PIP_VERSION_CHECK=1
if not exist "%PIP_CACHE_DIR%" mkdir "%PIP_CACHE_DIR%"
if not exist "%TEMP%" mkdir "%TEMP%"
echo   [2/5] 缓存与临时目录已指向：%ROOT%\.cache
echo.

REM ---------- 3. 创建虚拟环境 ----------
if exist ".venv\Scripts\python.exe" (
    echo   [3/5] 运行环境已存在，跳过创建
) else (
    echo   [3/5] 正在创建运行环境……
    %PYTHON_EXE% -m venv .venv
    if errorlevel 1 (
        echo.
        echo   [错误] 创建运行环境失败。
        pause
        exit /b 1
    )
)
echo.

REM ---------- 4. 安装依赖 ----------
echo   [4/5] 正在安装依赖（耗时较长，请勿关闭窗口）……
echo.
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   [错误] 基础依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-funasr.txt
if errorlevel 1 (
    echo.
    echo   [错误] 转写引擎安装失败，请检查网络后重试。
    pause
    exit /b 1
)
echo.
echo   可选组件：录音功能需要以下依赖，用于直接录制会议音频。
echo   如不需要可在录音时用手机或会议软件自行录制。
choice /c YN /n /m "   是否一并安装录音组件？(Y/N) "
if errorlevel 2 goto skip_record
.venv\Scripts\python.exe -m pip install -r requirements-record.txt
:skip_record
echo.

REM ---------- 5. 生成配置 ----------
echo   [5/5] 检查配置文件……
if not exist "config.local.yaml" (
    if exist "config.local.example.yaml" (
        copy "config.local.example.yaml" "config.local.yaml" >nul
    )
    if exist "config.local.yaml.example" (
        copy "config.local.yaml.example" "config.local.yaml" >nul
    )
    echo   已生成 config.local.yaml，请填入 API Key 或指向密钥文件。
) else (
    echo   config.local.yaml 已存在，未改动。
)
echo.

echo ================================================================
echo   安装完成
echo ================================================================
echo.
echo   下一步：
echo     1. 双击 启动.bat 打开界面
echo     2. 在「设置」页填入 API Key 与模板路径
echo     3. 回到「任务」页拖入录音文件
echo.
echo   提示：首次转写会自动下载语音模型（约 2 GB），
echo         所以第一次会比之后慢很多。
echo.
pause
