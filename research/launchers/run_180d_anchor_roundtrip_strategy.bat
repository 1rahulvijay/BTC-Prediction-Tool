@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
if not defined BTC_RESEARCH_INTERVAL set "BTC_RESEARCH_INTERVAL=30s"
if not defined BTC_RESEARCH_DAYS set "BTC_RESEARCH_DAYS=180"
if not defined BTC_RESEARCH_THREADS set "BTC_RESEARCH_THREADS=4"
set "SAVE_MODEL_ARG="
if "%BTC_RESEARCH_SAVE_MODELS%"=="1" set "SAVE_MODEL_ARG=--save-models"

echo ============================================================
echo  BTC Anchor Round-Trip Research - PAPER ONLY
echo  Days: %BTC_RESEARCH_DAYS%  Interval: %BTC_RESEARCH_INTERVAL%
echo  Exact 5m/15m anchors, 70 causal features, sequential models
echo  Output: data\research\anchor_roundtrip_%BTC_RESEARCH_DAYS%d_%BTC_RESEARCH_INTERVAL%
echo ============================================================

python -u backend\research\test_180d_anchor_roundtrip_strategy.py ^
  --days %BTC_RESEARCH_DAYS% ^
  --interval %BTC_RESEARCH_INTERVAL% ^
  --threads %BTC_RESEARCH_THREADS% ^
  --max-train-rows 120000 ^
  %SAVE_MODEL_ARG% %*

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Research completed successfully.
) else (
  echo Research failed with exit code %RC%. Check run.log in the output folder.
)
pause
exit /b %RC%
