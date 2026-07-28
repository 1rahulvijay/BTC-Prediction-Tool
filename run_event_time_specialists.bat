@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo Event-Time Specialist Research
echo ==============================
echo This is isolated research. It does not replace live models.
echo Window: latest 30 paired spot/perpetual trade days
echo Targets: first-barrier direction, movement, round-trip, ACT/SKIP
echo.

"%PYTHON%" backend\research\train_event_time_specialists.py --days 30 --horizons 5 15 30 60 --threads 4
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo Experiment FAILED with exit code %RC%.
) else (
    echo Experiment completed. Results are under data\research\event_time_specialists.
)
exit /b %RC%
