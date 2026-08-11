@echo off
setlocal
cd /d "%~dp0"
title MOEX Strategy Research Lab - Setup

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found.
        echo Install Python 3.11 or newer and enable Add python.exe to PATH.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 goto :error
)

echo [2/4] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/4] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/4] Starting Research Lab...
".venv\Scripts\python.exe" -m streamlit run research_lab.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Startup failed. Read the error above.
pause
exit /b 1
