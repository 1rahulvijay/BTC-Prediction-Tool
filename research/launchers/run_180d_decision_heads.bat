@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
if not defined BTC_RESEARCH_THREADS set "BTC_RESEARCH_THREADS=4"

echo ============================================================
echo  BTC Decision-Head Research - PAPER ONLY
echo  Uses validated 180d/30s anchor data and live recorder exports
echo  Sequential models, 4 threads, no deployed-model changes
echo ============================================================

python -u backend\research\test_180d_decision_heads.py ^
  --threads %BTC_RESEARCH_THREADS% ^
  --max-train-rows 120000 %*

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Decision-head research completed successfully.
) else (
  echo Decision-head research failed with exit code %RC%.
)
pause
exit /b %RC%
