@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

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
