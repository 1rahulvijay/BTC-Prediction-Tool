@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0backend;%~dp0"
set "BTC_DATA_DIR=%~dp0data"
echo ============================================================
echo  Polymarket btc-updown RECORDER (shadow-only, read-only)
echo  Writes: data\execution_layer.duckdb
echo          pm_round_snapshots + official pm_round_settlements
echo  Reads the frozen P(Hold) model. Places NO orders.
echo  Restart-safe: resolves the persisted settlement backlog first.
echo  Leave this window OPEN. Press Ctrl+C to stop.
echo ============================================================
python -u backend\polymarket\live_btc_updown_recorder.py --settle-batch 100
pause
