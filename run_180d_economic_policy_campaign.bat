@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Running frozen 180-day economic specialist campaign.
echo.
echo Data: earliest complete 180 days in research_matrix_1m.parquet
echo Split: 120d base train + 15d ACT train + 15d selection + 30d locked test
echo Models: direct LONG/SHORT classifiers, expected-net regressors, q20 and ACT/SKIP
echo Dynamic exit: one fixed challenger versus HOLD on identical locked-test entries
echo Research only. No live or paper model is replaced.
echo.

"%PYTHON_EXE%" backend\research\train_180d_economic_policy_campaign.py
if errorlevel 1 (
  echo.
  echo Campaign FAILED. Review data\research\economic_policy_campaign_180d logs.
  exit /b 1
)

echo.
echo Campaign completed.
endlocal
