# Startup Runtime Services

Date: 2026-08-09
Updated: 2026-08-13
Core launcher: `start.bat`
Capture launcher: `python capture_app/run.py --record`

Canonical detailed record:
[RECORDER_OWNERSHIP_AND_CORE_STARTUP_2026-08-14.md](RECORDER_OWNERSHIP_AND_CORE_STARTUP_2026-08-14.md).

## Current ownership

`start.bat` no longer starts the ten legacy DuckDB recorder processes by default. The independent
`capture_app` owns archival/training collection and can run locally or on the always-on GCP host.
The core launcher continues to display recorder/evidence health and fails closed when required
inputs are unavailable.

For temporary backward compatibility only, set `BTC_START_LEGACY_RECORDERS=1` before launching
the core app. That invokes `backend/start_recorders_once.ps1`, whose process-identity checks still
prevent duplicate legacy writers. The normal value is `0`.

| Legacy service | Store | Core default | Purpose |
|---|---|---:|---|
| Polymarket quotes + official outcomes | `execution_layer.duckdb` | off | executable top-of-book, market terms, outcomes and truth quarantine |
| Polymarket exact L2 | `polymarket_l2.duckdb` | off | full public ladders, trades, VWAP and queue research |
| Binance fast BTC ticks | `btc_ticks.duckdb` | off | same-host sub-second reference for Polymarket repricing research |
| Cross-exchange microstructure | `microstructure.duckdb` | off | synchronized order-flow snapshots |
| Multi-venue event time | `multi_venue.duckdb` | off | Binance/Coinbase/Bybit event-time evidence and liquidations |
| Binance funding + basis | `funding.duckdb` | off | immutable 8-hour funding publications plus mark/index basis samples |
| Binance sequenced L2 | `binance_l2.duckdb` | off | replayable USD-M snapshot + diff-depth book, capped at 10 GB |
| High-frequency anchor crossings | `polymarket_crossings_hf.duckdb` | off | 1-second crossing/reversion labels with supervised reconnects |
| Polymarket cross-window | `cross_window.duckdb` | off | same-expiry 5m/15m dominance observations and heartbeat |
| Deribit BTC option chain | `deribit_options.duckdb` | off | per-strike executable volatility surface every 30 seconds |

When legacy compatibility mode is explicitly enabled, skip one service with:

```text
BTC_SKIP_PM_RECORDER
BTC_SKIP_PM_L2_RECORDER
BTC_SKIP_BTC_TICK_RECORDER
BTC_SKIP_MICROSTRUCTURE_RECORDER
BTC_SKIP_VENUE_COLLECTOR
BTC_SKIP_BINANCE_FUNDING_RECORDER
BTC_SKIP_BINANCE_L2_RECORDER
BTC_SKIP_HF_CROSSING_RECORDER
BTC_SKIP_CROSS_WINDOW_RECORDER
BTC_SKIP_DERIBIT_CHAIN_RECORDER
```

The fast tick recorder measured about 0.3 GB/day without raw envelopes. The two legacy L2 recorders
have explicit 10 GB caps. Deribit is intentionally not pruned because deleting old batches
would destroy forward evidence; monitor free disk during long runs.

## Services owned by the backend process

These must not be launched again as standalone writers. `server.py` owns the Binance spot and
futures sockets, Coinbase socket, Pyth/Polymarket clients, Bybit/Deribit summary polls, feature
writer, model-metrics writer, open-position action evidence, settlement resolver, model serving,
the Binance paper engine and the Polymarket paper decision loop.

Before the backend starts, `start.bat` runs incremental archived-data builders, constructs
the requested research matrix, trains version-incompatible/missing heads transactionally, runs
quality checks, starts Vite, and then starts Uvicorn. Browser refresh only reconnects the UI; it
does not invoke the batch launcher or restart training.

To run the complete offline startup invariant suite without launching recorders, backfills,
training, Vite or Uvicorn:

```powershell
$env:BTC_SELFTEST_ONLY = "1"
$env:BTC_AUTO_STOP_EXISTING_APP = "0"
.\start.bat
```

## Health semantics

All ten standalone services are registered in `recorder_health.py` and the recorder evidence
audit. The system-health tab shows each one. Core decision dependencies remain required;
research-only streams (fast ticks, high-frequency crossings, cross-window, Deribit and settled
funding) are visible but optional and cannot globally invalidate an independent market decision.

On Windows, an active DuckDB writer can deny a second read-only connection. Health then tracks
actual DB/WAL progress and labels the method `locked_writer_db_wal_progress`; a held lock whose
files stop advancing becomes `STALLED`. A lock alone is never treated as proof of health.

## Capability boundary

Every recorder is public-data and read-only. None accepts exchange credentials or submits orders.
Collection improves future evidence coverage; it does not improve today's model automatically and
does not prove accuracy, precision, expectancy or profit.

## Compatibility boundary

`capture_app` writes partitioned Parquet and optional GCS archives. It is the source for future
research and training, but it does not currently publish the local low-latency
`data/pm_live_quotes.json` bridge consumed by executable Polymarket paper-entry gates. With legacy
recorders disabled, those gates must report unavailable/no edge rather than substituting stale or
archival data. Set `BTC_START_LEGACY_RECORDERS=1` only when that local compatibility bridge is
deliberately required.
