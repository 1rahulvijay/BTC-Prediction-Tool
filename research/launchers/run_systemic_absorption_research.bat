@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  Systemic Absorption Fragility Research - NO LIVE CHANGES
echo  Downloads cached ETH/SOL 5m archives; default window 180d
echo ============================================================
python -u backend\research\test_systemic_absorption_fragility.py %*
set "RC=%ERRORLEVEL%"
echo.
echo Systemic absorption research exited with code %RC%.
pause
exit /b %RC%
