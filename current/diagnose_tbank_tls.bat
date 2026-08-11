@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Run install_and_run_gui.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" diagnose_tbank_tls.py
echo.
pause
