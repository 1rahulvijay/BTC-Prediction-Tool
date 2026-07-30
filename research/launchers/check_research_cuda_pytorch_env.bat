@echo off
setlocal

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

set PYTHON_EXE=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe
if exist ".venv_research_cuda\Scripts\python.exe" set PYTHON_EXE=%CD%\.venv_research_cuda\Scripts\python.exe

echo Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import torch; print('torch', torch.__version__); print('cuda_available', torch.cuda.is_available()); print('device_count', torch.cuda.device_count()); print('device_name', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"

endlocal
