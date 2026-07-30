@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

set "PYTHON=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo Event-Time Evidence Accumulator Campaign
echo ========================================
echo Research only. No live models or paper policies are changed.
echo Development: older locked event-time predictions
echo Locked test: later non-overlapping event-time predictions
echo Output: data\research\event_evidence_accumulator
echo.

"%PYTHON%" backend\research\event_evidence_accumulator\run_accumulator_campaign.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo Campaign FAILED with exit code %RC%.
) else (
    echo Campaign complete.
)
exit /b %RC%
