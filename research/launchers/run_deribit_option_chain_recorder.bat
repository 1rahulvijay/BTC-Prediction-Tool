@echo off
setlocal
cd /d "%~dp0\..\.."
python backend\venues\deribit_option_chain_recorder.py --interval 30 %*
endlocal
