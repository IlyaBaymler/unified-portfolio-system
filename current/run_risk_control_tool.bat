@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Control Tool v3.7-alpha3
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat
    if errorlevel 1 goto :fail
)
if "%~1"=="" (
    echo Usage:
    echo   run_risk_control_tool.bat show
    echo   run_risk_control_tool.bat engage --reason "manual test"
    echo   run_risk_control_tool.bat clear --confirmation "CLEAR RISK HALT"
    echo   run_risk_control_tool.bat reset-baselines --equity-rub 50000 --confirmation "RESET RISK BASELINES"
    echo.
    echo Account id is read from TBANK_SANDBOX_ACCOUNT_ID in .env.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" risk_control_tool.py %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
:fail
echo [ERROR] Risk control tool could not start.
pause
exit /b 1
