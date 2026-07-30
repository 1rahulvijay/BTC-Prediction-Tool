@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  Causal Polymarket Round-ORB Research - NO LIVE CHANGES
echo  Tests path lift and P(Hold) veto quality on 5m/15m rounds
echo ============================================================
python -u backend\research\test_round_orb_features.py %*
set "RC=%ERRORLEVEL%"
echo.
echo Round-ORB research exited with code %RC%.
pause
exit /b %RC%
