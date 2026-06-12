# V3 — Directional-Feature, Calibration & Meta-Labeling Plan

**Status:** PLANNED (not yet built). Author: Claude. Date: 2026-06-09.
**Goal:** Raise live directional accuracy on the 5m/15m Polymarket-mirror task from the
honest current ~58% toward a realistic ~60–62% ceiling, by (a) adding genuinely
directional microstructure features, (b) calibrating probabilities, and (c) gating with a
meta-model — **without destabilizing the working frozen build.**

> This file supersedes the ad-hoc "Lever 2/3/4" notes. It is the canonical spec for the
> next retrain. Read it top to bottom before writing any code.

---

## 0. The one thing to understand first (read this twice)

Most of the "new" features the operator listed **already exist** in `features.py`
(`FEATURE_NAMES`, 109 features). The real limiter is **not feature count** — it is
**per-bar coverage** of the live features in `signal_history.py`.

`build_features_from_klines()` pulls each live signal through its `series()` helper:

```python
def series(key, snapshot_val):
    arr = sh.get(key)
    if arr is not None and len(arr) == n:   # per-bar history present → learnable
        return np.asarray(arr, dtype=np.float64)
    return np.full(n, float(snapshot_val))   # else broadcast ONE value → zero variance → DEAD
```

A column with the same value in every training row has **zero variance**, so the tree
models can never split on it. Until `LiveSignalHistoryBuffer` has recorded a snapshot for
a candle, every order-flow / derivative / wall feature for that candle is the neutral
default. Current coverage is ~1.3%, so **~70 of the 109 features are effectively dead in
training right now.** Adding ten more order-flow features makes ten more dead columns
until coverage fills.

**Therefore there are TWO classes of feature work, and they have different timelines:**

| Class | Source | Usable in a retrain… | Examples |
|---|---|---|---|
| **A. Kline-derived** | OHLCV (full 30-day history) | **immediately** | TWAP, VWAP bands, volume-profile/TPO, price-vs-VWAP divergence, exhaustion-from-candles |
| **B. Order-flow-derived** | live streams → `signal_history` | **only after weeks of coverage** | real CVD-delta divergence, VPIN, absorption-as-recorded, true cross-venue lead/lag, funding velocity |

This splits the plan cleanly: Class A can sharpen the *next* retrain; Class B is plumbing
we install now so it is *ready* for a retrain 2–4 weeks out. **Do not retrain Class-B
features the day after adding them — they will be empty and the model will learn ~zero
weight on them.**

### 0.1 UPDATE — historical backfill collapses the wait for *trade-derived* Class-B features

The "Class B needs weeks of live coverage" rule has a major exception, confirmed by the
operator's data-sourcing research: **the trade-and-funding-derived features can be
backfilled from free historical data**, so they are usable in the *next* retrain, not weeks
out. Order-book *depth*-derived features still need live coverage.

| Class-B feature | Backfillable from history? | Source |
|---|---|---|
| CVD (`cvd_change/1m/5m`), delta | **Yes** | `data.binance.vision` futures **aggTrades** (BTCUSDT perp) — has price, qty, `is_buyer_maker` |
| Delta / CVD divergence | **Yes** | aggTrades → per-bar CVD vs price pivots |
| VPIN | **Yes** | aggTrades → equal-volume buckets |
| funding_velocity | **Yes** | Binance `GET /fapi/v1/fundingRate` (settled) + `premiumIndexKlines` (1m intraperiod proxy) |
| OBI / book imbalance, absorption, walls, spoof, queue dynamics | **No** (depth) | needs historical L2 depth (coarse on Binance Vision) → keep live-coverage path |

So the **revised plan**: build a one-off **historical backfill pipeline** that downloads
~30–60 days of BTCUSDT-perp aggTrades + funding from `data.binance.vision`, computes the
trade/funding features **per 1m bar**, and writes them into the training matrix — giving
the *next* retrain full history on CVD/delta/divergence/VPIN/funding-velocity. The
depth-derived features (OBI/absorption/walls) still accrue live and wait for the later
retrain.

> ⚠️ **The make-or-break risk: train/serve consistency.** The backfill MUST reproduce the
> EXACT formulas the live recorder uses, candle-aligned, or the model trains on one
> distribution and serves on another (silent live degradation). Concretely:
> - Live `cvd_1m`/`cvd_5m` are **rolling windows ending at the candle close** (`get_time_based_cvd(60/300)` in `order_flow.py`), **not** a per-bar `delta.cumsum()`. The backfill must compute the same rolling-window-at-close value, not the doc's generic `resample().sum()`.
> - VPIN must use the **same `bucket_volume` and `rolling_buckets`** live and historically.
> - Use the **premium-index velocity** as the minute-scale funding signal in BOTH paths (settled 8h funding is too sparse to forward-fill into 1m without faking velocity).
> - Backfill divergences from **confirmed, shifted pivots** (no look-ahead) to match a causal live computation.
> Build the live computation and the backfill from **one shared function** where possible.

---

## 1. Feature inventory — what exists vs. what's missing

### Already present (do NOT reinvent)
| Operator's ask | Lives at | Notes |
|---|---|---|
| VWAP | feat 6 `vwap_deviation` | cumulative VWAP deviation |
| DELTA / CVD | feat 7–9 `cvd_change/1m/5m` | signed taker flow already computed in `order_flow.py` |
| ORDER IMBALANCE | feat 10–13 `book_imbalance`, `obi_5/10/20` | multi-level depth imbalance |
| ABSORPTION | feat 69 `absorption_ratio` | computed in `OrderFlowAnalyzer.process_depth` |
| STOP RUNS | feat 66–67 `liquidity_sweep_bullish/bearish` | sweep detector w/ 60s TTL |
| BOOKMAP (walls) | feat 52–56, 61–64 | wall imbalance, distance, persistence, growth |

### Missing or stubbed (the real V3 work)
| Feature | Current state | Class | Action |
|---|---|---|---|
| **TWAP deviation** | absent | A | add `twap()` + `twap_deviation` feature |
| **Delta divergence** | absent | A (price) + B (CVD) | price-vs-CVD-slope divergence |
| **Exhaustion** | absent | A | declining range/volume on continued move |
| **VPIN** (flow toxicity) | absent | B | volume-bucketed order-flow imbalance |
| **funding_velocity** | feat 17 = `0.0` **dead stub** | B | wire real Δfunding/Δt |
| **liq_acceleration** | feat 45 = `0.0` **dead stub** | B | wire 2nd-difference of liq volume |
| **cross_exchange_lead_lag** | feat 100 = crude `eth_ret − ret_1m` proxy | B | real Coinbase/Binance lead-lag corr |
| **volume_profile (TPO/POC/LVN)** | feat 101 = VWAP proxy, 102 = `0.0` stub | A | real rolling volume-at-price POC/LVN/value-area |
| **time_to_funding** | feat 104 = `0.0` stub | A | minutes to next 8h funding stamp (cyclical) |

> Note: features 105–108 (`polymarket_*`) are intentional stubs — leave them.

---

## 2. Phased execution

### Phase 0 — Build now (code only, no retrain) — Class A + Class B plumbing
Add the feature computations and register the new live keys so recording starts **tonight**.
Split commits so a regression is easy to bisect.

1. **`features.py`**
   - Append new names to `FEATURE_NAMES` (this changes `NUM_FEATURES` and the schema hash —
     see §5). Group them at the **end** (indices 109+) so existing column indices never
     shift (saved models reference positions; keep 0–108 stable).
   - Implement Class-A computations inline in `build_features_from_klines` (all derive from
     `closes/highs/lows/volumes` already in scope, fully vectorized, per-bar):
     - `twap` (rolling simple-time-weighted mean) + `twap_deviation` = `(close − twap)/close`.
     - `exhaustion` = sign(return) × (declining true-range AND declining volume over k bars),
       a mean-reversion exhaustion flag.
     - real `volume_profile_poc_distance` / `_lvn_distance` / `_value_area_pos` from a rolling
       price-binned volume histogram (replace the VWAP proxy at 101 and the 0.0 stub at 102).
     - `time_to_funding` from candle timestamp modulo the 8h funding cycle (encode as
       `sin/cos` so it's cyclical, not a sawtooth).
   - Wire the Class-B values through `series()` exactly like the existing ones (they read
     from `signal_history` and fall back to snapshot until coverage fills):
     - `funding_velocity` (fix dead feat 17), `liq_acceleration` (fix dead feat 45),
       `vpin`, `cvd_delta_divergence`, real `cross_exchange_lead_lag`.

2. **`order_flow.py`** — add the Class-B raw signals to `get_summary()`:
   - `vpin`: bucket recent trades into equal-volume buckets, VPIN = mean(|buy−sell|/bucket).
   - keep existing `absorption_ratio` (already good).

3. **`signal_history.py`** — add the new Class-B keys to `KEYS` and `_snapshot()` so they
   start being recorded per candle (this is the unlock — without it they never accrue):
   `funding_velocity`, `liq_acceleration`, `vpin`, `cvd_delta_divergence`,
   `cross_exchange_lead_lag`, plus the raw venue prices needed for lead-lag if not already
   present. Add neutral defaults where 0.0 is wrong (ratios → 1.0).

4. **`server.py`** — compute funding_velocity / liq_acceleration / lead-lag from the live
   `data_state` deltas where the raw series live, and store them into `data_state` so
   `signal_history._snapshot()` can pick them up. (No change to the prediction hot path
   beyond cheap scalar math.)

**Phase 0 acceptance:** app boots; `coverage_report()` shows the new keys as `present` and
climbing; Class-A features show non-zero variance over the 30-day matrix immediately;
`npm run build` clean; model still FROZEN.

### Phase 1 — Accrue coverage (no code, just uptime)
Leave the app running **frozen** for **2–4 weeks** (IDE + browser closed on the 16GB box per
the RAM ledger). Watch `signal_history.coverage_report()` climb from ~1.3% toward >50% on
the live keys. The longer this runs, the more Class-B features become learnable.

**Milestone gate to proceed to Phase 3:** at least **>40% candle coverage** on the new
Class-B keys AND **≥150 resolved directional leans per horizon** (5m, 15m) in the
`price_to_beat` / `predictions_*` tables. Below that, calibration and meta-labeling overfit.

### Phase 2 — Calibration (Lever 3)
Wrap each per-horizon stacker output in **isotonic regression** fit on **out-of-fold**
predictions only (never in-sample — that just memorizes). Persist the calibrators next to
the models. At inference, map raw `P(UP)/P(DOWN)` through the calibrator before the
two-way normalization that drives `fair_value_cents`. This makes the Polymarket
"fair value" honest (a calibrated 58% actually wins 58%), which **directly improves
value-betting EV even if raw accuracy is flat.** Add a reliability-curve check to the
backtester.

### Phase 3 — Meta-labeling (Lever 4) + the retrain
- Train a **meta-model** (López de Prado style): the primary model says direction; a
  secondary binary model predicts P(the primary call is correct) from the same features +
  conviction + regime. Trade/serve only when meta-P clears a threshold. This raises
  *precision on taken calls* (the metric that matters for betting) by abstaining on
  low-quality setups — it does **not** raise raw accuracy, it raises **realized** accuracy.
- `meta_model.py` already exists — verify it's wired into the gate, or wire it.
- **Then** run ONE overnight retrain with: new features (now covered) + calibration +
  meta-model active. Bump `MODEL_ARCH_VERSION`.

---

## 3. New-feature specifications (precise)

- **TWAP deviation** (Class A): `twap[i] = mean(close[i-w+1..i])`, `w≈20`;
  feature = `clip((close − twap)/close × 100, −3, 3)/3`. Directional: price extended above
  its own time-average mean-reverts more often than not at 5–15m.
- **Exhaustion** (Class A): over last `k=5` bars, `range_decay = TR slope < 0` and
  `vol_decay = volume slope < 0` while `|cum_return| > 0` → emit `−sign(return)` (fade).
  Captures "move running out of fuel."
- **Volume profile** (Class A): rolling `N=240`-bar price histogram (price binned to
  `~$5`), POC = modal bin, LVN = lowest-volume bin nearest price, value-area = 70% volume
  band. Features: signed distance to POC, distance to nearest LVN, position within value
  area. This is the legitimate TPO/market-profile signal (the operator's "TPO" ask).
- **VPIN** (Class B): equal-volume buckets over recent trades; `VPIN = Σ|V_buy−V_sell| /
  Σ V_total`. High VPIN = toxic/informed flow = larger directional follow-through. Needs
  trade history → recorded via `signal_history`.
- **CVD-delta divergence** (Class B): sign mismatch between price slope and CVD slope over
  a short window (price up while CVD down = bearish divergence). Uses recorded `cvd_*`.
- **funding_velocity** (Class B, fixes dead feat 17): `Δfunding/Δt`. Rising funding into a
  move = crowded longs = reversal risk.
- **liq_acceleration** (Class B, fixes dead feat 45): 2nd difference of liquidation volume;
  cascades are directional.
- **cross_exchange_lead_lag** (Class B, fixes crude feat 100): short-window lagged
  correlation of Coinbase vs Binance returns; whichever leads signals the next tick.

---

## 4. Why this is "sharpen," not "10×" (honest ceiling)

- 1–15m BTC direction is near-efficient. Realistic ceiling is **~60–62%** at 5m, not 70%+.
- Calibration and meta-labeling **do not add raw edge** — they make the existing edge
  *usable*: calibrated probabilities fix value-betting EV, and meta-labeling converts a
  modest hit-rate into higher *precision on the bets you actually place*.
- The biggest single lever remains **coverage** (Phase 1). A feature the model can't see
  in training is worth zero regardless of how clever it is.
- Expected realistic outcome after the full cycle: live directional precision on *taken*
  leans ~60–62%, calibrated fair-value error materially lower, more abstentions (fewer but
  better bets). If anyone promises more, distrust it.

---

## 5. Versioning, schema & retrain runbook

- **Schema hash changes.** Adding to `FEATURE_NAMES` changes `calculate_schema_hash()` and
  `NUM_FEATURES`. The loader validates schema; a mismatch forces a retrain. **Append only,
  never reorder 0–108** (saved models index by position).
- **Bump `MODEL_ARCH_VERSION`** in `model.py` (e.g. `2026-XX-XX-evidence-v5-...`) so the
  old frozen model is not silently reused with the new schema.
- **16GB retrain runbook** (unchanged from the optimization ledger):
  1. Close IDE (Antigravity/VS Code) and the browser — they hold ~4.6GB.
  2. Confirm High Performance power plan (CPU boosts to ~3.9GHz).
  3. `BTC_TRAIN_THREADS=12`, `OMP/OPENBLAS/MKL_NUM_THREADS=12` (4 cores reserved for feeds).
  4. DuckDB capped at 512MB (already set in `_connect`).
  5. Flip `BTC_FREEZE_MODEL=0` (or `POST /api/relearn`), let it run overnight, then set
     it back to `1`.
  6. Verify post-train: no 100%-NEUTRAL collapse (the `_predict_from_regime` GLOBAL
     fallback must remain), real `probUp/probDown`, reliability curve sane.

---

## 6. Risks / guardrails

- **Don't break the frozen build.** All Phase-0 work is additive; keep the app runnable and
  FROZEN throughout. No change to the inference hot path beyond cheap scalar math.
- **Don't retrain Class-B features early** (they'll be empty → wasted slots, and a bloated
  schema you then have to live with).
- **Isotonic must be OOF-fit.** In-sample calibration is a silent overfit that looks great
  in backtests and fails live.
- **Meta-model must train on OOF primary outputs**, same discipline.
- **Coverage gate is mandatory** before Phase 2/3 (>40% Class-B coverage, ≥150 resolved
  leans/horizon). Skipping it = overfitting to a tiny live sample.
- Keep `signal_history.pkl` — it is the accrued coverage; losing it resets Phase 1.

---

## 7. Sequenced checklist

- [ ] Phase 0: add Class-A features (TWAP, exhaustion, volume-profile, time_to_funding) — usable next retrain
- [ ] Phase 0: add Class-B plumbing (VPIN, cvd divergence, funding_velocity, liq_acceleration, lead-lag) + register in `signal_history.KEYS`
- [ ] **Phase 0b (NEW): historical backfill pipeline** — download `data.binance.vision` BTCUSDT-perp aggTrades + funding/premium-index (~30–60d), compute CVD/delta/divergence/VPIN/funding-velocity **per 1m bar using the SAME functions the live recorder uses**, write into the training matrix. Makes these usable in the NEXT retrain (no weeks-of-coverage wait).
- [ ] Phase 0: `npm run build` + boot smoke test, app stays FROZEN
- [ ] Phase 1: run frozen to accrue **depth-derived** coverage (OBI/absorption/walls); trade/funding features no longer gate on this thanks to backfill
- [ ] Phase 2: isotonic calibration (OOF) + reliability curve in backtester
- [ ] Phase 3: wire/verify `meta_model.py` gate; bump `MODEL_ARCH_VERSION`; ONE overnight retrain
- [ ] Verify: no NEUTRAL collapse, calibrated fair value, precision-on-taken-leans up
