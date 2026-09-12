@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Install and Verify MOEX Robot v3.10.0 Stable Release Candidate

call prepare_environment.bat --with-dev
if errorlevel 1 exit /b 1
call VERIFY_V3_10_0_STABLE.bat --no-pause
if errorlevel 1 exit /b %errorlevel%

echo.
echo v3.10.0 automated release-candidate verification passed.
echo Q1-Q8 evidence, Stable acceptance and publication remain separate gates.
pause
exit /b 0
