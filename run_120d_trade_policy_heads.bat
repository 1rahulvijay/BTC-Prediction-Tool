@echo off
setlocal
cd /d "%~dp0"

set "PY=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "data\logs" mkdir "data\logs"

set "LOG=data\logs\trade_policy_heads_120d.log"

echo Running isolated 120-day LONG, SHORT, and ACT/SKIP research.
echo Models run sequentially with four CPU threads for a 16 GB laptop.
echo Dynamic exit is intentionally excluded because its preregistered gate failed.
echo Log: %LOG%
echo Results: data\research\trade_policy_heads_120d
echo.

"%PY%" -u backend\research\train_120d_trade_policy_heads.py ^
  --days 120 ^
  --horizons 5 15 ^
  --models all ^
  --meta-models logreg,histgb ^
  --folds 4 ^
  --test-days 15 ^
  --fee-bps-per-side 5 ^
  --slippage-bps-per-side 1 ^
  --act-threshold 0.58 ^
  --max-train-rows 0 ^
  --max-features 80 ^
  --threads 4 ^
  --log-file "%LOG%" ^
  --save-models

if errorlevel 1 (
  echo.
  echo Trade-policy research FAILED. Review %LOG%.
  exit /b 1
)

echo.
echo Trade-policy research completed.
echo Review the latest summary.csv under data\research\trade_policy_heads_120d.
endlocal
