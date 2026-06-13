# Retrain Runbook — the 60–90 day "complete retrain" pipeline (2026-06-13)

The plan to go from a coin-flip to a harvestable edge via ONE deliberate, mostly-OFFLINE retrain.
Built around the insight that most of the data is reconstructable from history (no weeks of uptime).
Companion to [SPEC_ACCURACY_NEXT_RETRAIN.md](SPEC_ACCURACY_NEXT_RETRAIN.md) and the
[IMPLEMENTATION_QUEUE.md](IMPLEMENTATION_QUEUE.md) matrix.

**Golden rule (why this is staged, not one blast):** adding columns to `features.py FEATURE_NAMES`
bumps the schema hash → ALL saved models become unloadable until a retrain completes. So feature
additions MUST be bundled WITH the retrain and executed together; you cannot add features to a
running, frozen app. Each step is validated on purged walk-forward sign-truth before adoption.

---

## Phase 0 — OFFLINE data collection (no laptop-uptime, no serving-loop risk)

| Script | Status | Produces | Covers |
|---|---|---|---|
| `backfill_trade_features.py --start S --end E` | ✅ exists | `trade_features_backfill.parquet` (CVD/VPIN/large-trade/OFI family) | aggTrade-derived features |
| `build_persistence_dataset.py --start S --end E` | ✅ **built+validated** (late-entry held 90–97%) | `persistence_dataset.parquet` (A1 snapshots + held labels) | the A1 / T3 engine |
| `build_crossvenue_flow.py --start S --end E` | ✅ **built+validated** (basis ~−5bps, cvd_div ±300) | `crossvenue_flow.parquet` (spot-vs-perp CVD divergence + basis) | A4 backfillable flow |

All three pull archived data (data.binance.vision + Coinbase/Bybit trade history), compute features
with the SAME bucketing/thresholds as the live recorders (keystone parity), and write parquet.
60–90 days of aggTrades ≈ 68 MB/day → ~5–6 GB cache (fine).

**Run now (no retrain needed, no uptime):**
```
python backend/backfill_trade_features.py --start <90d-ago> --end <today>
python backend/build_persistence_dataset.py --start <90d-ago> --end <today>
```

## Phase 1 — Feature additions (schema bump → bundle with the retrain)

Append-only (never reorder 0–129; saved models index by position). Then bump `MODEL_ARCH_VERSION`.
- **A8 session/time** — Asia/EU/US flags, minutes-to-funding, top/bottom-of-hour, weekend. Free
  history (timestamp-derived). ~4–6 slots.
- **A4 cross-venue flow** — `cvd_cb_binance_div`, `cvd_bybit_binance_div`, `flow_lead_lag_cb`,
  `aggressive_buy_ratio_multi`, `large_print_venue_skew` (fed by `build_crossvenue_flow.py`).
- **`rv_term_structure`** — rv_1m / rv_15m (free, kline-derived).
- **`variance_ratio`** — var(k-period ret) / (k · var(1-period ret)); mean-revert (<1) vs
  trend (>1). Kline-derived → free full history. (added 2026-06-13)
- **price-efficiency** — permanent-vs-temporary impact (`mid_t+30s` vs `mid_t+1s` after an
  aggressive-flow impulse) + short-lag return autocorrelation. From aggTrades → backfillable
  via the trade_features pipeline. New non-L2 microstructure. (added 2026-06-13)
- **GEX** — `gex_live` side table is already accumulating (see §5bg / `compute_gex`); add the
  recorded value as a slowly-varying feature here, aligned on ts. (added 2026-06-13)
- **ATR triple-barrier labels** — cleaner target (V5 §2.5a); a label change, not a feature.

## Phase 2 — A7 Optuna tuning (offline, overnight, no new data)

`pip install optuna`; per-horizon search scored on PURGED WALK-FORWARD SIGN-TRUTH (never raw OOF).
Adopt a config ONLY when it beats the incumbent on the held-out scorecard. Highest-EV tuning lever.

## Phase 3 — The retrain (operator-run, multi-hour GPU)

1. Set the window to 60–90 days (boot `historical_days` / `BTC_HISTORICAL_DAYS`).
2. Ensure Phase-0 parquets are present (the new feature builders read them, like the existing one).
3. Run the retrain (start.bat → it trains on the bumped schema). Class-balanced loss + ATR labels +
   the new features. v6 already has TCN full seat + GPU + SGD removed.
4. The held-out conformal + purged OOF print the report card. Adopt only if sign-truth improves.

## Phase 3.5 — WARM-START calibration / meta / signal_history from OOF (leak-free)

The live-learning layers sit dormant for days after every retrain ("calibrator waiting 1/150",
"meta insufficient 0/100"). Seed them at train time from the **OOF predictions the stacker already
generates** (purged CV → never trained on the bar it predicts → LEAK-FREE):
- **Calibration:** fit the isotonic/precision bins on `(OOF confidence, regime, conviction →
  realized sign-truth)`. Active from the first live tick instead of after ~days of live leans.
- **Meta-model:** train the trust gate on `(OOF context → was it right)`. Active immediately.
- **signal_history:** replay the feature pipeline over recent history to warm the rolling buffer so
  backfillable features are live from boot (no uptime fill wait).

HARD RULE: **OOF / out-of-sample ONLY.** Seeding from in-sample predictions makes the model look
overconfident-correct → miscalibration (the retracted-"90%" failure mode). Live leans then refine the
warm start. Caveat: reflects the trainable feature set (L2 constant in training) — consistent with how
the model serves until the L2 increment lands. (kNN A10-voter also generates its OOF here for its seat.)

## Phase 4 — A1 persistence model (separate head, own validation)

Train a binary classifier on `persistence_dataset.parquet` (offline history) UNIONed with the live
`persistence_snapshot` rows: features `(distance, distance_pct, seconds_left, seconds_elapsed,
vol_60s_pct, regime?)` → label `held`. This is the T3 engine: surface P(hold) only when the
held-out reliability is calibrated (its 90% wins ~90%). Powers the late-entry tier + (later) the
re-anchored fair value.

---

## Validation gates (no step ships without passing)
- Each feature/label/tuning change: **one change → retrain → purged walk-forward sign-truth → adopt
  only if it beats incumbent.** No bundling unmeasured changes (that's how the old "90%" lie happened).
- The headline number that decides success: **5m committed-lean sign-truth ≥ ~56–60%, UP/DOWN within
  ~8 pts.** Below that, direction is still information-limited → iterate Phase 0/1, don't ship betting.

## What's offline vs needs-live (the timeline answer)
- **Offline / buildable now:** A1 persistence (✅ builder done), A4 cross-venue flow, A8 time, ATR
  labels, A7 tuning, the 60–90 day historical retrain. **No weeks of uptime.**
- **Live-only (the sole exception):** L2 order-book depth features (slots ~52–72) — not archived by
  Binance. Either let live B1 accumulate them for a *second* retrain, or buy a historical L2 archive
  (Tardis.dev) to backfill even those.

## Order of operations (recommended)
1. **Now:** run the two existing Phase-0 backfills over 90 days (offline).
2. **Next build:** `build_crossvenue_flow.py` (A4) — then Phase-1 feature additions as one bundle.
3. **Then (operator):** Phase 2 Optuna → Phase 3 retrain (90 days) → measure.
4. **Then:** Phase 4 A1 persistence head → the T3 precision tier.
