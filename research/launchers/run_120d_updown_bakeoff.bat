@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

if not exist data\research mkdir data\research
if not exist data\logs mkdir data\logs

set BTC_BAKEOFF_THREADS=4

echo Starting 120-day BTC up/down multi-head bakeoff...
echo Logs: data\logs\updown_bakeoff_120d.log
echo Metrics: data\research\updown_bakeoff_metrics.csv
echo Predictions: data\research\updown_bakeoff_predictions.csv
echo.

start "BTC 120d UpDown Bakeoff" cmd /k ^
  "python backend\research\standalone\run_updown_multihead_bakeoff.py --days 120 --horizons 5 15 30 --split 0.70 --rebuild --calibrate --save-predictions --prediction-limit-per-model 5000 --metrics-csv data\research\updown_bakeoff_metrics.csv --predictions-csv data\research\updown_bakeoff_predictions.csv --summary-json data\research\updown_bakeoff_run_summary.json > data\logs\updown_bakeoff_120d.log 2>&1"

echo Launched in a separate terminal window.
echo You can monitor:
echo   Get-Content data\logs\updown_bakeoff_120d.log -Wait
endlocal
