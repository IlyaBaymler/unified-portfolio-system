@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Build MOEX Research Robot v3.10.0 Stable Standalone

call prepare_environment.bat --with-dev
if errorlevel 1 goto :fail
set "PYTHON=%CD%\.venv\Scripts\python.exe"

"%PYTHON%" -m PyInstaller --clean --noconfirm --distpath "build\pyinstaller_dist" --workpath "build\pyinstaller_work" MOEXResearchRobot.spec
if errorlevel 1 goto :fail

set "OUT=%CD%\dist\MOEX_Research_Robot_v3_10_0"
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%\app" "%OUT%\runtime" "%OUT%\backups" "%OUT%\reports" "%OUT%\logs" "%OUT%\support"
xcopy /e /i /y "build\pyinstaller_dist\MOEXResearchRobot\*" "%OUT%\app\" >nul
copy /y portable_launcher.bat "%OUT%\MOEX Research Robot.bat" >nul
copy /y README.md "%OUT%\README.md" >nul
copy /y START_HERE_WINDOWS.md "%OUT%\START_HERE_WINDOWS.md" >nul
copy /y V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md "%OUT%\V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md" >nul
copy /y build_manifest.json "%OUT%\app\build_manifest.json" >nul

"%PYTHON%" tools\verify_standalone_layout.py --root "%OUT%" --expected-version "0.3.10" --expected-channel "stable" --minimum-risk-state-schema 4
if errorlevel 1 goto :fail

"%PYTHON%" -c "import sys; from tools import release_cleanup as c; c.CURRENT_FILES.add('V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md'); from tools.build_release import main; sys.exit(main(['--root',r'%OUT%','--output',r'%CD%\dist\moex_trading_robot_standalone_v3_10_0.zip','--archive-root','MOEX_Research_Robot_v3_10_0']))"
if errorlevel 1 goto :fail

echo.
echo Standalone package created:
echo %OUT%
echo %CD%\dist\moex_trading_robot_standalone_v3_10_0.zip
pause
exit /b 0

:fail
echo.
echo [ERROR] Standalone build failed.
pause
exit /b 1
