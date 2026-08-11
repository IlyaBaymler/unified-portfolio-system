@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "WITH_DEV=0"
if /I "%~1"=="--with-dev" set "WITH_DEV=1"

where py >nul 2>nul
if %errorlevel%==0 (
    set "BASE_PY=py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3.11 or newer was not found.
        echo Install Python and enable "Add python.exe to PATH".
        exit /b 1
    )
    set "BASE_PY=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    %BASE_PY% -m venv .venv
    if errorlevel 1 exit /b 1
) else (
    echo [1/4] Virtual environment already exists.
)

set "PYTHON=%CD%\.venv\Scripts\python.exe"

echo [2/4] Updating pip...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [3/4] Installing runtime dependencies...
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

if "%WITH_DEV%"=="1" (
    echo [4/4] Installing test dependencies...
    "%PYTHON%" -m pip install -r requirements-dev.txt
    if errorlevel 1 exit /b 1
) else (
    echo [4/4] Test dependencies were not requested.
)

exit /b 0
