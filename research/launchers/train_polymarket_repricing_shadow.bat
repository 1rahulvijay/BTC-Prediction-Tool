@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%backend\research;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  TRAIN POLYMARKET REPRICING SHADOW V1
echo  RESEARCH ARTIFACT ONLY - NOT LOADED BY THE PRODUCTION APP
echo ============================================================

"%PYTHON_EXE%" -u backend\research\polymarket_repricing_shadow_v1\train_event_bundle.py %*
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" backend\research\polymarket_repricing_shadow_v1\live_shadow.py --selftest
exit /b %ERRORLEVEL%
