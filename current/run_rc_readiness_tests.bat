@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX v3.7-alpha3 Readiness Backup Security Tests
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
".venv\Scripts\python.exe" -m pytest -q tests\test_runtime_backup_rc1.py tests\test_runtime_backup_rc1_2.py tests\test_support_readiness_rc1.py tests\test_security_rc1.py tests\test_rc_tool.py tests\test_standalone_rc1.py
pause
