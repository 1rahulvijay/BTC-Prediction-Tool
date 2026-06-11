@echo off
REM ============================================================
REM  BTC Trade-Feature Backfill  (one-time, ~30 days)
REM  Double-click this with the MAIN APP CLOSED.
REM  It downloads Binance SPOT aggTrades + futures premium-index
REM  and builds data\trade_features_backfill.parquet, which the
REM  next retrain uses to give the model full CVD history.
REM  Defaults to the last 30 UTC days; pass dates to override:
REM     backfill.bat --start 2026-05-10 --end 2026-06-09
REM  Extracted CSVs are deleted after each day (use --keep-cache
REM  to keep them). Re-running is safe.
REM ============================================================
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%backend;%PROJECT_ROOT%"
set "BTC_DATA_DIR=%PROJECT_ROOT%data"

echo ============================================================
echo  Backfill starting. Keep this window open until it finishes.
echo  This can take a while (multi-GB download + processing).
echo ============================================================
python backend\backfill_trade_features.py %*

echo.
echo ------------------------------------------------------------
echo  Backfill done. Output: data\trade_features_backfill.parquet
echo  Now launch start.bat — the app will retrain and the overlay
echo  will fill CVD history across the training window.
echo ------------------------------------------------------------
pause
