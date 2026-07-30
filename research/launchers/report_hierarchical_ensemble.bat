@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m backend.research.forecast_adapters_v1.run_adapters
if errorlevel 1 exit /b %errorlevel%
"%PYTHON_EXE%" -m backend.research.hierarchical_ensemble_v1.report %*
exit /b %errorlevel%
