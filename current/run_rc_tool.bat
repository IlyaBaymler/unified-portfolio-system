@echo off
setlocal
cd /d "%~dp0"
call prepare_environment.bat test
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" rc_tool.py %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
:fail
echo Failed to prepare environment.
pause
exit /b 1
