@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title MOEX Research Robot v3.7-alpha3 Verification

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" (
    call prepare_environment.bat --with-dev
    if errorlevel 1 goto :fail_start
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "verification_output" mkdir "verification_output"
set "LOG_FILE=verification_output\verify_v3_7_alpha3.log"
> "%LOG_FILE%" echo === MOEX Research Robot v3.7-alpha3 verification ===

echo [1/25] Version and alpha3 manifest...
"%PYTHON%" -c "import json,trading_robot; from pathlib import Path; m=json.loads(Path('build_manifest.json').read_text(encoding='utf-8')); assert trading_robot.__version__=='0.3.7a3'; assert m['display_version']=='v3.7-alpha3'; assert m['portfolio_state_schema']==2; assert m['portfolio_manager_protocol']=='canonical-portfolio-v2'; assert m['portfolio_manager_mode']=='canonical-only'; assert m['legacy_state_dual_read_enabled'] is False; assert m['legacy_state_cutover_complete'] is True; assert m['canonical_only_preflight'] is True; assert m['portfolio_transaction_protocol']=='canonical-single-writer-v1'; assert m['portfolio_cutover_protocol']=='schema1-to-schema2-v1'; assert m['automatic_portfolio_cutover'] is False; assert m['sandbox_only'] and not m['real_account_execution']; print('Version:',trading_robot.__version__); print('Alpha3 manifest: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=11" & goto :fail_run

echo [2/25] Alpha release metadata...
"%PYTHON%" -m pytest -q tests\test_alpha_release_v3_7.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=12" & goto :fail_run

echo [3/25] Alpha3 cutover, canonical-only and transaction tests...
"%PYTHON%" -m pytest -q tests\test_portfolio_cutover_alpha3.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=13" & goto :fail_run

echo [4/25] Alpha2 preflight and race regression...
"%PYTHON%" -m pytest -q tests\test_portfolio_preflight_alpha2.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=14" & goto :fail_run

echo [5/25] Alpha1.1 operator regression...
"%PYTHON%" -m pytest -q tests\test_alpha1_1_features.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=15" & goto :fail_run

echo [6/25] Alpha1.2 transport regression...
"%PYTHON%" -m pytest -q tests\test_incomplete_read_alpha1_2.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=16" & goto :fail_run

echo [7/25] Alpha1.3 UX/path regression...
"%PYTHON%" -m pytest -q tests\test_alpha1_3_features.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=17" & goto :fail_run

echo [8/25] Release hygiene...
"%PYTHON%" tools\release_cleanup.py --root . >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=18" & goto :fail_run

echo [9/25] Release builder audit...
"%PYTHON%" tools\build_release.py --root . --output "dist\_verification_only.zip" --archive-root "verification" --check-only >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=19" & goto :fail_run

echo [10/25] Compileall...
"%PYTHON%" -m compileall -q trading_robot desktop_gui.py portfolio_tool.py portfolio_cutover_tool.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=20" & goto :fail_run

echo [11/25] Full regression suite...
"%PYTHON%" -m pytest -q >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=21" & goto :fail_run

echo [12/25] Portfolio model and repository...
"%PYTHON%" -m pytest -q tests\test_portfolio_model_v3_7.py tests\test_portfolio_repository_v3_7.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=22" & goto :fail_run

echo [13/25] Portfolio reconciliation and manager...
"%PYTHON%" -m pytest -q tests\test_portfolio_reconciler_v3_7.py tests\test_portfolio_manager_v3_7.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=23" & goto :fail_run

echo [14/25] Portfolio infrastructure and GUI source...
"%PYTHON%" -m pytest -q tests\test_portfolio_infrastructure_v3_7.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=24" & goto :fail_run

echo [15/25] Bot, strategy and broker regression...
"%PYTHON%" -m pytest -q tests\test_bot.py tests\test_strategy.py tests\test_strategy_runtime.py tests\test_portfolio.py tests\test_tbank_sandbox.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=25" & goto :fail_run

echo [16/25] Recovery matrix...
"%PYTHON%" -m pytest -q tests\test_recovery_rc1.py tests\test_state_integrity_rc1.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=26" & goto :fail_run

echo [17/25] Runtime persistence and backup...
"%PYTHON%" -m pytest -q tests\test_runtime_bootstrap.py tests\test_runtime_backup_rc1.py tests\test_runtime_backup_rc1_2.py tests\test_state_persistence.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=27" & goto :fail_run

echo [18/25] Support bundle, secrets and readiness...
"%PYTHON%" -m pytest -q tests\test_support_readiness_rc1.py tests\test_security_rc1.py tests\test_rc_tool.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=28" & goto :fail_run

echo [19/25] MARKET_IDLE and transient API resilience...
"%PYTHON%" -m pytest -q tests\test_market_idle_rc1_3.py tests\test_gui_resilience_rc1_4.py tests\test_resilience.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=29" & goto :fail_run

echo [20/25] Standalone source and layout...
"%PYTHON%" -m pytest -q tests\test_standalone_rc1.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=30" & goto :fail_run

echo [21/25] Risk integration and audit...
"%PYTHON%" -m pytest -q tests\test_risk_runtime.py tests\test_risk_sandbox_integration.py tests\test_risk_control_tool.py tests\test_diagnostics.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=31" & goto :fail_run

echo [22/25] Risk Lab...
"%PYTHON%" risk_lab.py --output-dir risk_alpha_output >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=32" & goto :fail_run

echo [23/25] GUI canonical-only source checks...
"%PYTHON%" -c "from pathlib import Path; s=Path('desktop_gui.py').read_text(encoding='utf-8'); assert 'CanonicalPortfolioManager' in s; assert 'LegacyPortfolioManager' not in s; assert 'manager.recover_ownership(request)' in s; assert 'manager.acknowledge_external_close(request)' in s; assert 'initialdir=_reports_initial_dir()' in s; assert 'trading_events_v3_6_beta1.csv' not in s; print('GUI canonical-only operator paths: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=33" & goto :fail_run

echo [24/25] Runtime bootstrap and cutover CLI contract...
"%PYTHON%" runtime_setup.py --no-report >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=34" & goto :fail_run
"%PYTHON%" portfolio_cutover_tool.py --help >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=35" & goto :fail_run

echo [25/25] Portfolio CLI and manifest safety boundary...
"%PYTHON%" portfolio_tool.py inspect >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=36" & goto :fail_run
"%PYTHON%" -c "import json; m=json.load(open('build_manifest.json',encoding='utf-8')); assert m['multi_asset_execution'] is False; assert m['real_account_execution'] is False; print('Safety boundary: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=37" & goto :fail_run

set "RC=0"
"%PYTHON%" -c "from pathlib import Path; import sys; sys.stdout.write(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo.
echo Verification v3.7-alpha3 completed successfully.
echo Expected full suite: 443 passed; alpha3 targeted: 20 passed; alpha2 targeted: 19 passed; Risk Lab: 8/8 PASS.
echo Log: %LOG_FILE%
if "%NO_PAUSE%"=="0" pause
exit /b 0

:fail_run
"%PYTHON%" -c "from pathlib import Path; import sys; sys.stdout.write(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo.
echo [ERROR] Verification failed with code %RC%.
echo Log: %LOG_FILE%
if "%NO_PAUSE%"=="0" pause
exit /b %RC%

:fail_start
echo [ERROR] Verification could not start.
if "%NO_PAUSE%"=="0" pause
exit /b 1
