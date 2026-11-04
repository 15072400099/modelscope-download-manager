@echo off
cd /d "%~dp0"
setlocal
set "TQDM_DISABLE=1"
title 检查并补齐模型文件

if exist "python\python.exe" (
  set "PY=python\python.exe"
) else if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" "app\verify_models.py"
if errorlevel 1 (
  echo.
  echo 核对失败，请检查网络后重试。
  pause
  exit /b 1
)

echo.
echo 开始补齐缺失文件（只补差额）...
"%PY%" "app\repair_missing.py"

echo.
echo 完成了，按任意键关闭窗口。
pause >nul
