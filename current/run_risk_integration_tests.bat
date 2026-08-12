@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Integration Tests v3.7.0

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat --with-dev
    if errorlevel 1 goto :fail_start
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "risk_integration_output" mkdir "risk_integration_output"
set "LOG_FILE=risk_integration_output\risk_integration_console.log"

"%PYTHON%" -m pytest -q tests\test_risk_runtime.py tests\test_risk_sandbox_integration.py tests\test_risk_control_tool.py > "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
type "%LOG_FILE%"
echo.
echo Log: %LOG_FILE%
if not "%RC%"=="0" goto :fail_run
echo Dry-run and Sandbox Risk Engine integration tests completed successfully.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail_run
echo [ERROR] Risk integration tests failed with code %RC%.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
:fail_start
echo [ERROR] Risk integration tests could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
