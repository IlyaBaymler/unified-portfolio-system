@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title Install and Verify MOEX Robot v3.7-alpha3
call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" tools\release_cleanup.py --root . --clean
if errorlevel 1 goto :fail
"%PYTHON%" runtime_setup.py
if errorlevel 1 goto :fail
call VERIFY_V3_7_ALPHA3.bat --no-pause
if errorlevel 1 goto :fail
echo.
echo Installation and verification completed successfully.
echo.
echo IMPORTANT: Alpha3 never migrates PortfolioState automatically.
echo Run cutover preview before GUI trading:
echo   run_portfolio_cutover.bat preview --account-id ^<ACCOUNT_ID^>
pause
exit /b 0
:fail
echo.
echo [ERROR] Installation or verification failed.
pause
exit /b 1
