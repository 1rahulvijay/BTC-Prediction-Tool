@echo off
REM ====================================================================
REM  Record-forward L2 + cross-exchange microstructure recorder.
REM  Runs ALONGSIDE the app (separate data/microstructure.duckdb — no
REM  lock conflict). Leave it running for 2-4 weeks to accrue the data
REM  that a future retrain / probe needs to test whether L2 + cross-venue
REM  beats candle-only (the only real path past the direction ceiling).
REM
REM  Check progress any time:  python backend\microstructure_recorder.py --report
REM ====================================================================
cd /d "%~dp0"
echo Starting microstructure recorder (Ctrl+C to stop)...
python backend\microstructure_recorder.py --interval 1.0
pause
