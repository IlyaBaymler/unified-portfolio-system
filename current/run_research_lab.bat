@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запустите install_and_run_research_lab.bat для установки окружения.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run research_lab.py
if errorlevel 1 pause
endlocal
