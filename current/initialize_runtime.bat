@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Research Robot v3.7-alpha3 - Runtime Setup

call prepare_environment.bat
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"

"%PYTHON%" runtime_setup.py
if errorlevel 1 goto :fail

echo.
echo Runtime setup completed. Existing user files were preserved.
pause
exit /b 0

:fail
echo.
echo [ERROR] Runtime setup failed.
echo Review runtime_bootstrap_report.json and the console output.
pause
exit /b 1
