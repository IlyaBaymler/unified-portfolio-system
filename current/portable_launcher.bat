@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "MOEX_ROBOT_PORTABLE_LAYOUT=1"
set "MOEX_ROBOT_RUNTIME_DIR=%CD%\runtime"
set "MOEX_ROBOT_LOGS_DIR=%CD%\logs"
set "MOEX_ROBOT_BACKUPS_DIR=%CD%\backups"
set "MOEX_ROBOT_REPORTS_DIR=%CD%\reports"
set "MOEX_ROBOT_SUPPORT_DIR=%CD%\support"
if not exist "app\MOEXResearchRobot.exe" (
    echo [ERROR] app\MOEXResearchRobot.exe not found.
    pause
    exit /b 1
)
start "MOEX Research Robot v3.7-alpha3" "app\MOEXResearchRobot.exe"
exit /b 0
