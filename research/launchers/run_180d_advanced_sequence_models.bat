@echo off
setlocal

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

if not exist data\logs mkdir data\logs

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe
if exist ".venv_research_cuda\Scripts\python.exe" set PYTHON_EXE=%CD%\.venv_research_cuda\Scripts\python.exe

set LOG_FILE=data\logs\forecast_360d_advanced_sequence.log

echo Starting BTC 360-day advanced sequence model research...
echo Models: VLSTM, LPatchTST, PatchTST, iTransformer
echo Python: %PYTHON_EXE%
echo Output prefix: forecast_360d_advanced_sequence
echo Log: %LOG_FILE%
echo.
echo Note: Mamba/Mamba2 require mamba_ssm and are not included by default on Windows/Python 3.13.
echo Models will be saved under data\saved_models\research_advanced_sequence\forecast_360d_advanced_sequence

if exist "%LOG_FILE%" del "%LOG_FILE%"

start "BTC 360d Advanced Sequence" powershell -NoExit -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Continue'; Set-Location -LiteralPath '%PROJECT_ROOT%'; & '%PYTHON_EXE%' 'backend\research\train_180d_advanced_sequence_models.py' --days 360 --horizons 5 15 --models 'vlstm,lpatchtst,patchtst,itransformer' --device gpu --max-features 96 --seq-len 60 --seq-max-rows 80000 --batch-size 256 --epochs 5 --output-prefix forecast_360d_advanced_sequence 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

echo Launched in a separate terminal.
echo Monitor with:
echo   powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Wait"

endlocal
