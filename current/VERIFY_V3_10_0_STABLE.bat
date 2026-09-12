@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1"
cd /d "%~dp0"
title MOEX Research Robot v3.10.0 Stable Release Candidate Verification

set "NO_PAUSE=0"
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail_start
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "verification_output" mkdir "verification_output"
if not exist "verification_output\system-temp" mkdir "verification_output\system-temp"
set "TEMP=%CD%\verification_output\system-temp"
set "TMP=%CD%\verification_output\system-temp"
set "LOG_FILE=verification_output\verify_v3_10_0_stable.log"
> "%LOG_FILE%" echo === MOEX Research Robot v3.10.0 Stable release-candidate verification ===

echo [1/9] Exact version, manifest and candidate safety boundary...
"%PYTHON%" -c "import json,trading_robot; m=json.load(open('build_manifest.json',encoding='utf-8')); q=m['v3_10_cl8_release_cut']; assert trading_robot.__version__=='0.3.10'; assert m['software_version']=='0.3.10'; assert m['display_version']=='v3.10.0'; assert m['release_channel']=='stable'; assert m['sandbox_only'] and not m['real_account_execution']; assert q['status']=='candidate-not-accepted-not-published'; assert q['q0']=='PASS'; assert q['q1_q7']=='NOT_RUN'; assert not q['q9_stable_acceptance']; assert not q['publication_authorized']; print('Candidate safety boundary: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=11" & goto :fail_run

echo [2/9] CL8 qualification infrastructure contracts...
"%PYTHON%" -m pytest -q --basetemp verification_output\pytest-cl8 -p no:cacheprovider tests\test_v3_10_stable_qualification.py -k "not test_exact_qualification_delta_and_predecessor_immutability" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=12" & goto :fail_run

echo [3/9] Active v3.10 release files and stale v3.9 root cleanup...
"%PYTHON%" -c "from pathlib import Path; required={'CHANGELOG_V3_10_0_STABLE_RU.md','RELEASE_MANIFEST_V3_10_0_STABLE.txt','UPDATE_TO_V3_10_0_STABLE.md','V3_10_0_STABLE_ARCHITECTURE_RU.md','V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md','V3_10_0_STABLE_TEST_PLAN_RU.md','VERIFY_V3_10_0_STABLE.bat','install_and_verify_v3_10_0.bat'}; old={'CHANGELOG_V3_9_0_STABLE_RU.md','MASTER_UPDATE_2026-08-15_V3_9_0_STABLE_RU.md','RELEASE_MANIFEST_V3_9_0_STABLE.txt','UPDATE_TO_V3_9_0_STABLE.md','V3_9_0_STABLE_ARCHITECTURE_RU.md','V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md','V3_9_0_STABLE_TEST_PLAN_RU.md','VERIFY_V3_9_0_STABLE.bat','install_and_verify_v3_9_0.bat'}; missing=sorted(x for x in required if not Path(x).is_file()); stale=sorted(x for x in old if Path(x).exists()); assert not missing,(missing); assert not stale,(stale); print('Release root identity: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=13" & goto :fail_run

echo [4/9] Deterministic source-builder file audit...
"%PYTHON%" -c "import sys; from tools import release_cleanup as c; c.CURRENT_FILES.clear(); c.CURRENT_FILES.update({'CHANGELOG_V3_10_0_STABLE_RU.md','RELEASE_MANIFEST_V3_10_0_STABLE.txt','UPDATE_TO_V3_10_0_STABLE.md','V3_10_0_STABLE_ARCHITECTURE_RU.md','V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md','V3_10_0_STABLE_TEST_PLAN_RU.md','VERIFY_V3_10_0_STABLE.bat','install_and_verify_v3_10_0.bat','restore_stable_default_risk_profile.bat'}); from tools.build_release import main; sys.exit(main(['--root','.','--output','verification_output/_verification_only.zip','--archive-root','verification','--check-only']))" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=14" & goto :fail_run

echo [5/9] Dependency integrity and Ruff...
"%PYTHON%" -m pip check >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=15" & goto :fail_run
"%PYTHON%" -m ruff check --select E9,F63,F7,F82 . >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=16" & goto :fail_run

echo [6/9] Compileall...
"%PYTHON%" -m compileall -q -f trading_robot tools tests >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=17" & goto :fail_run

echo [7/9] Full regression with exact POST_RELEASE_CUT comparator...
"%PYTHON%" -m pytest -q -rfE --basetemp verification_output\pytest-full -p no:cacheprovider > "verification_output\pytest-full.log" 2>&1
"%PYTHON%" -c "from pathlib import Path; import json,re,sys; from tools.v3_10_stable_qualification import RegressionStage,compare_regression_failures; text=Path(r'verification_output\pytest-full.log').read_text(encoding='utf-8',errors='replace'); ids=re.findall(r'^(?:FAILED|ERROR)\s+([^\s]+)(?:\s+-.*)?$',text,re.M); d=compare_regression_failures(ids,stage=RegressionStage.POST_RELEASE_CUT); print(json.dumps(d.to_dict(),sort_keys=True)); raise SystemExit(0 if d.passed else 1)" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=18" & goto :fail_run

echo [8/9] Static release identity consistency...
"%PYTHON%" -c "from pathlib import Path; values=['README.md','START_HERE_WINDOWS.md','CHANGELOG_V3_10_0_STABLE_RU.md','RELEASE_MANIFEST_V3_10_0_STABLE.txt','UPDATE_TO_V3_10_0_STABLE.md','V3_10_0_STABLE_ARCHITECTURE_RU.md','V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md','V3_10_0_STABLE_TEST_PLAN_RU.md']; bad=[p for p in values if 'v3.10.0' not in Path(p).read_text(encoding='utf-8')]; assert not bad,bad; print('Static release identity: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=19" & goto :fail_run

echo [9/9] Authority boundary...
"%PYTHON%" -c "import json; q=json.load(open('build_manifest.json',encoding='utf-8'))['v3_10_cl8_release_cut']; assert not any(q[k] for k in ('provider_access_authorized','controlled_clock_experiment_authorized','sandbox_experiment_authorized','tag_or_github_release_authorized')); print('No provider/experiment/publication authority: PASS')" >> "%LOG_FILE%" 2>&1
if errorlevel 1 set "RC=20" & goto :fail_run

set "RC=0"
"%PYTHON%" -c "from pathlib import Path; print(Path(r'%LOG_FILE%').read_text(encoding='utf-8',errors='replace'))"
echo Automated v3.10.0 release-candidate verification completed successfully.
echo Q1-Q8 evidence and explicit Stable acceptance remain separate gates.
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
