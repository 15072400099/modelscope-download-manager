@echo off
cd /d "%~dp0"
setlocal
set "TQDM_DISABLE=1"
title 安装运行环境
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没找到 Python。
  echo   请先安装 Python 3.10 或更高版本，安装时记得勾选 "Add Python to PATH"。
  echo   下载地址： https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo 正在创建虚拟环境...
  python -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败。
    pause
    exit /b 1
  )
)
echo 正在安装依赖（首次约几分钟）...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)
echo.
echo 装好了。双击「启动下载管理器.bat」就能用。
pause
