@echo off
setlocal

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

if not exist data\logs mkdir data\logs

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe

set LOG_FILE=data\logs\forecast_180d_all_models.log

echo Starting BTC 180-day all-model research forecaster...
echo Horizons: 5m and 15m
echo Data: 180 days
echo Models: tabular GPU where supported, quantile GPU where supported, LSTM, GRU, TCN, Transformer core sequence targets
echo Log: %LOG_FILE%
echo.
echo This is research-only and will not modify live app models or bot logic.
echo Leave this window open overnight.

if exist "%LOG_FILE%" del "%LOG_FILE%"

start "BTC 180d All Models Forecaster" powershell -NoExit -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; Set-Location -LiteralPath '%PROJECT_ROOT%'; & '%PYTHON_EXE%' 'backend\research\train_360d_multitarget_forecaster.py' --days 180 --horizons 5 15 --models 'ridge,elasticnet,histgb,rf,extra_trees,logistic,lightgbm,xgboost,catboost,lstm,gru,tcn,transformer' --include-sequence --sequence-targets core --device gpu --quantile-backends lightgbm --max-features 160 --n-jobs 2 --seq-max-features 48 --seq-max-rows 100000 --seq-batch-size 384 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo Launched in a separate terminal.
echo Monitor with:
echo   powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Wait"

endlocal
