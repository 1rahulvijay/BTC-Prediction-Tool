@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

echo ============================================================
echo  TRAIN POLYMARKET REPRICING SHADOW V1
echo  RESEARCH ARTIFACT ONLY - NOT LOADED BY THE PRODUCTION APP
echo ============================================================

"%PYTHON_EXE%" -u backend\research\polymarket_repricing_shadow_v1\train_event_bundle.py %*
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" backend\research\polymarket_repricing_shadow_v1\live_shadow.py --selftest
exit /b %ERRORLEVEL%
