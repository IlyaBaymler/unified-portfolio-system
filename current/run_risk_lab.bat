@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Lab v3.9.0

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" (
    set "NO_PAUSE=1"
    shift
)
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat
    if errorlevel 1 goto :fail_start
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if "%~1"=="" (set "OUTPUT_DIR=risk_alpha_output") else (set "OUTPUT_DIR=risk_custom_output")
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"
set "LOG_FILE=%OUTPUT_DIR%\risk_lab_console.log"
if "%~1"=="" (
    "%PYTHON%" risk_lab.py --output-dir "%OUTPUT_DIR%" > "%LOG_FILE%" 2>&1
) else (
    "%PYTHON%" risk_lab.py --scenario "%~1" --output-dir "%OUTPUT_DIR%" > "%LOG_FILE%" 2>&1
)
set "RC=%ERRORLEVEL%"
type "%LOG_FILE%"
echo.
echo Log: %LOG_FILE%
if not "%RC%"=="0" goto :fail_run
echo Risk Lab completed successfully.
if "%NO_PAUSE%"=="0" pause
exit /b 0
:fail_run
echo [ERROR] Risk Lab failed with code %RC%.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
:fail_start
echo [ERROR] Risk Lab could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
