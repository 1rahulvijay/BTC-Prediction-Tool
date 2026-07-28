@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

echo ============================================================
echo  POLYMARKET REPRICING SHADOW V1
echo  RESEARCH ONLY - NO API KEYS, NO ORDERS, NO PAPER ACTIONS
echo ============================================================

if not exist "data\research\polymarket_repricing_shadow_v1\event_model_bundle.joblib" (
    echo Missing event-model bundle.
    echo Run train_polymarket_repricing_shadow.bat explicitly, then retry.
    goto :failed
)

"%PYTHON_EXE%" backend\research\polymarket_repricing_shadow_v1\live_shadow.py --selftest
if errorlevel 1 goto :failed

"%PYTHON_EXE%" -u backend\research\polymarket_repricing_shadow_v1\live_shadow.py %*
exit /b %ERRORLEVEL%

:failed
echo Repricing shadow failed closed. No fallback model was activated.
exit /b 1
