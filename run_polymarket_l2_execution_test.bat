@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\polymarket;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

echo ============================================================
echo  Polymarket Exact Depth VWAP + Queue Scenario Report
echo  Read-only research. It never places an order.
echo ============================================================

python -u backend\research\test_polymarket_l2_execution.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo L2 execution report completed successfully.
) else (
  echo L2 execution report exited with code %RC%.
)
pause
exit /b %RC%
