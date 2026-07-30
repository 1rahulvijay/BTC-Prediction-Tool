@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  VWAP + Bollinger + Mechanical-Level Path Research
echo  Causal 5m/15m holdout test - NO LIVE MODEL CHANGES
echo ============================================================
python -u backend\research\test_vwap_bollinger_path_features.py %*
set "RC=%ERRORLEVEL%"
echo.
echo VWAP/Bollinger path research exited with code %RC%.
pause
exit /b %RC%
