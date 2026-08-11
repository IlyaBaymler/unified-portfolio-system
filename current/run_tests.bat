@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Robot v3.7-alpha3 Tests
call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" -m compileall -q . || goto :fail
"%PYTHON%" -m pytest -q || goto :fail
echo.
echo All tests passed.
pause
exit /b 0
:fail
echo.
echo [ERROR] Tests failed.
pause
exit /b 1
