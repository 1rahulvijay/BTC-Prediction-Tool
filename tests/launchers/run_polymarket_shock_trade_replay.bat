@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  Polymarket BTC-Shock Share Replay - PAPER ONLY
echo  Recorded ask entry, bid exit, fees and latency scenarios
echo ============================================================

python -u backend\research\test_polymarket_shock_trade_replay.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo Shock share replay completed successfully.
) else (
  echo Shock share replay failed with exit code %RC%.
)
pause
exit /b %RC%
