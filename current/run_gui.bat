@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Research Robot v3.7.0
if not exist ".venv\Scripts\python.exe" (
    call install_and_run_gui.bat
    exit /b %errorlevel%
)
".venv\Scripts\python.exe" desktop_gui.py
if errorlevel 1 pause
