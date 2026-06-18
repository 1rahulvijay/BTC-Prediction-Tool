@echo off
setlocal

cd /d "%~dp0"

if not exist data\logs mkdir data\logs

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe

set LOG_FILE=data\logs\forecast_180d_remaining_quantile.log

echo Starting BTC 180-day remaining quantile-only forecaster...
echo This skips regression, classification, and sequence models already analyzed.
echo Output prefix: forecast_180d_quantile_only
echo Log: %LOG_FILE%

if exist "%LOG_FILE%" del "%LOG_FILE%"

start "BTC 180d Quantile Only" powershell -NoExit -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; Set-Location -LiteralPath '%~dp0'; & '%PYTHON_EXE%' 'backend\research\train_360d_multitarget_forecaster.py' --days 180 --horizons 5 15 --models 'lightgbm' --device gpu --skip-regression --skip-classification --skip-sequence --quantile-backends lightgbm --output-prefix forecast_180d_quantile_only --max-features 160 --n-jobs 2 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo Launched in a separate terminal.
echo Monitor with:
echo   powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Wait"

endlocal
