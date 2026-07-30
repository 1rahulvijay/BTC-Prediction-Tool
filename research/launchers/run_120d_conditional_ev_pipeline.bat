@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

set "PY=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "data\logs" mkdir "data\logs"
set "LOG=data\logs\conditional_ev_120d.log"

echo Running the frozen 120-day conditional-EV research pipeline.
echo Stages: magnitude, direction-given-move, signed-return mean and quantiles.
echo Primary action: conservative q10/q90 net value after 12 bps costs.
echo Research only. No live model or paper policy is changed.
echo Log: %LOG%
echo.

"%PY%" -u backend\research\train_120d_conditional_ev_pipeline.py ^
  --days 120 ^
  --horizons 5 15 ^
  --classifier-models all ^
  --regressor-models all ^
  --quantile-models all ^
  --folds 4 ^
  --test-days 15 ^
  --fee-bps-per-side 5 ^
  --slippage-bps-per-side 1 ^
  --max-train-rows 0 ^
  --max-features 80 ^
  --threads 4 ^
  --log-file "%LOG%"

if errorlevel 1 (
  echo Conditional-EV research FAILED. Review %LOG%.
  exit /b 1
)

echo Conditional-EV research completed.
endlocal
