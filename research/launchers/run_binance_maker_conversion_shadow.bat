@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo BINANCE_MAKER_CONVERSION_V1
echo Research shadow only. No API keys. No order submission.
echo Evidence: data\research\binance_maker_conversion_v1\shadow.duckdb

:run
"%PYTHON_EXE%" -u backend\research\binance_maker_conversion_v1\live_shadow.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" exit /b 0
echo Shadow stopped with exit code %EXIT_CODE%. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto run
