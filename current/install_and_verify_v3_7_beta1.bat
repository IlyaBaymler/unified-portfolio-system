@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Install and Verify MOEX Robot v3.7-beta1

call prepare_environment.bat --with-dev
if errorlevel 1 exit /b 1
call VERIFY_V3_7_BETA1.bat --no-pause
if errorlevel 1 exit /b %errorlevel%

echo.
echo v3.7-beta1 installed and verified. Sandbox Execution remains operator-controlled.
pause
exit /b 0
