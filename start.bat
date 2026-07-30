@echo off
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
REM All app-generated files (DuckDB, signal_history.pkl, saved_models, cache) live under
REM this project's data\ folder. IMPORTANT: keep OneDrive sync OFF for the Documents folder
REM so its sync service / IDE indexers cannot lock these files mid-write.
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
REM Binance perpetual paper trading is a separate research engine. It cannot start
REM from the UI unless this environment hard gate was enabled before launch.
if not defined BTC_ENABLE_BINANCE_PAPER set "BTC_ENABLE_BINANCE_PAPER=0"
REM === 5m UP-TILT FIX (serving, no retrain) =============================
REM Symmetric up-vs-down dead-zone applied to 5m ONLY (15m is already balanced: tilt -0.0pt).
REM Neutralizes the measured +34pt 5m UP-lean skew by sending marginal coin-flip calls to
REM NEUTRAL instead of the slightly-higher side. Set to 0 to revert. After a day, verify with:
REM   python backend\probe_direction_tilt.py
if not defined BTC_DIR_MARGIN_5 set "BTC_DIR_MARGIN_5=0.015"
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
REM SINGLE KNOB: this also drives the 1m research matrix (step c2) and therefore EVERY specialist
REM head (big-move/up/down/drop/activity, path forecaster, FADE model). Set the window here and every
REM model retrains on it. Long windows are resumable through the daily-file cache; the current
REM 1265d source-complete window is still a multi-day first build.
REM 1265d current executable window (2026-07-04): manifest-backed coverage starts 2023-01-15.
REM Do not describe this artifact as a 1500d/2022-bear model. Made safer by the bps-label upgrade
REM (labels remain comparable across price levels) and the
REM VALIDATED-REFIT flow (each head measures on its untouched recent tail,
REM then -- gate permitting -- refits production on all rows with rotated calibration; candidate test
REM metrics are preserved in every bundle as the honest record). The 98/2 split leaves ~30 recent days
REM genuinely unseen by the candidates. First build: ~200-230GB of new daily spot/perp downloads and a
REM MULTI-DAY (2-5 day) run on this laptop; files are cached and reused by all builders afterwards.
REM Main direction learners remain capped to a representative 40k samples because the measured
REM endpoint-direction ceiling is ~coin-flip; specialist path/risk heads consume the full matrix.
if not defined BTC_HISTORICAL_DAYS set "BTC_HISTORICAL_DAYS=1265"
REM Keep model provenance separate from the small candle window used by instant/production boot.
if not defined BTC_MODEL_TRAINING_DAYS set "BTC_MODEL_TRAINING_DAYS=%BTC_HISTORICAL_DAYS%"
REM === DATA BACKFILL WINDOW (DAYS) =======================================
REM ONE knob for ALL three offline data builders (trade-features, persistence, cross-venue).
REM Defaults to the training window so a single change covers both. Want 60/90 days of data?
REM set BTC_BACKFILL_DAYS=60  (or 90) here or in the environment — all scripts follow it.
if not defined BTC_BACKFILL_DAYS set "BTC_BACKFILL_DAYS=%BTC_HISTORICAL_DAYS%"
REM One-shot full retrain: marker is written only after all heads and the main ensemble save.
set "BTC_RETRAIN_COMPLETION_MARKER=%BTC_DATA_DIR%\saved_models\full_retrain_%BTC_HISTORICAL_DAYS%d_complete.json"
if "%BTC_FORCE_FULL_RETRAIN%"=="1" if exist "%BTC_RETRAIN_COMPLETION_MARKER%" del /q "%BTC_RETRAIN_COMPLETION_MARKER%"
REM Backward-compatible alias retained for older operator notes/scripts.
if "%BTC_FORCE_360_RETRAIN%"=="1" if exist "%BTC_RETRAIN_COMPLETION_MARKER%" del /q "%BTC_RETRAIN_COMPLETION_MARKER%"
if exist "%BTC_RETRAIN_COMPLETION_MARKER%" (
    set "BTC_FORCE_HEAD_RETRAIN=0"
    set "BTC_FORCE_MAIN_RETRAIN=0"
    set "BTC_OVERNIGHT_TRAIN_ALL=0"
    echo [mode] Completed %BTC_HISTORICAL_DAYS%d bundle found. Models remain frozen.
) else (
    set "BTC_FORCE_HEAD_RETRAIN=1"
    set "BTC_FORCE_MAIN_RETRAIN=1"
    set "BTC_OVERNIGHT_TRAIN_ALL=1"
    echo [mode] No completion marker. Forcing one full %BTC_HISTORICAL_DAYS%d retrain.
)
REM === TRAIN/HOLDOUT SPLIT ===============================================
REM Fraction of data used to FIT the base models. The remaining tail is the HOLDOUT,
REM used to (a) conformal-calibrate the magnitude bands and (b) score the OOS backtest.
REM A holdout is MANDATORY — code clamps this to [0.50, 0.98]. 0.95 = "use almost all the
REM data" (operator 2026-06-14, wanted ~100%) while keeping ~5% recent rows to keep the
REM expected-drop/up bands honest. Literal 1.0 would make the bands too narrow + backtest
REM in-sample, so it is intentionally not allowed.
if not defined BTC_TRAIN_SPLIT_FRAC set "BTC_TRAIN_SPLIT_FRAC=0.98"
REM === EVALUATE -> FULL REFIT -> LIVE SHADOW =============================
REM The 98%% candidate must pass its untouched 2%% tail before a second model is fit on
REM all rows. The full-data model is staged, reloaded, smoke-tested, then installed as a
REM SILENT A/B challenger. The incumbent keeps driving decisions until >=30 calendar days,
REM >=500 resolved predictions, PF>1.20 and positive expectancy all pass live.
if not defined BTC_FULL_REFIT_AFTER_GATE set "BTC_FULL_REFIT_AFTER_GATE=1"
if not defined BTC_PROMOTION_MIN_HOLDOUT_SAMPLES set "BTC_PROMOTION_MIN_HOLDOUT_SAMPLES=1000"
if not defined BTC_PROMOTION_MIN_DIRECTIONAL_PRECISION set "BTC_PROMOTION_MIN_DIRECTIONAL_PRECISION=0.48"
if not defined BTC_PROMOTION_MAX_BRIER set "BTC_PROMOTION_MAX_BRIER=0.80"
if not defined BTC_PROMOTION_MAX_ECE set "BTC_PROMOTION_MAX_ECE=0.20"
if not defined BTC_PROMOTION_MAX_PRECISION_REGRESSION set "BTC_PROMOTION_MAX_PRECISION_REGRESSION=0.03"
if not defined BTC_PROMOTION_MAX_BRIER_REGRESSION set "BTC_PROMOTION_MAX_BRIER_REGRESSION=0.03"
if not defined BTC_PROMOTION_MAX_EVAL_SAMPLES set "BTC_PROMOTION_MAX_EVAL_SAMPLES=12000"
REM =======================================================================
REM Run a validation backtest automatically on startup (1 = on, 0 = off).
REM OFF by default (2026-06-19): the replay is CPU-bound (BTC_BACKTEST_MAX_ROWS candles x 7 horizons)
REM and, in background threads, it starves the async event loop on this 16GB box for several minutes
REM post-boot -> the live price freezes and predictions lag ~30s until it finishes. It is validation
REM only (not needed for live serving). Run it on demand instead: POST /api/backtest when the box is
REM idle, or set BTC_RUN_STARTUP_BACKTEST=1 here for a one-off validated boot.
if not defined BTC_RUN_STARTUP_BACKTEST set "BTC_RUN_STARTUP_BACKTEST=0"
REM Backtest window: recent N rows (faster) or 0 = full historical replay (heavy on a laptop).
if not defined BTC_BACKTEST_MAX_ROWS set "BTC_BACKTEST_MAX_ROWS=12000"
REM Specialist-head move buckets in dollars (big-move/up/down/drop/activity). Each horizon has
REM   meaningful | large | extreme  — the first value is the binary training boundary.
REM AUTO-DERIVE (default, recommended): the trainers compute these from the ACTUAL move
REM   distribution of the matrix on every retrain (p75 / p90 / p97 = top-quartile / top-10% /
REM   top-3%), so "big move" stays a genuinely notable event at ANY BTC price level — absolute
REM   dollar buckets otherwise go stale as price re-prices ($30/5m is top-quartile at $65k, noise
REM   at $130k). Tune the percentiles with BTC_MOVE_BUCKET_PCTS (default 0.75,0.90,0.97).
REM MANUAL OVERRIDE (optional): uncomment the next line to PIN fixed dollar buckets instead of auto.
REM set "BTC_MOVE_BUCKETS_USD_BY_HORIZON=1:35|60|100;3:60|105|190;5:80|140|240;7:95|160|290;10:115|195|340;15:140|240|420;30:200|335|580"
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
if not defined BTC_TRAIN_THREADS set "BTC_TRAIN_THREADS=8"
REM Cap the OTHER parallel libs (HistGradientBoosting/numpy/BLAS use OpenMP, NOT n_jobs) to
REM the same budget — without this they'd still grab all 16 cores and freeze the feed.
if not defined OMP_NUM_THREADS set "OMP_NUM_THREADS=8"
if not defined OPENBLAS_NUM_THREADS set "OPENBLAS_NUM_THREADS=8"
if not defined MKL_NUM_THREADS set "MKL_NUM_THREADS=8"
REM 360-day laptop safety. The full sequence tensor is disk-backed; each direction
REM learner uses a representative sample spanning history plus a recent tail.
if not defined BTC_SEQUENCE_MEMMAP_THRESHOLD_MB set "BTC_SEQUENCE_MEMMAP_THRESHOLD_MB=1024"
if not defined BTC_DIRECTION_MAX_SAMPLES set "BTC_DIRECTION_MAX_SAMPLES=40000"
REM Long-window sample policy. The production control keeps the proven 40k direction
REM budget, while each selected row is weighted by both recency and similarity to the
REM latest causal volatility/trend regime. Monthly source-quality gating makes the
REM data-quality factor exactly 1 for admitted rows and rejects broken months entirely.
if not defined BTC_SAMPLE_WEIGHT_MODE set "BTC_SAMPLE_WEIGHT_MODE=recency_similarity"
REM TCN no longer means "latest 25k only": it uses 50%% recent, 25%% historical-regime,
REM and 25%% historical-tail rows. The separate multi-window harness tests alternatives.
if not defined BTC_TCN_MAX_SAMPLES set "BTC_TCN_MAX_SAMPLES=25000"
REM LightGBM's Windows OpenCL path intermittently crashes this wheel/driver.
REM Keep it on CPU; XGBoost and PyTorch may still use CUDA on the RTX 4050.
if not defined BTC_LGB_DEVICE set "BTC_LGB_DEVICE=cpu"
REM Reject model/head artifacts whose requested days, source/data hash, end timestamp,
REM feature schema, or artifact bytes differ from the current matrix contract.
REM
REM 2026-07-26 -- WHY THIS IS 0 UNTIL THE 1265d BUNDLE EXISTS:
REM   Sidecar manifests are written only by the NEW training path. Every artifact currently on
REM   disk predates it, so with strict=1 all six are refused at load and the app serves with
REM   NO heads at all (measured: P(hold), path, fade, signed-quantile, round-state, keepers).
REM   Back-filling manifests is NOT a fix: artifact_compatibility compares every key against the
REM   CURRENT training identity, so a manifest recording their real 400d provenance is refused
REM   anyway, and one recording the current identity would be a lie about what trained them.
REM   The honest state is "identity is not yet enforced because no artifact can satisfy it".
REM   The 1265d run writes real manifests; AFTER it completes, set this back to 1 and the gate
REM   becomes meaningful instead of merely fatal. Verify with:
REM     python backend/verify_artifact_identity.py
if not defined BTC_STRICT_ARTIFACT_IDENTITY set "BTC_STRICT_ARTIFACT_IDENTITY=0"
REM === HEAD-HEALTH ENFORCEMENT (Blueprint 31.2) ==========================
REM A head that live outcomes say cannot price is not allowed to price. Specifically:
REM BTC_ENABLE_PAPER_BET=1 used to be enough on its own to re-enable betting on P(hold) even
REM when the head-health report had already measured P(hold) as CALIBRATION_ONLY -- i.e. the
REM override could overrule the evidence. It now ALSO requires the head to measure USABLE.
REM FAILS CLOSED: a missing, stale, unknown or corrupt report DENIES both pricing and
REM ranking. The app stays online and still displays diagnostics; only ACTION authority is
REM withheld. The gate re-opens by itself when the next report returns the head to USABLE.
REM Set to 0 for observe-only (permissions logged, not enforced). Must be set BEFORE launch.
REM   python backend\head_permissions.py          (print current permissions)
if not defined BTC_ENFORCE_HEAD_HEALTH set "BTC_ENFORCE_HEAD_HEALTH=1"
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

if "%BTC_VALIDATE_STARTUP%"=="1" (
    echo [validate] days=%BTC_HISTORICAL_DAYS% backfill=%BTC_BACKFILL_DAYS% split=%BTC_TRAIN_SPLIT_FRAC%
    echo [validate] binance_paper=%BTC_ENABLE_BINANCE_PAPER% default_db=data\binance_paper.duckdb
    echo [validate] force_heads=%BTC_FORCE_HEAD_RETRAIN% force_main=%BTC_FORCE_MAIN_RETRAIN% frozen=%BTC_FREEZE_MODEL%
    echo [validate] direction_cap=%BTC_DIRECTION_MAX_SAMPLES% memmap_threshold_mb=%BTC_SEQUENCE_MEMMAP_THRESHOLD_MB% lgb_device=%BTC_LGB_DEVICE%
    echo [validate] full_refit_after_gate=%BTC_FULL_REFIT_AFTER_GATE% min_precision=%BTC_PROMOTION_MIN_DIRECTIONAL_PRECISION% max_ece=%BTC_PROMOTION_MAX_ECE%
    echo [validate] marker=%BTC_RETRAIN_COMPLETION_MARKER%
    exit /b 0
)

REM Stop an existing app BEFORE any multi-day data/model work. The old guard exited here,
REM making the later port-8000 killer unreachable. start.bat is an explicit relaunch command;
REM browser refreshes never invoke it. Set BTC_AUTO_STOP_EXISTING_APP=0 to warn and abort instead.
if not defined BTC_AUTO_STOP_EXISTING_APP set "BTC_AUTO_STOP_EXISTING_APP=1"
powershell -NoProfile -Command "$p = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 3000,8000 }); if (-not $p) { exit 0 }; $p | ForEach-Object { Write-Host ('[preflight] port {0} is held by PID {1}' -f $_.LocalPort,$_.OwningProcess) }; if ('%BTC_AUTO_STOP_EXISTING_APP%' -ne '1') { exit 2 }; $p.OwningProcess | Sort-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500; $left = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 3000,8000 }); if ($left) { exit 3 }"
if errorlevel 3 (
    echo [preflight] ERROR: existing frontend/backend could not be stopped.
    exit /b 1
)
if errorlevel 2 (
    echo [preflight] ERROR: frontend/backend already active and auto-stop is disabled.
    echo             Close them manually or set BTC_AUTO_STOP_EXISTING_APP=1.
    exit /b 1
)

REM Long-window disk guard. Existing 400d cache is ~73GB; extending to 1500d plus the temporary
REM pruned sequence memmap needs roughly 230-270GB more. Keep a safety margin for parquet rewrites.
for /f %%G in ('powershell -NoProfile -Command "[math]::Floor((Get-PSDrive -Name C).Free / 1GB)"') do set "BTC_FREE_DISK_GB=%%G"
for /f %%G in ('powershell -NoProfile -Command "@(Get-ChildItem -LiteralPath '%BTC_DATA_DIR%\backfill_cache' -Filter 'BTCUSDT*aggTrades-*.csv' -File -ErrorAction SilentlyContinue).Count"') do set "BTC_BACKFILL_CACHE_FILES=%%G"
if not defined BTC_BACKFILL_CACHE_FILES set "BTC_BACKFILL_CACHE_FILES=0"
echo [preflight] C: free disk=%BTC_FREE_DISK_GB%GB, cache files=%BTC_BACKFILL_CACHE_FILES%, requested window=%BTC_HISTORICAL_DAYS%d.
REM A first long build needs the full 300GB margin. Once >=1000 daily source files are cached,
REM a retry is a RESUME, not a first build; requiring 300GB free again would reject the run simply
REM because completed downloads now occupy disk. Resumes retain a hard 80GB floor for the sequence
REM memmap, staged candidate/full-refit bundles and DuckDB/parquet rewrites.
REM Disk/readiness classification now lives in a TESTED module rather than this one-liner.
REM The CSV count alone is the wrong question: this machine's derived sources
REM (trade_features_backfill 1288d, crossvenue_flow 1286d) already cover the window, so a matrix
REM rebuild needs NO bulk download even with an empty cache. Three modes, each with its own floor:
REM   REBUILD     derived parquets already span the window  -> 80GB
REM   RESUME      >=1000 daily CSVs cached                  -> 80GB
REM   FIRST_BUILD neither                                   -> 300GB
REM   python backend\preflight_longwindow.py --days 1265     (explain the current verdict)
REM   python backend\preflight_longwindow.py --selftest
python backend\preflight_longwindow.py --days %BTC_HISTORICAL_DAYS%
if errorlevel 2 (
    echo [preflight] ERROR: insufficient free disk for this long run - see the mode above.
    exit /b 1
)

REM One-time rollback snapshot before any forced head can replace an artifact. Robocopy exit
REM codes 0-7 are success/nonfatal; 8+ means at least one copy failed and training must stop.
if "%BTC_OVERNIGHT_TRAIN_ALL%"=="1" if not exist "%BTC_DATA_DIR%\saved_models_pre1500d_backup" (
    echo [preflight] Backing up the current working model bundle before the long retrain...
    robocopy "%BTC_DATA_DIR%\saved_models" "%BTC_DATA_DIR%\saved_models_pre1500d_backup" /E /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
    if errorlevel 8 (
        echo [preflight] ERROR: model backup failed. Training has not started.
        exit /b 1
    )
    echo [preflight] Model rollback snapshot ready: data\saved_models_pre1500d_backup
)

REM === INVARIANT SELFTESTS (fast, offline, no network) ===================
REM These guard the failures that are SILENT: the app keeps running, the report looks healthy,
REM and the corruption only shows up as apparent edge that dies on contact with real money.
REM Each suite is pure-python and runs in well under a second.
REM
REM NOTE ON THE STRUCTURE: each check exits immediately on failure rather than accumulating into
REM a flag. start.bat has no `setlocal enabledelayedexpansion`, so a %VAR% set inside a
REM parenthesised block is expanded at PARSE time and would always read its pre-block value -
REM i.e. an accumulator here would silently never fire, which is exactly the class of bug these
REM selftests exist to catch.
REM
REM Set BTC_SKIP_SELFTESTS=1 to bypass (NEVER for an evidence run).
if "%BTC_SKIP_SELFTESTS%"=="1" goto :selftests_done
echo [selftest] a. Complete-trade audit regressions - label/M0/execution correctness:
python -m backend.trade_forecast.test_audit_fixes >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a2. Builder integration - EXECUTES the label path:
python -m backend.trade_forecast.test_builder_integration >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a2b. Complete-trade serving and optimizer integration:
python -m backend.trade_forecast.test_complete_trade_forecast >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a3. Forward evidence isolation + M0 gates:
python backend/trade_forecast/forward_evidence.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
python -m backend.trade_forecast.freeze_complete_trade_threshold --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
python backend/trade_forecast/m0_gates.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a7. Ledger V2 end-to-end - real DuckDB round trip:
python -m backend.trade_forecast.test_ledger_v2_end_to_end >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a8. Evidence completion - durable logging, eligibility, own-L2 outcomes:
python -m backend.trade_forecast.test_evidence_completion >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a6. Forward M0 V2 evaluator + import boundary:
python -m backend.trade_forecast.evaluate_complete_trade_m0_v2_forward --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a5. Serving integration - EXECUTES loaders + pointer swaps:
python -m backend.trade_forecast.test_serving_integration >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] a4. Champion resolver - promotion reaches serving:
python backend/trade_forecast/champion_resolver.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_a
echo [selftest] b. Frozen-artifact pinning - no model swap mid-evidence-run:
python backend\trade_forecast\freeze_guard.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
echo [selftest] b2. Quarantined prototypes + non-blocking feed callbacks:
python backend\test_quarantine_and_feed.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\feed_writer.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_regime_causal_filter.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_endogenous_kelly.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_kelly_scratch_semantics.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_kelly_scratch_handling.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_feed_writer_load.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
echo [selftest] b3. Launcher integrity - every invoked path exists:
python backend\test_launcher_integrity.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b3
echo [selftest] b4. Model registry + artifact bundles (foundation):
python backend\model_registry.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b4
python backend\model_artifacts.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b4
python backend\order_lifecycle.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\trading_authority.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\task_supervisor.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_close_only_authority.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_polymarket_client_protocol.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_feed_protocol_health.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\verified_io.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\artifact_migration_status.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\control_auth.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
python backend\test_control_plane_security.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_b
echo [selftest] c. Head permissions - a head that cannot price may not price:
python backend\head_permissions.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_c
echo [selftest] d. Long-window preflight classification:
python backend\preflight_longwindow.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_d
echo [selftest] e. Multi-venue collector schema + episode accounting:
python backend\venues\multi_venue_recorder.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_e
echo [selftest] f. Venue admissibility - backlog/lead-lag/identity gates:
python backend\venues\venue_admissibility.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_f
python backend\venues\rl_data_readiness.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_f
echo [selftest] g. Collector evidence integrity - D1-D5:
python backend\venues\test_collector_integrity.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_g
echo [selftest] h. Strategy registry consistency:
python backend\research\audit_strategy_registry.py >nul 2>&1
if errorlevel 1 goto :selftest_failed_h
echo [selftest] i. Challenger promotion gates - no ungated model replacement:
python backend\promote_challenger.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_i
echo [selftest] j. Shared quant-platform kernel:
python -m backend.quant_platform.test_kernel >nul 2>&1
if errorlevel 1 goto :selftest_failed_j
echo [selftest] k. Binance paper execution and accounting:
python -m backend.binance_paper.test_engine >nul 2>&1
if errorlevel 1 goto :selftest_failed_k
echo [selftest] l. Research validation and promotion gates:
python -m backend.quant_platform.test_research_validation >nul 2>&1
if errorlevel 1 goto :selftest_failed_l
python research\run_all_sequence.py --selftest >nul 2>&1
if errorlevel 1 goto :selftest_failed_l
echo [selftest] All invariant selftests passed.
if "%BTC_SELFTEST_ONLY%"=="1" exit /b 0
goto :selftests_done

:selftest_failed_a
echo [selftest] FAILED: python -m backend.trade_forecast.test_audit_fixes
goto :selftest_abort
:selftest_failed_b
echo [selftest] FAILED: python backend\trade_forecast\freeze_guard.py --selftest
goto :selftest_abort
:selftest_failed_b3
echo [selftest] FAILED: python backend\test_launcher_integrity.py
goto :selftest_abort
:selftest_failed_b4
echo [selftest] FAILED: python backend\model_registry.py --selftest (or model_artifacts.py)
goto :selftest_abort
:selftest_failed_c
echo [selftest] FAILED: python backend\head_permissions.py --selftest
goto :selftest_abort
:selftest_failed_d
echo [selftest] FAILED: python backend\preflight_longwindow.py --selftest
goto :selftest_abort
:selftest_failed_e
echo [selftest] FAILED: python backend\venues\multi_venue_recorder.py --selftest
goto :selftest_abort
:selftest_failed_f
echo [selftest] FAILED: python backend\venues\venue_admissibility.py --selftest
goto :selftest_abort
:selftest_failed_g
echo [selftest] FAILED: python backend\venues\test_collector_integrity.py
goto :selftest_abort
:selftest_failed_h
echo [selftest] FAILED: python backend\research\audit_strategy_registry.py
goto :selftest_abort

:selftest_failed_i
echo [selftest] FAILED: python backend\promote_challenger.py --selftest
goto :selftest_abort

:selftest_failed_j
echo [selftest] FAILED: python -m backend.quant_platform.test_kernel
goto :selftest_abort

:selftest_failed_k
echo [selftest] FAILED: python -m backend.binance_paper.test_engine
goto :selftest_abort

:selftest_failed_l
echo [selftest] FAILED: python -m backend.quant_platform.test_research_validation
goto :selftest_abort

:selftest_abort
echo [selftest] ERROR: an invariant selftest failed. Startup stopped.
echo             Re-run the command above WITHOUT ^>nul to see which check broke.
exit /b 1

:selftests_done

echo Starting BTC Quantum Trader...

REM Start all record-forward collectors once. The PowerShell helper detects existing
REM Python writers, skips duplicates, runs missing collectors hidden, and redirects
REM stdout/stderr to data\*.log. Individual BTC_SKIP_* flags remain supported.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%backend\start_recorders_once.ps1"
if errorlevel 1 echo [recorder] Recorder launcher failed - app startup will continue.

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
    REM Keep extracted spot files: persistence and cross-venue builders immediately reuse them.
    REM Without this flag a wide run downloads the same daily spot archive twice.
    python backend\backfill_trade_features.py --auto --days %BTC_BACKFILL_DAYS% --keep-cache
    if errorlevel 1 echo [0/3a] Trade-feature backfill failed - continuing with existing data.
    echo [0/3] b. Updating A1 persistence dataset - late-entry/T3 engine...
    python backend\build_persistence_dataset.py --auto --days %BTC_BACKFILL_DAYS%
    if errorlevel 1 echo [0/3b] Persistence build failed - continuing.
    echo [0/3] c. Updating A4 cross-venue flow - spot-vs-perp divergence...
    python backend\build_crossvenue_flow.py --auto --days %BTC_BACKFILL_DAYS%
    if errorlevel 1 echo [0/3c] Cross-venue build failed - continuing.
)
echo [0/3] c2. Rebuilding the 1m research matrix to the BTC_HISTORICAL_DAYS window. This is
echo          the SINGLE knob that drives ALL specialist heads (big-move/up/down/drop/activity):
echo          current window=%BTC_HISTORICAL_DAYS% days. Every requested head uses this source window.
echo          only when the matrix coverage and source mtimes are already valid:
set "BTC_HEAD_RETRAIN_COMPLETE=0"
python backend\build_research_matrix.py --days %BTC_HISTORICAL_DAYS%
if errorlevel 1 (
    echo [0/3c2] Research-matrix build failed or coverage is too low.
    echo          Skipping specialist-head training to avoid stamping stale data as fresh.
) else (
    REM CHALLENGER-ONLY for long windows. A build finishing is NOT a reason to replace the models
    REM currently driving decisions. For >=1200d runs heads train into a challenger directory and
    REM the incumbent bundle is untouched; promotion is a separate GATED step that verifies every
    REM artifact manifest, the matrix monthly-quality gate, the admitted training window, and head
    REM health:
    REM   python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265
    REM   (add --apply to promote; the incumbent is snapshotted first)
    REM Set BTC_LONG_WINDOW_CHALLENGER=0 to train straight into saved_models (not advised).
    if not defined BTC_LONG_WINDOW_CHALLENGER set "BTC_LONG_WINDOW_CHALLENGER=1"
    if %BTC_HISTORICAL_DAYS% GEQ 1200 if "%BTC_LONG_WINDOW_CHALLENGER%"=="1" (
        set "BTC_MODEL_OUTPUT_DIR=%BTC_DATA_DIR%\saved_models_challenger_%BTC_HISTORICAL_DAYS%d"
        echo [0/3d] CHALLENGER MODE - heads train into saved_models_challenger_%BTC_HISTORICAL_DAYS%d
        echo         The live bundle is NOT modified. Promote explicitly via promote_challenger.py.
    )
    echo [0/3] d. Specialized heads - VERSION-AWARE - retrain a head only if MISSING or its
    echo          HEAD_VERSION changed. Set BTC_FORCE_HEAD_RETRAIN=1 to train every head one by one:
    if "%BTC_FORCE_HEAD_RETRAIN%"=="1" (
        python backend\train_heads.py --force
    ) else (
        python backend\train_heads.py
    )
    if errorlevel 1 (
        set "BTC_HEAD_RETRAIN_COMPLETE=0"
        echo [0/3d] Head training had an issue. Completion marker will NOT be written.
    ) else (
        set "BTC_HEAD_RETRAIN_COMPLETE=1"
    )
)
if "%BTC_OVERNIGHT_TRAIN_ALL%"=="1" if not "%BTC_HEAD_RETRAIN_COMPLETE%"=="1" (
    echo [0/3] ERROR: the %BTC_HISTORICAL_DAYS%d matrix or a required specialist head failed.
    echo       Main-ensemble training will NOT start against incomplete inputs.
    echo       Fix the logged failure and run start.bat again; cached daily files are reused.
    exit /b 1
)
echo [0/3] e. Data-quality health check - last 3 days - report only:
python backend\data_quality_audit.py --days 3
if errorlevel 1 echo [0/3e] Data-quality audit skipped - continuing.
echo [0/3] f. Cleanup superseded research artifacts - safe allow-list only:
python backend\cleanup_artifacts.py --apply
if errorlevel 1 echo [0/3f] Cleanup skipped - continuing.
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
