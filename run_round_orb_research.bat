@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

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
