@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Release Cleanup v3.7-beta1
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" tools\release_cleanup.py --root . --clean
if errorlevel 1 goto :fail
echo.
echo Legacy release files were removed.
pause
exit /b 0
:fail
echo.
echo [ERROR] Cleanup failed.
pause
exit /b 1
