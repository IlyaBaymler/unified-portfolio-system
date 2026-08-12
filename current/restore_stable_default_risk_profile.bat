@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Restore MOEX v3.7.0 Stable Risk Profile

echo This operation replaces the selected SANDBOX_EXECUTION risk profile.
echo Standard stable limits include max_position_lots=1 and max_orders_per_day=4.
echo Stop Sandbox Execution and create a verified runtime backup first.
echo.
set /p "CONFIRM=Type STABLE to continue: "
if /I not "%CONFIRM%"=="STABLE" (
    echo Cancelled. No files were changed.
    pause
    exit /b 1
)

call run_risk_profile_tool.bat preset default --mode SANDBOX_EXECUTION
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [ERROR] Stable risk profile was not restored.
    pause
    exit /b %RC%
)

echo.
echo Stable SANDBOX_EXECUTION risk profile restored.
echo Restart the GUI and verify Risk Dashboard before execution.
pause
exit /b 0
