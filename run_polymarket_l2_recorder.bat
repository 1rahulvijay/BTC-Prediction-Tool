@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\polymarket;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

echo ============================================================
echo  Polymarket Full L2 Recorder - DATA ONLY, NO ORDERS
echo  Full book + level updates + trades + exact depth VWAP
echo  Ctrl+C stops safely. Database: data\polymarket_l2.duckdb
echo ============================================================

python -u backend\polymarket\l2_recorder.py %*
set "RC=%ERRORLEVEL%"
echo.
echo L2 recorder exited with code %RC%.
pause
exit /b %RC%
