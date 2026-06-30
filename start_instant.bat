@echo off
REM ============================================================================
REM  BTC Quantum Trader - INSTANT START
REM  Launches the app DIRECTLY: no aggTrade downloads, no research-matrix rebuild,
REM  no model training, no startup backtest. It loads the already-trained FROZEN
REM  model + a tiny candle window and serves immediately.
REM
REM  Use this for normal daily launches. Use start.bat ONLY when you want to
REM  refresh data / rebuild the matrix / retrain the heads.
REM ============================================================================
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

REM --- Frozen: load the saved ensemble from disk, NEVER retrain on boot. ---
set "BTC_FREEZE_MODEL=1"
REM --- No post-boot validation replay (it pegs CPU for minutes). ---
set "BTC_RUN_STARTUP_BACKTEST=0"
REM --- Belt-and-braces: never run the offline data builders / head trainers. ---
set "BTC_SKIP_BACKFILL=1"
REM --- Boot candle window. This is the existing knob the app uses to decide how many days of
REM     1m/5m/15m candles to load on startup. The frozen model + chart + live features only need
REM     a few hours of trailing bars, so loading 3 days (instead of 100-150) is what makes boot
REM     near-instant. A tiny one-time kline fetch (~a few seconds) is still needed to have live
REM     data to serve - that is unavoidable for a live feed, but it is small, not the big backfill.
set "BTC_HISTORICAL_DAYS=3"
REM --- 5m UP-tilt fix: symmetric up-vs-down dead-zone, 5m ONLY (15m is already balanced). ---
REM     Neutralizes the measured +33.8pt 5m UP-lean skew by sending marginal coin-flip calls
REM     to NEUTRAL instead of the slightly-higher side. Set to 0 to revert. Verify after a day
REM     with: python backend\probe_direction_tilt.py
set "BTC_DIR_MARGIN_5=0.015"
REM --- Keep the live feed responsive (price/predictions). ---
set "BTC_MAIN_LOOP_SEC=3"

echo ============================================================
echo  BTC Quantum Trader - INSTANT START
echo  Frozen model, no downloads/training, ~3-day boot window.
echo ============================================================
echo [preflight] Verifying that the saved main ensemble matches the current code...
python backend\check_model_compatibility.py
if errorlevel 1 (
    echo [preflight] Instant start stopped to protect model quality.
    echo             Run start.bat once to train the current 5m/15m ensemble on the full window.
    echo             After that training completes and saves, start_instant.bat is safe again.
    pause
    exit /b 1
)
if "%BTC_SKIP_PM_RECORDER%"=="1" (
    echo [recorder] Polymarket recorder skipped: BTC_SKIP_PM_RECORDER=1.
) else (
    echo [recorder] Starting Polymarket quote + official-settlement recorder...
    start "BTC Polymarket Recorder" /min cmd /k call "%PROJECT_ROOT%start_recorder.bat"
)
echo [1/2] Starting Frontend Server (Vite)...
start "BTC Frontend" cmd /k "npm run dev"
echo [2/2] Starting Backend API (Port 8000)...
python -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8000

pause
