@echo off
echo Starting BTC Quantum Trader Backend...
set "PROJECT_ROOT=%~dp0"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
cd /d "%PROJECT_ROOT%backend"
python server.py
pause
