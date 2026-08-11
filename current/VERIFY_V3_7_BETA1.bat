@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title MOEX Research Robot v3.7-beta1 Verification

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail_start
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "verification_output" mkdir "verification_output"
set "LOG_FILE=verification_output\verify_v3_7_beta1.log"
> "%LOG_FILE%" echo === MOEX Research Robot v3.7-beta1 verification ===

echo [1/9] Version and beta1 manifest...
"%PYTHON%" -c "import json,trading_robot; from pathlib import Path; m=json.loads(Path('build_manifest.json').read_text(encoding='utf-8')); assert trading_robot.__version__=='0.3.7b1'; assert m['display_version']=='v3.7-beta1'; assert m['release_channel']=='beta'; assert m['sandbox_only'] and not m['real_account_execution']; print('Beta1 manifest: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=11" & goto :fail_run

echo [2/9] Beta1 release and stabilization tests...
"%PYTHON%" -m pytest -q tests\test_beta_release_v3_7.py tests\test_portfolio_manager_v3_7.py tests\test_runtime_bootstrap.py tests\test_risk_reporting.py tests\test_support_readiness_rc1.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=12" & goto :fail_run

echo [3/9] Release hygiene...
"%PYTHON%" tools\release_cleanup.py --root . --check-runtime >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=13" & goto :fail_run

echo [4/9] Deterministic release audit...
"%PYTHON%" tools\build_release.py --root . --output "dist\_verification_only.zip" --archive-root "verification" --check-only >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=14" & goto :fail_run

echo [5/9] Compileall...
"%PYTHON%" -m compileall -q trading_robot desktop_gui.py portfolio_tool.py portfolio_cutover_tool.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=15" & goto :fail_run

echo [6/9] Full regression suite...
"%PYTHON%" -m pytest -q >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=16" & goto :fail_run

echo [7/9] Risk Lab...
"%PYTHON%" risk_lab.py --output-dir risk_beta_output >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=17" & goto :fail_run

echo [8/9] Runtime and CLI contracts...
"%PYTHON%" runtime_setup.py --no-report >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=18" & goto :fail_run
"%PYTHON%" portfolio_cutover_tool.py --help >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=19" & goto :fail_run

echo [9/9] Safety boundary...
"%PYTHON%" -c "import json; m=json.load(open('build_manifest.json',encoding='utf-8')); assert m['portfolio_manager_mode']=='canonical-only'; assert m['multi_asset_execution'] is False; assert m['real_account_execution'] is False; print('Safety boundary: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=20" & goto :fail_run

set "RC=0"
"%PYTHON%" -c "from pathlib import Path; print(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo Verification v3.7-beta1 completed successfully.
echo Expected full suite: 453 passed; Risk Lab: 8/8 PASS.
echo Log: %LOG_FILE%
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
