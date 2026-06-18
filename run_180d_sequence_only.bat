@echo off
setlocal

cd /d "%~dp0"

if not exist data\logs mkdir data\logs

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe
if exist ".venv_research_cuda\Scripts\python.exe" set PYTHON_EXE=%CD%\.venv_research_cuda\Scripts\python.exe

set LOG_FILE=data\logs\forecast_180d_sequence_only.log

echo Starting BTC 180-day sequence-only forecaster...
echo This skips regression, classification, and quantile tabular phases.
echo Output prefix: forecast_180d_sequence_only
echo Python: %PYTHON_EXE%
echo If .venv_research_cuda is installed, sequence models can use CUDA GPU.
echo Log: %LOG_FILE%

if exist "%LOG_FILE%" del "%LOG_FILE%"

start "BTC 180d Sequence Only" powershell -NoExit -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; Set-Location -LiteralPath '%~dp0'; & '%PYTHON_EXE%' 'backend\research\train_360d_multitarget_forecaster.py' --days 180 --horizons 5 15 --models 'lstm,gru,tcn,transformer' --include-sequence --sequence-targets core --device gpu --skip-regression --skip-classification --skip-quantile --output-prefix forecast_180d_sequence_only --max-features 160 --n-jobs 2 --seq-max-features 48 --seq-max-rows 100000 --seq-batch-size 384 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo Launched in a separate terminal.
echo Monitor with:
echo   powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Wait"

endlocal
