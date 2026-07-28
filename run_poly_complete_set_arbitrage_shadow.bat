@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Starting POLY_COMPLETE_SET_ARBITRAGE_V1 research shadow...
echo This process reads public market data only. It cannot submit orders.
"%PYTHON_EXE%" -u -m backend.research.poly_complete_set_arbitrage_v1.live_shadow %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Complete-set shadow finished with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

