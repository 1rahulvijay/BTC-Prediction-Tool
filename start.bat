@echo off
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
REM All app-generated files (DuckDB, signal_history.pkl, saved_models, cache) live under
REM this project's data\ folder. IMPORTANT: keep OneDrive sync OFF for the Documents folder
REM so its sync service / IDE indexers cannot lock these files mid-write.
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
REM === TRAINING WINDOW (DAYS) ============================================
REM Historical training window in DAYS. 40 = current (v5-classbal run, 2026-06-12):
REM   +33%% samples vs 30, dilutes the one-sided -21%% month, no RAM risk on 16GB
REM   (~1.8GB tensor), train ~5.5-6.5h. 90 needs 10-16GB + a 90-day backfill (V5.md §2.6).
REM   1  = ~24h DEBUG smoke-test only (fast, NOT accurate).
REM HEADS-UP: a MULTI-HOUR train (overnight), and the TREND/RANGE/VOLATILE regime buckets
REM train too. The dashboard stays usable throughout (non-blocking boot). The
REM microstructure features only fill from UPTIME, so after training, LEAVE IT RUNNING.
REM 30 = quick overnight run (2026-06-13): ~half the train time of 60, ~43k samples is enough to
REM validate the v7 pipeline + heads. Bump to 60 for the keeper once 30 looks sane. BACKFILL follows.
if not defined BTC_HISTORICAL_DAYS set "BTC_HISTORICAL_DAYS=60"
REM === DATA BACKFILL WINDOW (DAYS) =======================================
REM ONE knob for ALL three offline data builders (trade-features, persistence, cross-venue).
REM Defaults to the training window so a single change covers both. Want 60/90 days of data?
REM set BTC_BACKFILL_DAYS=60  (or 90) here or in the environment — all scripts follow it.
if not defined BTC_BACKFILL_DAYS set "BTC_BACKFILL_DAYS=%BTC_HISTORICAL_DAYS%"
REM === TRAIN/HOLDOUT SPLIT ===============================================
REM Fraction of data used to FIT the base models. The remaining tail is the HOLDOUT,
REM used to (a) conformal-calibrate the magnitude bands and (b) score the OOS backtest.
REM A holdout is MANDATORY — code clamps this to [0.50, 0.98]. 0.95 = "use almost all the
REM data" (operator 2026-06-14, wanted ~100%) while keeping ~5% recent rows to keep the
REM expected-drop/up bands honest. Literal 1.0 would make the bands too narrow + backtest
REM in-sample, so it is intentionally not allowed.
if not defined BTC_TRAIN_SPLIT_FRAC set "BTC_TRAIN_SPLIT_FRAC=0.98"
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
REM FROZEN (operator 2026-06-14, post-60d-retrain): 1 = no auto/scheduled retraining. The
REM purged walk-forward showed ALL horizons at the information ceiling (1m 0.36 -> 30m 0.50,
REM below_chance) — retraining cannot lift that, it only burns ~6h and freezes the feed. The
REM saved v8 model's arch MATCHES the code, so boot LOADS it (no startup retrain). Set back to
REM 0 only for a deliberate, operator-chosen retrain (e.g. new features / longer window).
set "BTC_FREEZE_MODEL=1"
REM Heavy prediction loop interval (s). 3 = ~33%% less inference CPU than 2, with no
REM visible UI change (live price/charts/Polymarket run on separate fast tickers).
set "BTC_MAIN_LOOP_SEC=3"
REM Booster thread cap: training uses this many cores, leaving the rest for the live app.
REM 12 = overnight mode (browser/IDE closed): ~20-25%% faster train, feeds still get 4
REM cores so the signal recorder keeps accruing coverage during the train. Drop to 10 if
REM you want to actively use the dashboard while it trains.
if not defined BTC_TRAIN_THREADS set "BTC_TRAIN_THREADS=12"
REM Cap the OTHER parallel libs (HistGradientBoosting/numpy/BLAS use OpenMP, NOT n_jobs) to
REM the same budget — without this they'd still grab all 16 cores and freeze the feed.
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=12"
if not defined OPENBLAS_NUM_THREADS set "OPENBLAS_NUM_THREADS=12"
if not defined MKL_NUM_THREADS set "MKL_NUM_THREADS=12"
REM Retrain at most ~once a day so each retrain learns from a meaningful chunk of NEW data
REM (and the UI isn't freezing every few hours). 86400s = 24h.
if not defined BTC_AUTO_RELEARN_COOLDOWN_SEC set "BTC_AUTO_RELEARN_COOLDOWN_SEC=86400"
if not defined BTC_SCHEDULED_RELEARN_SEC set "BTC_SCHEDULED_RELEARN_SEC=86400"
REM FSR-PPO challenger: mothballed in v6 (strategy layer premature pre-edge). 1 = revive.
if not defined BTC_FSR_PPO set "BTC_FSR_PPO=0"
REM Speed knobs — DELIBERATELY OFF (operator decision 2026-06-12: accuracy/precision is
REM the absolute priority; no sample or iteration caps below the model.py defaults).
REM Training runs at full data budgets (12000 linear/magnitude/quantile, 6000 stacker,
REM 350 SGD iters). Speed comes ONLY from accuracy-neutral levers: thread count above,
REM and (future, V5.md §4.5) structural cuts IF each proves accuracy-neutral on the
REM held-out scorecard first.
REM set "BTC_QUANTILE_MAX_SAMPLES=6000"
REM set "BTC_MOVE_SIZE_MAX_SAMPLES=6000"
REM set "BTC_LINEAR_MAX_SAMPLES=8000"
REM set "BTC_STACKER_MAX_SAMPLES=4000"
REM set "BTC_SGD_MAX_ITER=250"
REM set "BTC_QUANTILE_REGIME_SCOPE=NONE"

echo Starting BTC Quantum Trader...

REM === TRADE-FEATURE BACKFILL (incremental) ==============================
REM Updates data\trade_features_backfill.parquet BEFORE the app starts so the
REM retrain/backtest always see full CVD/VPIN/large-trade history.
REM   - First ever run: full ~30-day download (multi-GB, can take a while).
REM   - After that: only the days since the last run (usually ~1 day, fast).
REM   - Already current: instant no-op.
REM Set BTC_SKIP_BACKFILL=1 to skip (e.g. offline). A failure NEVER blocks the
REM app — the overlay just falls back to whatever history already exists.
REM All three offline data builders run BEFORE the app, incrementally (--auto only fetches
REM days missing since the last run). First-ever run downloads the full %BTC_HISTORICAL_DAYS%-day
REM window per source (multi-GB, slow ONCE); afterwards each is a fast ~1-day top-up or no-op.
REM They share data\backfill_cache\ (spot CSVs are reused across builders). A failure NEVER
REM blocks the app. Set BTC_SKIP_BACKFILL=1 to skip all three (offline).
if "%BTC_SKIP_BACKFILL%"=="1" (
    echo [0/3] Data backfills skipped: BTC_SKIP_BACKFILL=1.
) else (
    echo [0/3] a. Updating trade-feature backfill - CVD/VPIN/large-trade...
    python backend\backfill_trade_features.py --auto --days %BTC_BACKFILL_DAYS%
    if errorlevel 1 echo [0/3a] Trade-feature backfill failed - continuing with existing data.
    echo [0/3] b. Updating A1 persistence dataset - late-entry/T3 engine...
    python backend\build_persistence_dataset.py --auto --days %BTC_BACKFILL_DAYS%
    if errorlevel 1 echo [0/3b] Persistence build failed - continuing.
    echo [0/3] c. Updating A4 cross-venue flow - spot-vs-perp divergence...
    python backend\build_crossvenue_flow.py --auto --days %BTC_BACKFILL_DAYS%
    if errorlevel 1 echo [0/3c] Cross-venue build failed - continuing.
    echo [0/3] d. Specialized heads - VERSION-AWARE - retrain a head only if MISSING or its
    echo          HEAD_VERSION changed, same idea as the ensemble arch. Bump a trainer's
    echo          HEAD_VERSION to force just that head to rebuild; else restarts skip them:
    python backend\train_heads.py
    if errorlevel 1 echo [0/3d] Head training had an issue - continuing.
    echo [0/3] e. Data-quality health check - last 3 days - report only:
    python backend\data_quality_audit.py --days 3
    if errorlevel 1 echo [0/3e] Data-quality audit skipped - continuing.
    echo [0/3] f. Cleanup superseded research artifacts - safe allow-list only:
    python backend\cleanup_artifacts.py --apply
    if errorlevel 1 echo [0/3f] Cleanup skipped - continuing.
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
