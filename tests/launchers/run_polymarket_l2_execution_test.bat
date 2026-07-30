@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\polymarket;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

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
