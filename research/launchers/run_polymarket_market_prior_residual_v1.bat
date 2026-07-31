@echo off
setlocal
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
python research\polymarket_market_prior_residual_v1\run.py %*
endlocal
