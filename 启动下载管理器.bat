@echo off
cd /d "%~dp0"
setlocal
set "TQDM_DISABLE=1"
title 模搭 · 模型下载管理器（运行中）

if exist "python\python.exe" (
  set "PY=python\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

if /i "%PY%"=="python" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo.
    echo   还没装环境，请先双击「安装环境.bat」。
    echo.
    pause
    exit /b 1
  )
)

echo 正在启动，浏览器会自动打开界面...
echo 想停止：双击「关闭下载管理器.bat」，或直接关掉这个窗口。
echo.

"%PY%" "app\server.py" --open
set "CODE=%ERRORLEVEL%"

if "%CODE%"=="2" (
  echo.
  echo 已经有一个在运行了，浏览器应该已经打开了。
  echo 这个窗口 3 秒后自动关闭。
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

echo.
echo 服务已停止。按任意键关闭窗口。
pause >nul
