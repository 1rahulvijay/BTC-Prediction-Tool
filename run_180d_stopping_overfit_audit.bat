@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"
if not defined BTC_RESEARCH_THREADS set "BTC_RESEARCH_THREADS=4"

echo ============================================================
echo  TP50-SL10 Walk-Forward Overfitting Audit - PAPER ONLY
echo  Five expanding folds, 20 policies, purged chronology
echo ============================================================

python -u backend\research\test_180d_stopping_overfit_audit.py ^
  --threads %BTC_RESEARCH_THREADS% %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo Stopping-policy audit completed successfully.
) else (
  echo Stopping-policy audit failed with exit code %RC%.
)
pause
exit /b %RC%
