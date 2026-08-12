@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MOEX v3.7.0 Recovery Tests
if not exist ".venv\Scripts\python.exe" call prepare_environment.bat --with-dev
".venv\Scripts\python.exe" -m pytest -q tests\test_recovery_rc1.py tests\test_state_integrity_rc1.py
pause
