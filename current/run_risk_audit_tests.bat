@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Audit Tests v3.7-alpha3

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat --with-dev
    if errorlevel 1 goto :fail_start
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "risk_audit_output" mkdir "risk_audit_output"
set "LOG_FILE=risk_audit_output\risk_audit_console.log"

"%PYTHON%" -m pytest -q tests\test_risk_runtime.py tests\test_risk_sandbox_integration.py tests\test_diagnostics.py tests\test_risk_control_tool.py tests\test_bot.py > "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
type "%LOG_FILE%"
echo.
echo Log: %LOG_FILE%
if not "%RC%"=="0" goto :fail_run
echo Risk audit, diagnostic accounting, recovery and external-activity tests completed successfully.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail_run
echo [ERROR] Risk audit tests failed with code %RC%.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%

:fail_start
echo [ERROR] Risk audit tests could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
