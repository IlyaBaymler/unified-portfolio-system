@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build MOEX Robot v3.10.0 Stable Source Release
call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"
"%PYTHON%" -c "import sys; from tools import release_cleanup as c; c.CURRENT_FILES.clear(); c.CURRENT_FILES.update({'CHANGELOG_V3_10_0_STABLE_RU.md','RELEASE_MANIFEST_V3_10_0_STABLE.txt','UPDATE_TO_V3_10_0_STABLE.md','V3_10_0_STABLE_ARCHITECTURE_RU.md','V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md','V3_10_0_STABLE_TEST_PLAN_RU.md','VERIFY_V3_10_0_STABLE.bat','install_and_verify_v3_10_0.bat','restore_stable_default_risk_profile.bat'}); from tools.build_release import main; sys.exit(main(['--root','.','--output','dist/moex_trading_robot_source_v3_10_0.zip','--archive-root','moex_trading_robot_source_v3_10_0']))"
if errorlevel 1 goto :fail
echo Release build completed.
pause
exit /b 0
:fail
echo [ERROR] Release build failed.
pause
exit /b 1
