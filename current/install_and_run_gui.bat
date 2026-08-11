@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Research Robot v3.7-beta1 - Setup

call prepare_environment.bat --with-dev
if errorlevel 1 goto :error
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" tools\release_cleanup.py --root . --clean
if errorlevel 1 goto :error

echo.
echo Environment is ready. GUI will run the unified first-run setup.
"%PYTHON%" desktop_gui.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo [ERROR] Setup or GUI startup failed.
echo Read the message above, runtime_bootstrap_report.json or robot_gui.log.
pause
exit /b 1
