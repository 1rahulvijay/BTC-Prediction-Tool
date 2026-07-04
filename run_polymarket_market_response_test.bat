@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

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
