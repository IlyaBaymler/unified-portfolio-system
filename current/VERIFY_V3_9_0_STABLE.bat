@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1"
cd /d "%~dp0"
title MOEX Research Robot v3.9.0 Stable Candidate Verification

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail_start
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "verification_output" mkdir "verification_output"
if not exist "verification_output\system-temp" mkdir "verification_output\system-temp"
set "TEMP=%CD%\verification_output\system-temp"
set "TMP=%CD%\verification_output\system-temp"
set "LOG_FILE=verification_output\verify_v3_9_0_stable.log"
> "%LOG_FILE%" echo === MOEX Research Robot v3.9.0 Stable candidate verification ===

echo [1/11] Exact source and candidate manifest...
"%PYTHON%" tools\v3_9_stable_preflight.py --source-root . --output "verification_output\m6_source_preflight.json" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=11" & goto :fail_run

echo [2/11] Stable release and standalone contracts...
"%PYTHON%" -m pytest -q --basetemp verification_output\pytest-release -p no:cacheprovider tests\test_stable_release_v3_9.py tests\test_standalone_rc1.py tests\test_release_hygiene.py tests\test_v3_9_stable_preflight.py tests\test_v3_9_source_artifact_qualification.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=12" & goto :fail_run

echo [3/11] Portfolio Risk and Central regression...
"%PYTHON%" -m pytest -q --basetemp verification_output\pytest-risk -p no:cacheprovider tests\test_portfolio_risk_v3_9.py tests\test_portfolio_risk_adapter_v3_9.py tests\test_portfolio_risk_runtime_v3_9.py tests\test_portfolio_risk_shadow_v3_9.py tests\test_v3_9_risk_control_tool.py tests\test_v3_9_external_cash_resync.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=13" & goto :fail_run

echo [4/11] M5.3 persistence and recovery contracts...
"%PYTHON%" -m pytest -q --basetemp verification_output\pytest-persistence -p no:cacheprovider tests\test_v3_9_persistence_qualification.py tests\test_runtime_backup_rc1.py tests\test_runtime_backup_rc1_2.py tests\test_support_readiness_rc1.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=14" & goto :fail_run

echo [5/11] Release hygiene...
"%PYTHON%" tools\release_cleanup.py --root . --check-runtime >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=15" & goto :fail_run

echo [6/11] Deterministic release file audit...
"%PYTHON%" tools\build_release.py --root . --output "verification_output\_verification_only.zip" --archive-root "verification" --check-only >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=16" & goto :fail_run

echo [7/11] Dependency integrity...
"%PYTHON%" -m pip check >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=17" & goto :fail_run

echo [8/11] Ruff checks...
"%PYTHON%" -m ruff check --select E9,F63,F7,F82 . >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=18" & goto :fail_run
"%PYTHON%" -m ruff check trading_robot\portfolio_risk*.py trading_robot\central_order_coordinator.py trading_robot\central_order_manager.py tools\v3_9_*.py tests\test_*v3_9.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=19" & goto :fail_run

echo [9/11] Compileall...
"%PYTHON%" -m compileall -q -f trading_robot tools tests >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=20" & goto :fail_run

echo [10/11] Full regression suite...
"%PYTHON%" -m pytest -q --basetemp verification_output\pytest-full -p no:cacheprovider >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=21" & goto :fail_run

echo [11/11] Candidate safety boundary...
"%PYTHON%" -c "import json,trading_robot; m=json.load(open('build_manifest.json',encoding='utf-8')); q=m['stable_qualification']; assert trading_robot.__version__=='0.3.9'; assert m['display_version']=='v3.9.0'; assert m['release_channel']=='stable'; assert m['sandbox_only'] and not m['real_account_execution']; assert m['risk_state_schema']>=4; assert q['status']=='candidate' and not q['user_acceptance']; assert not q['final_burn_in_complete']; print('Candidate safety boundary: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=22" & goto :fail_run

set "RC=0"
"%PYTHON%" -c "from pathlib import Path; print(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo Automated v3.9.0 candidate verification completed successfully.
echo Manual M6 gates remain pending; this is not Stable acceptance.
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail_run
"%PYTHON%" -c "from pathlib import Path; print(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo [ERROR] Verification failed with code %RC%.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%

:fail_start
echo [ERROR] Verification could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
