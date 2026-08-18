@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title MOEX v3.9.0 MARKET_IDLE Tests
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pytest -q tests\test_market_idle_rc1_3.py
if errorlevel 1 exit /b 1
pause
