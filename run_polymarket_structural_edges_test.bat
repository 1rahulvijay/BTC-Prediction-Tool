@echo off
setlocal
cd /d "%~dp0"
set OMP_NUM_THREADS=2
set OPENBLAS_NUM_THREADS=2
set MKL_NUM_THREADS=2
echo Polymarket structural-edge research: complement arb, opening drift, model straddles
echo CPU is capped at 2 threads. This script never sends an order.
"C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe" -u backend\research\test_polymarket_structural_edges.py %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" echo FAILED with exit code %EXIT_CODE%
if "%EXIT_CODE%"=="0" echo COMPLETE - see data\research\polymarket_structural_edges\REPORT.md
pause
exit /b %EXIT_CODE%
