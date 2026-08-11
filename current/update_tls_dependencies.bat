@echo off
setlocal
cd /d "%~dp0"
title MOEX Robot v3.2 TLS Update
if not exist ".venv\Scripts\python.exe" (
    call install_and_run_gui.bat
    exit /b %errorlevel%
)
echo Updating HTTPS certificate support...
".venv\Scripts\python.exe" -m pip install --upgrade requests certifi truststore
if errorlevel 1 goto :error
echo.
echo Dependencies updated.
echo If TLS still fails, install NUC certificates from the official Gosuslugi page.
start "" "https://www.gosuslugi.ru/crt"
echo Then fully close and restart the program.
pause
exit /b 0
:error
echo Update failed. Check internet access and try install_and_run_gui.bat.
pause
exit /b 1
