@echo off
cd /d "%~dp0"
setlocal
title 环境自检

echo ============================================
echo   这个程序不需要安装第三方库，只做一次自检
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] 找不到 Python。
  echo     请安装 Python 3.10 或更高版本，安装时勾选 "Add Python to PATH"。
  echo     下载地址： https://www.python.org/downloads/
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('python -V 2^>^&1') do set "PYVER=%%v"
echo [OK] Python %PYVER%

where curl >nul 2>nul
if errorlevel 1 (
  echo [X] 找不到 curl.exe。
  echo     本程序靠系统自带的 curl 下载，Windows 10 1803 之后应自带。
  echo     如果没有，从 https://curl.se/windows/ 下载解压到 PATH 里。
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('curl --version 2^>^&1') do set "CURLVER=%%v"
echo [OK] curl %CURLVER%

echo.
echo 环境没问题，双击「启动下载管理器.bat」就能用。
pause
