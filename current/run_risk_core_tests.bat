@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Engine Core Tests v3.9.0

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat --with-dev
    if errorlevel 1 goto :fail_start
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "risk_alpha_output" mkdir "risk_alpha_output"
set "LOG_FILE=risk_alpha_output\risk_core_tests_console.log"

"%PYTHON%" -m compileall -q trading_robot\risk.py trading_robot\risk_persistence.py trading_robot\risk_runtime.py trading_robot\risk_reporting.py risk_lab.py risk_report.py > "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=11" & goto :fail_run
"%PYTHON%" -m pytest -q tests\test_risk.py tests\test_risk_lab.py tests\test_risk_reporting.py tests\test_risk_report_cli.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=12" & goto :fail_run
"%PYTHON%" risk_lab.py --output-dir risk_alpha_output >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=13" & goto :fail_run
set "RC=0"
type "%LOG_FILE%"
echo.
echo Risk Engine core and reporting tests completed successfully.
echo Log: %LOG_FILE%
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail_run
type "%LOG_FILE%"
echo [ERROR] Risk Engine core tests failed with code %RC%.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
:fail_start
echo [ERROR] Risk Engine core tests could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
