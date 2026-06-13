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
| outcomes | `predictions_{h}m`, `price_to_beat`, `model_predictions`, `ab_results` | predictions + resolved outcomes (labels) | live | — |

B1 is the ONLY collector with no offline twin — live L2 order-book depth (slots ~52–72) is not
archived by Binance. That subset alone needs live accumulation (or a paid Tardis.dev L2 archive).

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
