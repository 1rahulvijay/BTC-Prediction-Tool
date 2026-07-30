@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"

set "PY=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"

echo Scoring all saved W90/W400/W1265 experts on identical new matrix rows.
echo Shadow only. This command cannot promote or route a trading decision.

"%PY%" -u backend\research\window_expert_shadow.py
if errorlevel 1 (
  echo Multi-window forward shadow scoring FAILED.
  exit /b 1
)
echo Multi-window forward shadow scoring completed.
endlocal
