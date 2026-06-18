@echo off
setlocal

cd /d "%~dp0"

if not exist data\logs mkdir data\logs

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe

set LOG_FILE=data\logs\forecast_360d_multitarget.log

echo Starting BTC 360-day multi-target research forecaster...
echo This is research-only. It does not modify live app models or bot logic.
echo Log: %LOG_FILE%

start "BTC 360d Multi-Target Forecaster" cmd /k ""%PYTHON_EXE%" backend\research\train_360d_multitarget_forecaster.py --days 360 --horizons 5 15 --models core --device gpu --quantile-backends lightgbm --max-features 180 --n-jobs 2 > "%LOG_FILE%" 2>&1"

echo Launched in a separate terminal.
echo Monitor with:
echo   powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Wait"

endlocal
