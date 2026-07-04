@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

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
