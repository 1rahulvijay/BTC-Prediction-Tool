# Recorder Ownership And Core Startup

Date: 2026-08-14  
Status: implemented and validated  
Core launcher: `start.bat`  
Capture launcher: `python capture_app/run.py --record`

## Purpose

The trading application and the long-running market-data collector now have separate process
ownership. This prevents every core-app restart from spawning a second copy of ten historical
DuckDB recorders when the independent `capture_app` is already collecting the same research
families.

The split is intentionally fail-closed:

- `capture_app` records public raw data for future research, replay, feature construction and
  training;
- `start.bat` starts the core application, validates/trains models and serves paper decisions;
- the core still reports stale or missing evidence as degraded or `DO_NOT_TRUST`;
- disabling duplicate recorders does not convert missing inputs into neutral or valid data;
- neither process contains a real-order submission path.

This change removes duplicate process startup. It does not claim that every legacy runtime
consumer has already been replaced by `capture_app`.

## Implemented Change

Commit `6e74b03` changed the default launcher contract:

```text
BTC_START_LEGACY_RECORDERS=0
```

With the default value, `start.bat` prints:

```text
[recorder] External capture_app owns recording; no legacy recorder processes started.
```

It does not invoke `backend/start_recorders_once.ps1`. The old helper remains available only for
temporary compatibility:

```powershell
$env:BTC_START_LEGACY_RECORDERS = "1"
.\start.bat
```

To return the current PowerShell session to the default:

```powershell
Remove-Item Env:BTC_START_LEGACY_RECORDERS -ErrorAction SilentlyContinue
```

Changing this setting does not terminate recorder processes that were already running. Existing
legacy processes continue until their terminals/processes are closed or the computer is restarted.
The launcher deliberately does not kill arbitrary Python processes.

## What `start.bat` Still Does

Recorder decoupling does not remove core startup responsibilities. The launcher still:

1. resolves one Python interpreter and the canonical `BTC_DATA_DIR`;
2. enforces the training-pipeline lease;
3. runs the complete startup invariant/self-test suite;
4. runs incremental archived-data builders unless `BTC_SKIP_BACKFILL=1`;
5. builds and validates the requested research matrix;
6. transactionally trains missing or version-incompatible specialist heads;
7. evaluates the main ensemble against untouched-tail promotion gates;
8. refuses publication when accuracy, calibration, identity or completeness gates fail;
9. starts Vite and Uvicorn only after the blocking startup stages pass;
10. starts backend-owned live clients, paper engines and evidence writers;
11. keeps recorder and evidence health visible in the UI.

The incremental builders in step 4 are offline data preparation jobs, not long-running recorders.
They remain in `start.bat` because they construct the exact training inputs required for the
current model release.

## Backend-Owned Runtime Services

The backend process continues to own the services that feed live inference or maintain decision
evidence:

- Binance spot WebSocket and order-flow state;
- Binance futures book/raw-trade streams plus REST aggregate-trade fallback;
- Coinbase ticker;
- Bybit and Deribit summary polls used by current features;
- Pyth and in-process Polymarket clients;
- live feature, model-metric and prediction writers;
- open-position Protocol B/C evidence and settlement resolution;
- model serving and paper-only Polymarket/Binance decision loops.

These are not the ten detached legacy recorder processes. Browser refresh reconnects to the
backend and does not rerun `start.bat`, rebuild models or start recorders.

## Standalone Capture-App Dataset

`capture_app` stores raw causal observations as ZSTD Parquet partitioned by stream, UTC date and
UTC hour. Every row has a local receive timestamp and retains source/exchange timestamps where
available.

The default configuration enables 19 source families which produce the following 25 datasets:

| Group | Datasets |
|---|---|
| Binance spot | `binance_depth_snapshot`, `binance_depth`, `binance_trades` |
| Binance USD-M | `futures_depth_snapshot`, `futures_depth`, `futures_trades`, `futures_mark`, `futures_open_interest`, `futures_funding_history`, `futures_positioning`, `futures_liquidations` |
| Polymarket | `polymarket_market_meta`, `polymarket_book`, `polymarket_trades`, `polymarket_market_events`, `polymarket_reference`, `polymarket_settlement` |
| Bybit | `bybit_quotes`, `bybit_trades`, `bybit_open_interest`, `bybit_funding_history` |
| Coinbase | `coinbase_ticker` |
| Deribit | `deribit_options` |
| Reference | `pyth_reference` |
| Runtime proof | `collector_runtime` |

The raw dataset supports later construction of L2 imbalance, queue/VWAP simulations, CVD, VPIN,
funding/basis, OI divergence, venue lead/lag, liquidation state, options skew/term structure,
Polymarket execution state and official settlement labels. Derived features must be produced by a
separate causal transformation with explicit source-time/receive-time rules.

The capture app does not know private queue priority, missed passive fills, private order
acknowledgements or actual account executions. Those require an authenticated order-audit stream.

## Reconstruction And Data Integrity

Binance L2 deltas are not treated as standalone books. On each connection the capture app buffers
updates, obtains a REST snapshot, stores a session identifier, discards stale deltas, checks the
first overlap and enforces spot `U/u` or futures `pu` continuity. A true sequence gap invalidates
the local book and starts a new snapshot/session.

Polymarket stores both-token snapshots and deltas under connection/session identity, preserves
unknown events as raw JSON, and records exact market metadata needed to join books to outcomes.
Only an unambiguous final `1/0` result is accepted as settlement truth.

Storage writes use temporary files, `fsync` and atomic rename. Failed writes return detached rows
to memory. Completed hours can be compacted and uploaded to GCS with content hashes, CRC32C,
manifests and immutable catalog pointers. Local deletion is permitted only after verified archival
and outside protected recent hours.

## Current Compatibility Boundary

The most important remaining boundary is executable Polymarket paper pricing.

`backend/price_to_beat.py` currently reads:

```text
data/pm_live_quotes.json
```

That low-latency local bridge is published by the legacy Polymarket recorder, not by
`capture_app`. The backend has its own in-memory `PolymarketClient` and synchronized books, but
those books are not yet exposed through the exact quote-provider contract used by
`price_to_beat.py`.

Therefore, with default recorder decoupling:

- archival Polymarket books and settlements continue to be collected by `capture_app`;
- the core must report executable Polymarket book edge as unavailable/no edge;
- paper-entry gates must abstain rather than read stale archival Parquet;
- current share prices/full ladders that depend on `pm_live_quotes.json` can be absent;
- temporary compatibility mode may be used when this local bridge is deliberately required.

The correct permanent replacement is a tested in-process quote-provider adapter from
`PolymarketClient` to `price_to_beat.py`. It must enforce exact round identity, correct UP/DOWN
token alignment, complete ladders, valid tick/fee metadata, receive-time freshness and fail-closed
handling for stale, one-sided or mismatched books. It should not make the core read open Parquet
partitions as a live quote source.

## Training Boundary

`capture_app` is a durable raw-data lake, not an automatic retraining trigger. The current startup
trainers consume their existing canonical matrix/backfill contracts. Captured Parquet improves a
model only after a versioned builder:

1. selects immutable completed partitions or a GCS catalog generation;
2. verifies schema, hashes, source coverage and missing-hour limits;
3. performs causal as-of joins using source and receive timestamps;
4. reconstructs book sessions only across gap-free spans;
5. derives features without outcome or future-window leakage;
6. joins official settlements for outcome labels;
7. writes a manifest-bound matrix;
8. trains/evaluates under the normal untouched-tail and promotion gates.

More data does not automatically raise precision or profitability. It expands the evidence set and
regime coverage; candidate models must still beat predeclared controls after fees and slippage.

## Legacy Recorder Inventory

The compatibility helper can still launch these detached local recorders:

| Legacy process | Local store/bridge | Historical purpose |
|---|---|---|
| Polymarket quotes/outcomes | `execution_layer.duckdb`, `pm_live_quotes.json` | executable quotes and official outcomes |
| Polymarket exact L2 | `polymarket_l2.duckdb` | full ladders and VWAP/queue research |
| Binance fast ticks | `btc_ticks.duckdb` | sub-second reference observations |
| Cross-exchange microstructure | `microstructure.duckdb` | synchronized flow snapshots |
| Multi-venue event-time | `multi_venue.duckdb` | venue timing and liquidations |
| Binance funding/basis | `funding.duckdb` | funding publications and basis |
| Binance sequenced L2 | `binance_l2.duckdb` | replayable snapshot/delta book |
| High-frequency crossings | `polymarket_crossings_hf.duckdb` | anchor-cross and reversion labels |
| Polymarket cross-window | `cross_window.duckdb` | same-expiry 5m/15m observations |
| Deribit option chain | `deribit_options.duckdb` | strike/expiry volatility surface |

Individual `BTC_SKIP_*` switches still apply only after compatibility mode is enabled. They are
retained to support migration and diagnosis, not as the normal architecture.

## Operator Commands

Validate and run the capture app independently:

```powershell
python capture_app\run.py --selftest
python capture_app\run.py --record
python capture_app\run.py --status
python capture_app\run.py --quality
```

Run the core with default recorder ownership:

```powershell
.\start.bat
```

Run only the launcher's invariant suite, without backfills, training, frontend or backend:

```powershell
$env:BTC_SELFTEST_ONLY = "1"
$env:BTC_AUTO_STOP_EXISTING_APP = "0"
.\start.bat
Remove-Item Env:BTC_SELFTEST_ONLY -ErrorAction SilentlyContinue
Remove-Item Env:BTC_AUTO_STOP_EXISTING_APP -ErrorAction SilentlyContinue
```

Enable the temporary legacy quote bridge and recorder set:

```powershell
$env:BTC_START_LEGACY_RECORDERS = "1"
.\start.bat
```

## Health Interpretation

Process existence is not data health. Required checks are:

- stream heartbeat and durable row/file growth;
- maximum receive-time age;
- gap-free L2 session state;
- readable Parquet footers and stable schemas;
- complete required UTC hours;
- official settlement join coverage;
- database or archive writability;
- runtime loop lag, memory and free disk.

The core UI may continue to display legacy recorder health during migration. A missing optional
research stream should be visible but must not invalidate an unrelated decision. A missing input
required by a pricing, execution or settlement decision must force abstention/`DO_NOT_TRUST`.

## Validation Evidence

The recorder-decoupling change was checked with:

- full `start.bat` `BTC_SELFTEST_ONLY=1` invariant suite: all groups `a` through `m` passed;
- `python capture_app/run.py --selftest`: 21/21 tests passed on 2026-08-14;
- launcher integrity checks: passed;
- `git diff --check`: passed;
- compatibility helper retained and still guarded against duplicate legacy writers;
- default branch: `master`;
- implementation commit: `6e74b03`.

Related recent correctness commits:

- `5a8c4be`: fixed persistence keeper production refit, purging and optional challenger behavior;
- `05a15c6`: fixed futures-flow ingestion and readiness diagnostics;
- `6e74b03`: decoupled legacy recorder startup from the core launcher.

## Current Readiness Statement

The process-ownership change is complete: normal core startup no longer creates duplicate legacy
recorders, while explicit compatibility remains available. The standalone capture app is the
documented archival/research source.

This is not equivalent to full production trading readiness. The direct in-process Polymarket
quote adapter remains unwired, GCP archival must pass a real upload/verification smoke after billing
is enabled, and captured Parquet needs explicit versioned feature builders before it can enter a
production model. The latest 30-day main direction candidate was also correctly rejected by its
promotion gates, so model-dependent actions must remain fail-closed until a later candidate passes.

No recorder, model or audit guarantees accuracy, profit or sustainable income. The valid result is
narrower: duplicate recorder startup has been removed without weakening evidence visibility or
model/trading abstention rules.
