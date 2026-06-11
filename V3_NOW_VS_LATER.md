# V3 Execution — NOW vs LATER

**Created:** 2026-06-10. Owner: operator + Claude.
**Goal (operator's words):** *maximize precision and accuracy.* The path is: better features →
calibrate → meta-label → concentrate on proven cells → accept fewer, better bets.
**Decision rule:** anything that consumes *resolved outcomes* is **LATER** (DB has only ~6.3h,
23–100 leans/horizon). Anything that is **pure code / backfillable data** is **NOW**.

> Companion docs: `V2_CONTEXT.md` (discipline + proof gates), `V3_ACCURACY_PLAN.md` (full
> feature specs + train/serve-consistency guardrails). This file is the actionable cut.

---

## ✅ NOW — build this batch (none of it is data-gated)

All of this feeds the **next single retrain**. Build in this order; commit in small,
bisectable steps; keep the app FROZEN until the retrain.

### N1. Historical backfill + shared feature function (the keystone — do first)
- One-off pipeline: download ~30–60d BTCUSDT-perp **aggTrades** + **funding/premium-index**
  from `data.binance.vision` (free).
- Compute per-1m-bar: `cvd_change/1m/5m`, `delta`, `delta/CVD divergence`, `VPIN`,
  `funding_velocity` (premium-index velocity proxy).
- **CRITICAL — train/serve consistency:** the backfill MUST reproduce the EXACT live
  formulas, candle-aligned. Build the live computation and the backfill from **one shared
  function**. Specifically: live `cvd_1m/5m` are rolling-windows-ending-at-close
  (`order_flow.get_time_based_cvd`), NOT `delta.cumsum()`; VPIN uses the same
  `bucket_volume`/`rolling_buckets`; divergences from confirmed, shifted pivots (no
  look-ahead). Verify the live `order_flow.py` market (spot vs perp) matches the backfill market.

### N2. Class-A features (kline-derived, full history immediately)
- `twap_deviation` (rolling time-weighted mean).
- `exhaustion` (declining range+volume on a continued move → fade).
- real `volume_profile` POC / LVN / value-area position (replace VWAP-proxy feat 101 + stub 102).
- `time_to_funding` (cyclical sin/cos of the 8h funding clock; replace stub feat 104).

### N3. Dead-stub fixes (features that are currently literally 0.0 or crude proxies)
- feat 17 `funding_velocity` (= 0.0 today) → real Δfunding/Δt (premium-index proxy).
- feat 45 `liq_acceleration` (= 0.0 today) → 2nd diff of liquidation volume.
- feat 100 `cross_exchange_lead_lag` (crude `eth_ret−ret_1m`) → real Coinbase-vs-Binance lagged corr.
- **OI momentum** (the one item from the options/derivatives ask worth doing now): add
  OI momentum/divergence off the existing live OI feeds. (Free, transfers to BTC, backfillable.)

### N4. Plumbing + versioning
- Register all new live keys in `signal_history.KEYS` + `_snapshot()` (so depth-derived ones
  start accruing; trade-derived ones are covered by N1 backfill).
- Append new names to `features.py FEATURE_NAMES` **at the end** (indices 109+); never reorder
  0–108. This changes `NUM_FEATURES` + schema hash → bump `MODEL_ARCH_VERSION`.

### N5. Retrain once (overnight) + validate
- Runbook (16GB): close IDE + browser (frees ~4.6GB), High-Performance plan, `BTC_TRAIN_THREADS=12`,
  flip `BTC_FREEZE_MODEL=0` for the run, set back to `1` after.
- Validate: no NEUTRAL-collapse (GLOBAL fallback intact), real probUp/probDown, schema-hash
  change forced exactly one retrain, base-model directional winrate at 3–15m moves OFF ~30%.

### N0. Already-made fixes — just need a restart to take effect
(No model impact; roll in whenever you next start the app.)
- Price-to-beat = **direction only** (value-betting / fair-value removed).
- Price-to-beat **always shows a side** (`_bet_lean` two-way fallback) — frequent leans restored.
- Action-log query pushed `LIMIT` per-table; frontend keeps last-good on a failed poll.
- **Freeze is now total** — `apply_learning_feedback` gated behind `MODEL_FROZEN` (no more
  "Auto-learning: decreased smoothing" on a frozen model).

---

## ⏳ LATER — gated, with explicit promotion triggers

Each item is blocked until the DB crosses a threshold. Re-check after the NOW retrain + accrual.

| Item | Why it raises precision | Promotion trigger (per horizon) |
|---|---|---|
| **Calibration (isotonic, OOF)** | makes a "70%" call actually win 70% → trustworthy precision | **≥150 resolved UP/DOWN leans** (5m, 15m) |
| **Meta-labeling** | the core selectivity instrument — abstains on junk, lifts precision-on-taken-bets | **≥300 resolved leans** AND calibration in place |
| **Per-model prune / upweight** | drop/cut weight on models that don't add incremental edge | **≥200 resolved per model per horizon** (today: 17–269) |
| **Horizon × regime concentration** | bet only the cells with demonstrated edge | enough resolved per (horizon,regime) cell to be non-noise (~≥100) |
| **Depth-feature coverage** (OBI/absorption/walls/spoof) | these can't be backfilled — need live recording | **>40% candle coverage** on those keys |
| **Real-money proof gates (V2)** | — | **≥500 resolved *actionable*/horizon, 30–90 live days, PF>1.2, stable calibration** → until then PAPER ONLY |

---

## 🚫 DEFERRED / SKIP (with reasons, so we don't relitigate)

- **GEX, vanna, 0DTE IV** — SPX-native dealer-positioning signals. BTC options are
  Deribit-dominated and tiny vs perp; dealer hedging barely moves spot; 0DTE BTC is sparse;
  vanna is a multi-day signal. Not transferable at 1–15m, can't be backfilled free → would be
  dead in training for weeks. **Revisit only if** the backfillable features prove the model
  clears chance at 3–15m AND weeks of Deribit coverage exist.
- **Options IV / skew / PCR enrichment** — pipe already exists (`DeribitOptionsClient`,
  feat 78–81). Low priority, slow *context* only, live-coverage-gated (no free backfill).
- **V2 P5 canonical prediction object & P4.3 regime train/serve alignment** — large
  cross-cutting refactors; do AFTER a clean validated run so accuracy changes stay attributable.

---

## Guardrails (carry from V2)
1. One coherent change set → compile + smoke-test → validate → freeze → measure. Never stack
   unvalidated changes.
2. Bump `MODEL_ARCH_VERSION` on any feature/label/saved-model change.
3. Keep `signal_history.pkl` (accrued coverage). Use a throwaway `BTC_DB_PATH` for debug runs.
4. Precision ⇄ frequency is a hard tradeoff: chasing precision means betting fewer windows.
   Don't loosen the AVOID gate to get more signals — that lowers precision.

---

## One-line summary
**NOW:** features (Class-A) + the backfillable trade/funding suite + dead-stub fixes + OI
momentum → one retrain. **LATER (data-gated):** calibration → meta-labeling → per-model
pruning → regime concentration → proof gates. **SKIP:** GEX/vanna/0DTE. The NOW batch raises
the raw signal; the LATER batch converts it into high *precision* once data exists.
