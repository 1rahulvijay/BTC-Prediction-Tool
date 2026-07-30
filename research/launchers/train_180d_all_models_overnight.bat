@echo off
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

echo ============================================================
echo BTC Quantum Trader - 180d Overnight All-Model Training
echo ============================================================
echo This run is intentionally heavy. Leave the laptop plugged in.
echo It will train sequentially:
echo   1. Data backfills
echo   2. 180d research matrix
echo   3. All standalone heads one by one
echo   4. Main ensemble from backend startup
echo   5. Startup backtest after training
echo ============================================================

set "BTC_OVERNIGHT_TRAIN_ALL=1"
set "BTC_HISTORICAL_DAYS=180"
set "BTC_BACKFILL_DAYS=180"
set "BTC_TRAIN_SPLIT_FRAC=0.98"
set "BTC_FORCE_HEAD_RETRAIN=1"
set "BTC_FORCE_MAIN_RETRAIN=1"
set "BTC_FREEZE_MODEL=0"
set "BTC_RUN_STARTUP_BACKTEST=1"
set "BTC_BACKTEST_MAX_ROWS=12000"
set "BTC_DEV_RELOAD=0"
set "BTC_SKIP_BACKFILL=0"

REM Keep enough CPU free that WebSockets and Windows do not starve during the long run.
set "BTC_TRAIN_THREADS=10"
set "OMP_NUM_THREADS=10"
set "OPENBLAS_NUM_THREADS=10"
set "MKL_NUM_THREADS=10"
set "BTC_MAIN_LOOP_SEC=3"

echo Training window confirmed: BTC_HISTORICAL_DAYS=%BTC_HISTORICAL_DAYS%, BTC_BACKFILL_DAYS=%BTC_BACKFILL_DAYS%
echo Force flags confirmed: BTC_FORCE_HEAD_RETRAIN=%BTC_FORCE_HEAD_RETRAIN%, BTC_FORCE_MAIN_RETRAIN=%BTC_FORCE_MAIN_RETRAIN%

call "%PROJECT_ROOT%start.bat"
