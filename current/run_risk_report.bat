@echo off
setlocal
cd /d "%~dp0"
title MOEX Risk Dashboard and Burn-in Report v3.9.0

call prepare_environment.bat --with-dev
if errorlevel 1 goto :error

if not exist "risk_beta_output" mkdir "risk_beta_output"
".venv\Scripts\python.exe" risk_report.py all --output-dir risk_beta_output
if errorlevel 1 goto :error

echo.
echo Reports created in risk_beta_output\
echo   risk_dashboard_snapshot.json
echo   risk_burn_in_report.json
echo   risk_burn_in_checks.csv
echo   risk_burn_in_metrics.csv
echo.
pause
exit /b 0

:error
echo.
echo Risk report failed. See the error above.
pause
exit /b 1
