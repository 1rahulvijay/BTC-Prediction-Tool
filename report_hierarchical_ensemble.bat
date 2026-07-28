@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
"%PYTHON_EXE%" -m backend.research.hierarchical_ensemble_v1.report %*
exit /b %errorlevel%
