@echo off
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
REM All app-generated files (DuckDB, signal_history.pkl, saved_models, cache) live under
REM this project's data\ folder. IMPORTANT: keep OneDrive sync OFF for the Documents folder
REM so its sync service / IDE indexers cannot lock these files mid-write.
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
REM === TRAINING WINDOW (DAYS) ============================================
REM Historical training window in DAYS. 30 = the real evidence run (current).
REM   1  = ~24h DEBUG smoke-test only (fast, NOT accurate).
REM   30 = recommended sweet spot: ~32k training rows, reflects the CURRENT regime,
REM        tolerable train time. 45 = more regime variety; 60 = diminishing returns + staler.
REM HEADS-UP: the tree models are UNCAPPED, so 30 days = a MULTI-HOUR train (possibly
REM overnight), and the TREND/RANGE/VOLATILE regime buckets now train too. The dashboard
REM stays usable throughout (non-blocking boot). The microstructure features only fill
REM from UPTIME, so after training, LEAVE IT RUNNING for days to accrue real coverage.
if not defined BTC_HISTORICAL_DAYS set "BTC_HISTORICAL_DAYS=30"
REM =======================================================================
REM Run a validation backtest automatically on startup (1 = on, 0 = off).
REM It runs in the BACKGROUND after the app is ready, so it does not block live trading.
if not defined BTC_RUN_STARTUP_BACKTEST set "BTC_RUN_STARTUP_BACKTEST=1"
REM Backtest window: recent N rows (faster) or 0 = full historical replay (heavy on a laptop).
if not defined BTC_BACKTEST_MAX_ROWS set "BTC_BACKTEST_MAX_ROWS=12000"
REM FREEZE MODE (set to 1 for this 16GB machine): 1 = no auto/scheduled retraining, so the
REM model is STABLE and the live feed NEVER freezes (a background retrain pegs all cores for
REM hours and this box has no headroom for that). 0 = auto-improve, but on 16GB the feed WILL
REM freeze during each ~4.6h retrain. To improve the model, retrain manually (POST /api/relearn
REM or set this to 0 briefly) when you can leave it overnight with the IDE/browser closed.
set "BTC_FREEZE_MODEL=0"
REM Heavy prediction loop interval (s). 3 = ~33%% less inference CPU than 2, with no
REM visible UI change (live price/charts/Polymarket run on separate fast tickers).
set "BTC_MAIN_LOOP_SEC=3"
REM Booster thread cap: training uses this many cores, leaving the rest for the live app.
REM 10 of 16 -> 6 cores reserved for price/feeds/UI during a retrain (was 12/4: the live
REM Polymarket ticker + WS feeds visibly stuttered during the startup retrain). Retrain
REM takes ~20%% longer but the app stays usable. Raise back to 12 for a pure overnight
REM run with the browser closed.
if not defined BTC_TRAIN_THREADS set "BTC_TRAIN_THREADS=10"
REM Cap the OTHER parallel libs (HistGradientBoosting/numpy/BLAS use OpenMP, NOT n_jobs) to
REM the same budget — without this they'd still grab all 16 cores and freeze the feed.
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=10"
if not defined OPENBLAS_NUM_THREADS set "OPENBLAS_NUM_THREADS=10"
if not defined MKL_NUM_THREADS set "MKL_NUM_THREADS=10"
REM Retrain at most ~once a day so each retrain learns from a meaningful chunk of NEW data
REM (and the UI isn't freezing every few hours). 86400s = 24h.
if not defined BTC_AUTO_RELEARN_COOLDOWN_SEC set "BTC_AUTO_RELEARN_COOLDOWN_SEC=86400"
if not defined BTC_SCHEDULED_RELEARN_SEC set "BTC_SCHEDULED_RELEARN_SEC=86400"
REM Speed knobs for laptop training:
REM set "BTC_QUANTILE_REGIME_SCOPE=NONE"
REM set "BTC_QUANTILE_MAX_SAMPLES=6000"
REM set "BTC_MOVE_SIZE_MAX_SAMPLES=6000"
REM set "BTC_LINEAR_MAX_SAMPLES=8000"
REM set "BTC_STACKER_MAX_SAMPLES=4000"
REM set "BTC_SGD_MAX_ITER=250"

echo Starting BTC Quantum Trader...

REM === TRADE-FEATURE BACKFILL (incremental) ==============================
REM Updates data\trade_features_backfill.parquet BEFORE the app starts so the
REM retrain/backtest always see full CVD/VPIN/large-trade history.
REM   - First ever run: full ~30-day download (multi-GB, can take a while).
REM   - After that: only the days since the last run (usually ~1 day, fast).
REM   - Already current: instant no-op.
REM Set BTC_SKIP_BACKFILL=1 to skip (e.g. offline). A failure NEVER blocks the
REM app — the overlay just falls back to whatever history already exists.
if "%BTC_SKIP_BACKFILL%"=="1" (
    echo [0/3] Backfill skipped: BTC_SKIP_BACKFILL=1.
) else (
    echo [0/3] Updating trade-feature backfill data...
    python backend\backfill_trade_features.py --auto
    if errorlevel 1 echo [0/3] Backfill failed - continuing with existing data.
)
REM =======================================================================

echo [1/3] Checking dependencies...
python -c "import duckdb" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing duckdb...
    pip install duckdb
)

echo [2/3] Starting Frontend Server (Vite)...
start cmd /k "npm run dev"

echo [3/3] Starting Backend API (Port 8000)...
if "%BTC_DEV_RELOAD%"=="1" (
    echo Backend reload mode enabled. Set BTC_DEV_RELOAD=0 for stable long runs.
    python -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8000 --reload --reload-dir "%PROJECT_ROOT%backend"
) else (
    echo Backend reload mode disabled for stable long runs.
    python -m uvicorn server:app --app-dir backend --host 127.0.0.1 --port 8000
)

pause
