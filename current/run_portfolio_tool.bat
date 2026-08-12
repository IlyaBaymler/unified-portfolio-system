@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment is missing. Run install_and_run_gui.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" portfolio_tool.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%
