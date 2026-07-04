@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

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
