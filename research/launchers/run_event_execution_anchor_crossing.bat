@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"

echo EVENT_EXECUTION_AND_ANCHOR_CROSSING_V1
echo Research only. This does not start, stop, or modify the live backend.

"%PYTHON_EXE%" backend\research\event_execution_v1\run_campaign.py
if errorlevel 1 goto :failed

"%PYTHON_EXE%" backend\research\event_execution_v1\validate_result.py
if errorlevel 1 goto :failed

echo Campaign and validation completed.
exit /b 0

:failed
echo Campaign failed. Review the latest run.log.
exit /b 1
