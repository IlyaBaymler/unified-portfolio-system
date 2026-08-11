@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pytest -q tests\test_gui_resilience_rc1_4.py
) else (
  python -m pytest -q tests\test_gui_resilience_rc1_4.py
)
pause
