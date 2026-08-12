@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

REM Production serves the built frontend and API from one Uvicorn process.
REM Real-order flags are forced off. This launcher never trains or rebuilds data.
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
REM Match backend/audit/datastore_identity.py's committed canonical write-path declaration.
REM data\analytics.duckdb is a divergent sibling and must never be selected by convenience.
if not defined BTC_DB_PATH set "BTC_DB_PATH=%BTC_DATA_DIR%\btc_duckdbs\analytics.duckdb"
set "BTC_LOG_DIR=%PROJECT_ROOT%data\logs"
set "BTC_DEPLOYMENT_ENV=production"
set "BTC_REQUIRE_ADMIN_TOKEN=1"
set "BTC_STRICT_ARTIFACT_IDENTITY=1"
set "BTC_FREEZE_MODEL=1"
set "BTC_RUN_STARTUP_BACKTEST=0"
set "BTC_FORCE_MAIN_RETRAIN=0"
set "BTC_FORCE_HEAD_RETRAIN=0"
set "BTC_OVERNIGHT_TRAIN_ALL=0"
set "BTC_SKIP_BACKFILL=1"
set "BTC_SERVE_FRONTEND=1"
set "BTC_EVIDENCE_MODE=1"
set "BTC_REQUIRE_COMPLETE_TRADE=1"
set "BTC_REQUIRE_POLYMARKET_FEED=1"
set "BTC_REQUIRE_PROTOCOL_HEALTH=1"
if not defined BTC_HISTORICAL_DAYS set "BTC_HISTORICAL_DAYS=3"
if not defined BTC_SERVING_WARMUP_DAYS set "BTC_SERVING_WARMUP_DAYS=%BTC_HISTORICAL_DAYS%"
REM The 3-day value above is only the live candle warm-up. Artifact identity must remain the
REM actual trained window or strict serving will reject a valid 1000-day bundle as a 3-day mismatch.
if not defined BTC_MODEL_TRAINING_DAYS set "BTC_MODEL_TRAINING_DAYS=1000"
if not defined BTC_ALLOWED_ORIGINS set "BTC_ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000"
set "BTC_ENABLE_LIVE_TRADING=0"
set "BTC_ENABLE_REAL_ORDERS=0"
set "BTC_BINANCE_LIVE=0"
set "BTC_POLYMARKET_LIVE=0"

if not defined BTC_BIND_HOST set "BTC_BIND_HOST=127.0.0.1"
if not defined BTC_PORT set "BTC_PORT=8000"

set "PYTHON_EXE=%PROJECT_ROOT%.venv-prod\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "BTC_PYTHON_EXE=%PYTHON_EXE%"

if "%BTC_VALIDATE_PRODUCTION_STARTUP%"=="1" (
    echo [validate-production] db=%BTC_DB_PATH%
    echo [validate-production] warmup_days=%BTC_HISTORICAL_DAYS% model_days=%BTC_MODEL_TRAINING_DAYS%
    echo [validate-production] bind=%BTC_BIND_HOST%:%BTC_PORT% real_orders=%BTC_ENABLE_REAL_ORDERS%
    "%PYTHON_EXE%" backend\wait_for_forward_evidence.py --selftest
    exit /b %errorlevel%
)

echo [1/6] Refusing an ambiguous launch if the production port is already occupied...
powershell -NoProfile -Command "$p = @(Get-NetTCPConnection -State Listen -LocalPort %BTC_PORT% -ErrorAction SilentlyContinue); if ($p) { $p | ForEach-Object { Write-Error ('Port %BTC_PORT% is already held by PID {0}. Stop the existing service deliberately before deploying new code.' -f $_.OwningProcess) }; exit 2 }"
if errorlevel 1 exit /b 1

echo [2/6] Building immutable frontend assets...
call npm run build
if errorlevel 1 exit /b 1

echo [3/6] Starting the standalone forward-evidence recorders exactly once...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%backend\start_recorders_once.ps1"
if errorlevel 1 (
    echo Production launch refused because a required recorder failed to start.
    exit /b 1
)

echo [4/6] Waiting for required recorder rows to advance...
"%PYTHON_EXE%" backend\wait_for_forward_evidence.py --timeout 90 --interval 3
if errorlevel 1 (
    echo Production launch refused because forward evidence did not become healthy.
    exit /b 1
)

echo [5/6] Running fail-closed production readiness...
"%PYTHON_EXE%" backend\production_readiness.py --mode paper
if errorlevel 1 (
    echo Production launch refused. Correct every reported prerequisite first.
    exit /b 1
)

echo [6/6] Starting paper/shadow production service on %BTC_BIND_HOST%:%BTC_PORT%...
"%PYTHON_EXE%" -m uvicorn server:app --app-dir backend --host %BTC_BIND_HOST% --port %BTC_PORT% --workers 1
exit /b %errorlevel%
