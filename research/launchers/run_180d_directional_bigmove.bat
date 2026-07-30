@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

if not exist data\logs mkdir data\logs

set "PYTHON_EXE=python"
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"

set "LOG_FILE=data\logs\forecast_180d_directional_bigmove.log"
if exist "%LOG_FILE%" del "%LOG_FILE%"

echo Starting BTC 180-day directional big-move research...
echo.
echo Targets:
echo   - big_up: future close moved up enough
echo   - big_down: future close moved down enough
echo   - big_drop: future low dropped enough during the window
echo.
echo Models:
echo   logistic,histgb,rf,extra_trees,lightgbm,xgboost,catboost
echo.
echo Horizons:
echo   5m and 15m
echo.
echo Output:
echo   data\research\forecast_180d_directional_bigmove_*.csv
echo   data\research\forecast_180d_directional_bigmove_report.md
echo   data\saved_models\research_directional_bigmove\
echo.
echo Log:
echo   %LOG_FILE%
echo.

start "BTC 180d Directional BigMove" powershell -NoExit -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; Set-Location -LiteralPath '%PROJECT_ROOT%'; & '%PYTHON_EXE%' 'backend\research\train_180d_directional_bigmove.py' --days 180 --horizons 5 15 --models 'logistic,histgb,rf,extra_trees,lightgbm,xgboost,catboost' --device gpu --max-features 160 --n-jobs 2 --threshold-5m-bps 10 --threshold-15m-bps 15 --drop-threshold-5m-bps 10 --drop-threshold-15m-bps 15 --output-prefix forecast_180d_directional_bigmove 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo Launched in a separate PowerShell window.
echo Watch progress in:
echo   %LOG_FILE%
endlocal
