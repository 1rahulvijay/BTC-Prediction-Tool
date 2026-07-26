@echo off
setlocal
cd /d "%~dp0"

set "PY=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

set "BTC_HISTORICAL_DAYS=1265"
set "BTC_BACKFILL_DAYS=1265"
set "BTC_TRAIN_THREADS=4"

echo Running purged W90/W400/W1265 shadow experts.
echo This is research-only and cannot replace the live champion automatically.
echo Results: data\research\multiwindow_experts

"%PY%" -u backend\research\multiwindow_experiment.py ^
  --horizons 5 15 ^
  --families logreg histgb rf xgb lgbm catboost ^
  --folds 5 ^
  --test-days 30 ^
  --threads 4 ^
  --save-models

if errorlevel 1 (
  echo Multi-window experiment FAILED.
  exit /b 1
)
echo Multi-window experiment completed.
endlocal
