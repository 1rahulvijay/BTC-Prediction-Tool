@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
set "PYTHONPATH=%~dp0backend;%~dp0backend\research;%~dp0"
set "BTC_DATA_DIR=%~dp0data"

"%PYTHON_EXE%" -u backend\research\polymarket_repricing_shadow_v1\report.py %*
exit /b %ERRORLEVEL%
