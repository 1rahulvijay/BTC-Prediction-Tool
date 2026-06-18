@echo off
setlocal

cd /d "%~dp0"

set BASE_PYTHON=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set BASE_PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe

set VENV_DIR=.venv_research_cuda
set VENV_PY=%VENV_DIR%\Scripts\python.exe

echo Creating CUDA PyTorch research environment...
echo Base Python: %BASE_PYTHON%
echo Venv: %VENV_DIR%
echo.

if not exist "%VENV_PY%" (
  "%BASE_PYTHON%" -m venv "%VENV_DIR%"
)

"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install numpy pandas requests scikit-learn pyarrow
"%VENV_PY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

echo.
echo Verifying CUDA PyTorch...
"%VENV_PY%" -c "import torch; print('torch', torch.__version__); print('cuda_available', torch.cuda.is_available()); print('device_count', torch.cuda.device_count()); print('device_name', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"

echo.
echo Done. Sequence-only runner will automatically use this venv if CUDA is available.

endlocal
