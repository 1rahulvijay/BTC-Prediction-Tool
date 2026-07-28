@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo Economic V2 Blueprint Campaign
echo ==============================
echo Research only. No live models or paper policies are changed.
echo E1: LONG/SHORT common magnitude and residual direction factors
echo E2: Polymarket market-price residual with executable-ask delay stress
echo.

"%PYTHON%" backend\research\economic_v2\run_blueprint_campaign.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo Campaign FAILED with exit code %RC%.
) else (
    echo Campaign completed. Results are under data\research\economic_v2.
)
exit /b %RC%
