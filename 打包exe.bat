@echo off
cd /d "%~dp0"
setlocal
title 打包 exe

if exist "python\python.exe" goto :havepy
if exist ".venv\Scripts\python.exe" goto :havevenv
set "PY=python"
goto :build

:havepy
set "PY=python\python.exe"
goto :build

:havevenv
set "PY=.venv\Scripts\python.exe"

:build
echo 正在安装打包工具...
"%PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo 安装 PyInstaller 失败，检查网络后重试。
  pause
  exit /b 1
)

echo.
echo 正在打包（几分钟，别关窗口）...
"%PY%" -m PyInstaller --noconfirm --onefile --console --name ModelScopeDownloadManager --add-data "app/web;app/web" --paths app --hidden-import ms_client --hidden-import download_manager --hidden-import transport app/server.py

if errorlevel 1 (
  echo.
  echo 打包失败，把上面的报错发出来看看。
  pause
  exit /b 1
)

echo.
echo 打包完成：dist\ModelScopeDownloadManager.exe
echo 双击它就能用，模型会存在 exe 旁边的 models 目录。
pause
