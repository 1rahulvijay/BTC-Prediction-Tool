@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" -u -m backend.research.poly_complete_set_arbitrage_v1.report %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Report finished with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
