@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0"
echo ============================================================
echo  Polymarket btc-updown RECORDER (shadow-only, read-only)
echo  Writes: data\execution_layer.duckdb (pm_round_snapshots)
echo  Reads the frozen P(Hold) model. Places NO orders.
echo  Leave this window OPEN. Press Ctrl+C to stop.
echo ============================================================
python backend\polymarket\live_btc_updown_recorder.py
pause
