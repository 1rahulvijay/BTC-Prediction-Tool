@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

echo ============================================================
echo  Binance USD-M Sequenced L2 Recorder - DATA ONLY
echo  REST snapshot + 100ms diff depth + gap/resync ledger
echo  Places NO orders. Database: data\binance_l2.duckdb
echo  Ctrl+C stops safely. Default size cap: 10 GB.
echo ============================================================

python -u backend\venues\binance_l2_recorder.py --max-db-gb 10 %*
set "RC=%ERRORLEVEL%"
echo.
echo Binance L2 recorder exited with code %RC%.
pause
exit /b %RC%
