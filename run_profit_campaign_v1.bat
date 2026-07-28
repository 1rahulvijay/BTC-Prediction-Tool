@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Starting PROFIT_CAMPAIGN_V1 research-only suite...
echo Campaigns: BINANCE_COST_AWARE_NET_PNL_V1 and BINANCE_DYNAMIC_EXIT_V1
echo No paper or live trading behavior will be changed.
"%PYTHON_EXE%" -u -m backend.research.profit_campaign_v1.run_campaigns %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo PROFIT_CAMPAIGN_V1 failed with exit code %EXIT_CODE%.
  exit /b %EXIT_CODE%
)
echo PROFIT_CAMPAIGN_V1 completed.
exit /b 0
