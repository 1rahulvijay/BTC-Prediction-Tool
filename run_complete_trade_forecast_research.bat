@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if not defined BTC_TRADE_FORECAST_FAMILIES set "BTC_TRADE_FORECAST_FAMILIES=hgb,lgb,cat"
set "PYTHONUNBUFFERED=1"
set "PYTHONPATH=%CD%\backend"

echo [1/6] Building immutable complete-trade dataset from official settlements and L2 books...
"%PYTHON_EXE%" -u -m backend.trade_forecast.build_complete_trade_dataset
if errorlevel 1 goto :failed

echo [2/6] Training executable share-path and complete-trade event heads...
"%PYTHON_EXE%" -u -m backend.trade_forecast.train_share_path_model
if errorlevel 1 goto :failed

echo [3/6] Training time-indexed BTC path and competing-risk heads...
"%PYTHON_EXE%" -u -m backend.trade_forecast.train_btc_path_model
if errorlevel 1 goto :failed

echo [4/6] Training arrival-slippage, fill, quote-survival and capacity heads...
"%PYTHON_EXE%" -u -m backend.trade_forecast.train_execution_heads
if errorlevel 1 goto :failed

echo [5/6] Resolving any matching historical shadow outcomes...
"%PYTHON_EXE%" -u -m backend.trade_forecast.trade_outcome_resolver
if errorlevel 1 goto :failed

echo [6/6] Printing evidence and artifact-integrity report...
"%PYTHON_EXE%" -u -m backend.trade_forecast.report_complete_trade_forecast
if errorlevel 1 goto :failed

echo Complete-trade research lane finished. It remains shadow-only.
exit /b 0

:failed
echo Complete-trade research lane failed. Review the last error above.
exit /b 1
