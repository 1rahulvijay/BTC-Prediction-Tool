@echo off
setlocal
cd /d "%~dp0\..\.."
python research\polymarket_market_prior_residual_v1\run.py %*
endlocal
