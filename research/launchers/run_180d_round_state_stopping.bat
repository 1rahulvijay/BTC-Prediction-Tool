@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"
if not defined BTC_RESEARCH_THREADS set "BTC_RESEARCH_THREADS=4"

echo ============================================================
echo  BTC Round-State and Stopping Research - PAPER ONLY
echo  Existing 180d paths; no forward-recorder dependency
echo ============================================================

python -u backend\research\test_180d_round_state_and_stopping.py ^
  --threads %BTC_RESEARCH_THREADS% ^
  --max-train-rows 120000 %*

set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Round-state research completed successfully.
) else (
  echo Round-state research failed with exit code %RC%.
)
pause
exit /b %RC%
