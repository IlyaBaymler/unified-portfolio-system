@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Install and Verify MOEX Robot v3.9.0 Stable Candidate

call prepare_environment.bat --with-dev
if errorlevel 1 exit /b 1
call VERIFY_V3_9_0_STABLE.bat --no-pause
if errorlevel 1 exit /b %errorlevel%

echo.
echo v3.9.0 automated candidate preflight passed.
echo Manual standalone, recovery, kill-switch and burn-in gates remain pending.
pause
exit /b 0
