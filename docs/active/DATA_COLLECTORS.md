# Data Collectors — unified registry (2026-06-13)

Every place the system collects + saves data, built to ONE consistent pattern. Two kinds:
**offline builders** (run on demand from archived data, no laptop uptime) and **live recorders**
(write to DuckDB while serving). All offline builders share the same shape so they're interchangeable
to operate and review.

## The standard pattern (every offline builder follows this)
1. `argparse`: `--start S --end E` for bulk, `--validate DATE` for a one-day dry-run (no write).
2. Reuse the **keystone loaders** (`backfill_trade_features.download_day` / `load_aggtrades`,
   ms-normalized) so the math matches everywhere.
3. A **testable pure core** (`build_*_for_day(...)`) separate from I/O — unit-tested on synthetic data.
4. Cached downloads (idempotent), sanity stats printed, output to `data/*.parquet`.
5. **Keystone parity:** offline computation MUST equal the live recorder's (same thresholds/bucketing)
   — else the feature is constant in serving (the train/serve gap). Backfill + live recorder are twins.
6. **Always `--validate` one day before a bulk run.**

---

## Offline builders (run from archived data — NO uptime)

| Script | Output | Source | Status | Validate |
|---|---|---|---|---|
| `backfill_trade_features.py` | `trade_features_backfill.parquet` | Binance SPOT aggTrades + funding | ✅ validated (2026-06-12: 787k→1,440 bars) | `--validate DATE` |
| `build_persistence_dataset.py` | `persistence_dataset.parquet` | Binance SPOT aggTrades (tick) | ✅ validated (31,924 snapshots; **late-entry 90–97% held**) | `--validate DATE` |
| `build_crossvenue_flow.py` | `crossvenue_flow.parquet` | Binance SPOT + **PERP** aggTrades | ✅ validated (1,440 bars; basis ~−5bps, cvd_div ±300) | `--validate DATE` |

**1. `backfill_trade_features.py`** — per-1m-bar CVD (1m/5m), VPIN, large-trade flow, OFI family.
Feeds the existing slots 109–125. *Live twin:* `order_flow` recorder (same EWMA thresholds).

**2. `build_persistence_dataset.py`** — intra-window snapshots `(distance, seconds_left, position,
vol_60s_pct) → held` label, every 15s per 5m/15m window, reconstructed at tick fidelity. Feeds the
**A1 persistence / T3 engine.** *Live twin:* `persistence_snapshot` DuckDB table (the A1 recorder).
UNION offline+live for training. **Result: the 95% tier is real** (late-entry held 90–97% in 1 day).

**3. `build_crossvenue_flow.py`** — per-1m-bar `cvd_spot, cvd_perp, cvd_divergence,
perp_spot_basis_bps, vol_spot, vol_perp` (Binance spot-vs-perp; perp leads, basis tension precedes
mean-reversion). Chosen over Coinbase/Bybit because perp aggTrades ARE archived (Coinbase has no bulk
history → would re-create the train/serve gap). **Parity TODO before it becomes a model feature:**
wire a live Binance futures aggTrade stream computing the same per-bar CVD, then add the slots.

---

## Live recorders (write to DuckDB while serving — twin of an offline builder)

| Recorder | Table | Captures | Activates | Offline twin |
|---|---|---|---|---|
| **B1** | `feature_outcome_log(ts, schema_hash, regime, features[])` | full live feature vector per cycle (incl. live-only L2) | next restart | *(none — L2 not archivable)* |
| **A1** | `persistence_snapshot(round_id, horizon, ts, seconds_left, distance, position)` | live round trajectory; label via join to `price_to_beat` | next restart | `build_persistence_dataset.py` |
| **A4 perp** | `perp_cvd_live(ts, cvd_perp, vol_perp, perp_price)` | live per-1m-bar PERP CVD (futures aggTrade); parity-verified vs offline | next restart | `build_crossvenue_flow.py` (perp leg) |
| **A10** | `setup_fingerprint(ts, horizon, regime, raw_direction, conviction, agreement, confidence, grade, cvd_1m, gex, expected_move)` | per-prediction decision context; joins `predictions_{h}m` for outcome | next restart | *(derivable from B1 too)* |
| **GEX** | `gex_live(ts, gex, total_gamma, spot, pcr, atm_iv)` | live dealer gamma (Deribit) | next restart | *(live-only; no archive)* |
| **Deribit chain** | `deribit_options.duckdb` (`deribit_chain_batches`, `deribit_chain_snapshots`) | per-expiry/strike BTC call/put bid, ask, mark, IV, OI and receive/exchange time | standalone public recorder | *(forward-only)* |
| outcomes | `predictions_{h}m`, `price_to_beat`, `model_predictions`, `ab_results` | predictions + resolved outcomes (labels) | live | — |

### Open-position Protocol B/C recorder (2026-08-03)

`backend/open_position_action_recorder.py` writes causal open-position evidence into the dedicated
`data/open_position_actions.duckdb` store. The main app database remains
`data/analytics.duckdb`:

- `open_position_recorder_heartbeats`: one row per capture cycle, including no-open-position cycles;
- `open_position_action_snapshots`: same-time HOLD/EXIT/REDUCE_50/SWITCH/LOCK inputs;
- `position_crossing_state`: causal anchor-side transition state;
- `post_entry_crossing_outcomes`: 5/15/30/60-second reversion and official final-crossing outcomes;
- `open_position_action_outcomes`: append-only proxy and official action-arm values.

Only official Polymarket settlement can complete Protocol B/C evidence gates. A Pyth proxy may be
recorded for diagnostics, but it cannot turn a forward protocol into `COMPLETE`. Readiness can be
queried while the writer owns DuckDB through `/api/evidence-readiness`.

B1 is the ONLY collector with no offline twin — live L2 order-book depth (slots ~52–72) is not
archived by Binance. That subset alone needs live accumulation (or a paid Tardis.dev L2 archive).

### L2 / order-flow sourcing reality (operator research 2026-06-13) — the bakeoff (§5bt) proved this IS the missing edge
- **Free historical = trades/aggTrades ONLY** (`data.binance.vision/data/spot/daily/{aggTrades,trades,klines}/BTCUSDT/`).
  These give order-flow: buy/sell volume delta, trade imbalance, aggressive-flow ratio, large-print,
  VWAP, short-RV — **we already backfill these** (`backfill_trade_features.py` → slots 109–125). They
  are NOT true L2 depth; the bakeoff shows price/vol/flow-derived features alone stay coin-flip.
- **True historical L2 depth = mostly NOT free** — BUT one free lead to investigate (operator
  2026-06-13): **Binance FUTURES `bookDepth` IS public**
  (`data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/`). It is **snapshot depth**
  (periodic, % -from-mid notional buckets), NOT full incremental L2, and futures (not spot) — so it
  can't reconstruct exact book state / queue position, but it MAY give backfillable depth-imbalance
  features for a retrain instead of waiting weeks for live B1. **TODO: feasibility-check one day's
  file** (`--validate` pattern) before counting on it. Paid full L2 = Tardis.dev; Crypto Lake
  (`lakeapi`) advertises free L2 — verify pair/exchange/date coverage. Synthetic L2 from aggTrades
  (estimated depth around mid) is fine for ML FEATURES but NOT for queue/slippage/market-making sim.
- **The realistic FREE path = collect live forward = exactly what B1 does.** B1 logs the full live
  feature vector incl. the live L2 slots (`depth20@100ms` → OBI/walls/queue) every cycle. So "start a
  Binance `@depth` collector now" ≈ already running via B1 — it just needs ~3–4 weeks of uptime before
  those slots VARY enough to train (the SPEC Track-B1 plan). Decision: **rely on B1 live accumulation
  for L2** + the trade-derived order-flow we already backfill; revisit a paid L2 archive only if a
  faster L2-bearing retrain is worth the spend.

---

## How they feed the retrain (see [RETRAIN_RUNBOOK.md](RETRAIN_RUNBOOK.md))
- `trade_features_backfill.parquet` → existing aggTrade feature slots (already wired).
- `crossvenue_flow.parquet` → new A4 slots (after the live perp twin is wired, for parity).
- `persistence_dataset.parquet` (+ live `persistence_snapshot`) → the A1 persistence model (separate head).
- `feature_outcome_log` (live) → the L2-microstructure increment for a *second* retrain.

## Quick operate (90-day collection, all offline, no uptime)
```
python backend/backfill_trade_features.py  --start <90d-ago> --end <today>
python backend/build_persistence_dataset.py --start <90d-ago> --end <today>
python backend/build_crossvenue_flow.py     --start <90d-ago> --end <today>
```
Each: ~68–93 MB/day/source cached; validate one day first. All three verified end-to-end 2026-06-13.

## Polymarket Full-L2 Execution Recorder (2026-07-01)

`backend/polymarket/l2_recorder.py` is a standalone public WebSocket collector for current/next BTC
5m and 15m UP/DOWN tokens. It reconstructs complete books, records level changes and trades, and writes
exact size-specific taker VWAP into `data/polymarket_l2.duckdb`. Calculated states are sampled at one
second per token while causal level updates remain event-by-event for queue replay.

Run `.\run_polymarket_l2_recorder.bat`; analyze with `.\tests\launchers\run_polymarket_l2_execution_test.bat`.
Queue output is conservative/base/optimistic because public L2 does not reveal order IDs or true rank.
See [POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md](POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md).

## Binance Sequenced L2 Recorder (2026-07-31)

`backend/venues/binance_l2_recorder.py` is the durable USD-M BTCUSDT depth recorder. It is separate
from the top-of-book multi-venue recorder and from the main app database.

It records:

- a REST depth snapshot with `lastUpdateId`;
- every 100 ms diff-depth event with `U`, `u`, `pu`, exchange time and receive time;
- raw bid/ask changes and their SHA-256;
- a deterministic top-20 book checksum after each applied event;
- sequence gaps, reconnect sessions and current progress.

It does not store credentials and cannot submit orders. A gap invalidates the current local book
and starts a new snapshot/session. The raw snapshot plus diffs can be replayed and checksum
verified.

Commands:

```powershell
.\start_binance_l2_recorder.bat
python backend\venues\binance_l2_recorder.py --report
python backend\venues\binance_l2_recorder.py --selftest
python backend\venues\rl_data_readiness.py
```

The default archive is `data/binance_l2.duckdb` with a 10 GB size cap. `start.bat` launches one
hidden instance through `backend/start_recorders_once.ps1`; set
`BTC_SKIP_BINANCE_L2_RECORDER=1` only when disk or network constraints require it.

Capability boundary:

- deterministic local-book replay: available after enough gap-free rows accrue;
- exact visible-depth taker VWAP: computable from replayed books;
- conservative public-trade queue model: research only;
- exact queue priority/passive fill: unavailable from aggregate public L2;
- production execution RL: blocked until defensible forward fill labels exist.

## Deribit Per-Strike Option Chain Recorder (2026-07-31)

`backend/venues/deribit_option_chain_recorder.py` is a standalone, public,
read-only BTC option-chain recorder. It persists one batch every 30 seconds by
default, including instrument, expiry, strike, call/put type, underlying,
bid/ask/mid/mark, IV in percentage units, open interest, volume and available exchange/receive
timestamps. It never reads credentials and has no order-submission route.

Commands:

```powershell
research\launchers\run_deribit_option_chain_recorder.bat
research\launchers\report_deribit_option_chain_recorder.bat
python backend\venues\deribit_option_chain_recorder.py --selftest
```

The first public smoke stored 942 rows across 13 expiries with zero parser
drops. This starts a forward dataset; it is not yet evidence that buying a
straddle beats executable implied volatility, spread, fees or hedge cost.
