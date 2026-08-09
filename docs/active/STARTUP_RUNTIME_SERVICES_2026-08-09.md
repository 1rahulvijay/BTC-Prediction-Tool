# Startup Runtime Services

Date: 2026-08-09  
Launcher: `start.bat`  
Standalone supervisor: `backend/start_recorders_once.ps1`

## What one launch starts

`start.bat` starts the forward recorders before any long backfill or model training. They keep
collecting while the 1,000-day pipeline runs. The PowerShell helper finds an existing Python
process by recorder entry point and skips it, preventing two writers from opening one DuckDB.
Each newly started process is checked for immediate exit and writes separate unbuffered stdout
and stderr logs under `data/`.

| Service | Store | Default | Purpose |
|---|---|---:|---|
| Polymarket quotes + official outcomes | `execution_layer.duckdb` | on | executable top-of-book, market terms, outcomes and truth quarantine |
| Polymarket exact L2 | `polymarket_l2.duckdb` | on | full public ladders, trades, VWAP and queue research |
| Binance fast BTC ticks | `btc_ticks.duckdb` | on | same-host sub-second reference for Polymarket repricing research |
| Cross-exchange microstructure | `microstructure.duckdb` | on | synchronized order-flow snapshots |
| Multi-venue event time | `multi_venue.duckdb` | on | Binance/Coinbase/Bybit event-time evidence and liquidations |
| Binance sequenced L2 | `binance_l2.duckdb` | on | replayable USD-M snapshot + diff-depth book, capped at 10 GB |
| High-frequency anchor crossings | `polymarket_crossings_hf.duckdb` | on | 1-second crossing/reversion labels with supervised reconnects |
| Polymarket cross-window | `cross_window.duckdb` | on | same-expiry 5m/15m dominance observations and heartbeat |
| Deribit BTC option chain | `deribit_options.duckdb` | on | per-strike executable volatility surface every 30 seconds |

Skip one intentionally by setting its flag to `1` before launch:

```text
BTC_SKIP_PM_RECORDER
BTC_SKIP_PM_L2_RECORDER
BTC_SKIP_BTC_TICK_RECORDER
BTC_SKIP_MICROSTRUCTURE_RECORDER
BTC_SKIP_VENUE_COLLECTOR
BTC_SKIP_BINANCE_L2_RECORDER
BTC_SKIP_HF_CROSSING_RECORDER
BTC_SKIP_CROSS_WINDOW_RECORDER
BTC_SKIP_DERIBIT_CHAIN_RECORDER
```

The fast tick recorder measured about 0.3 GB/day without raw envelopes. The two L2 recorders
have explicit 10 GB caps. Deribit is intentionally not pruned because deleting old batches
would destroy forward evidence; monitor free disk during long runs.

## Services owned by the backend process

These must not be launched again as standalone writers. `server.py` owns the Binance spot and
futures sockets, Coinbase socket, Pyth/Polymarket clients, Bybit/Deribit summary polls, feature
writer, model-metrics writer, open-position action evidence, settlement resolver, model serving,
the Binance paper engine and the Polymarket paper decision loop.

Before the backend starts, `start.bat` also runs incremental archived-data builders, constructs
the requested research matrix, trains version-incompatible/missing heads transactionally, runs
quality checks, starts Vite, and then starts Uvicorn. Browser refresh only reconnects the UI; it
does not invoke the batch launcher or restart training.

## Health semantics

All nine standalone services are registered in `recorder_health.py` and the recorder evidence
audit. The system-health tab shows each one. Core decision dependencies remain required;
research-only streams (fast ticks, high-frequency crossings, cross-window and Deribit) are
visible but optional and cannot globally invalidate an independent market decision.

On Windows, an active DuckDB writer can deny a second read-only connection. Health then tracks
actual DB/WAL progress and labels the method `locked_writer_db_wal_progress`; a held lock whose
files stop advancing becomes `STALLED`. A lock alone is never treated as proof of health.

## Capability boundary

Every standalone recorder is public-data and read-only. None accepts exchange credentials or
submits orders. Starting every collector improves future evidence coverage; it does not improve
today's model automatically and does not prove accuracy, precision, expectancy or profit.

