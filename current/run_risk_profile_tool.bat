@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX Risk Profile Tool v3.7-alpha3
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat
    if errorlevel 1 goto :fail
)
if "%~1"=="" (
    echo Usage:
    echo   run_risk_profile_tool.bat show
    echo   run_risk_profile_tool.bat preset default
    echo   run_risk_profile_tool.bat preset max-one
    echo   run_risk_profile_tool.bat preset block-new
    echo   run_risk_profile_tool.bat preset permissive
    echo   run_risk_profile_tool.bat preset sandbox-beta1 --mode SANDBOX_EXECUTION
    echo   run_risk_profile_tool.bat show --mode SANDBOX_EXECUTION
    echo   run_risk_profile_tool.bat reset --mode SANDBOX_EXECUTION
    pause
    exit /b 1
)
".venv\Scripts\python.exe" risk_profile_tool.py %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
:fail
echo [ERROR] Risk profile tool could not start.
pause
exit /b 1
