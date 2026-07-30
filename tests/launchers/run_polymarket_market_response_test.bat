@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  Polymarket Market-Response Test - PAPER ONLY
echo  Read-only: edge decay, quote response, parity and depth
echo ============================================================

python -u backend\research\test_polymarket_market_response.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo Market-response test completed successfully.
) else (
  echo Market-response test failed with exit code %RC%.
)
pause
exit /b %RC%
