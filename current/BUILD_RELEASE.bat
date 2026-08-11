@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build MOEX Robot v3.7-alpha3 Source Release
call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" tools\release_cleanup.py --root . --clean
if errorlevel 1 goto :fail
"%PYTHON%" tools\build_release.py --root . --output "dist\moex_trading_robot_research_v3_7_alpha3.zip" --archive-root "moex_trading_robot_research_v3_7_alpha3"
if errorlevel 1 goto :fail
echo Release build completed.
pause
exit /b 0
:fail
echo [ERROR] Release build failed.
pause
exit /b 1
