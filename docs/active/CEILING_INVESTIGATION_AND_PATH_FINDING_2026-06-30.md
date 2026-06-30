# Direction-Ceiling Investigation, Bug Fixes & the Path-Prediction Finding (2026-06-29/30)

**Scope.** A multi-day push to (a) fix real production bugs, (b) settle once and for all whether BTC
5m/15m **direction** can be predicted, and (c) find what *can* be predicted. Everything below is
measured (walk-forward, out-of-sample, shuffle-validated), not asserted.

---

## 0. TL;DR

- **Direction (up/down) is an information-capped coin-flip (~0.52 AUC).** Proven now ~15 ways including
  a 13-model bakeoff, meta-labeling, fractional-diff, the volatility-estimator literature, and
  perpetual-**futures** flow/basis. It is **not** a model/feature/paper gap — the signal is not in any
  data reachable from this box (spot OR futures).
- **What IS predictable: the path's MAGNITUDE.** How far price travels, the high/low, and whether it
  **touches a barrier** (±$50/$100) is predictable at **AUC 0.65–0.83** (walk-forward). Volatility,
  not direction.
- **The product's edges are unchanged and confirmed:** P(hold)/late-entry, big_move/selectivity,
  volume. The only *new* path to a tradeable edge is **Polymarket mispricing** (relative edge; recorder
  fixed, accumulating).
- **Bugs fixed:** microstructure parity (CVD/VPIN), the Polymarket recorder (never captured snapshots),
  the advice/P(hold) coherence bug, the 100% display, and analytics sign-truth.

---

## 1. Bugs found & fixed

| # | Bug | File(s) | Fix |
|---|---|---|---|
| 1 | **Microstructure features dead-zero live** (cvd/large-trade): `get_aligned_series` 0-default masked the live snapshot; model trained on real values, served zeros for days | `server.py`, `features.py` | drop masked keys from `live_sig_hist` so `series()` broadcasts the live value |
| 2 | **VPIN serve-path** hardcoded `series("vpin", 0.0)` + slow warmup (750 BTC) | `features.py:1146`, `server.py` | pass `of.get("vpin")`; documented the ~1h warmup as cold-start, not a bug |
| 3 | **Polymarket recorder never captured snapshots for 13.5 days** — `discover_rounds` trusted Gamma's future-biased list; the live round (`0<=elapsed<=dur`) was never discovered | `live_btc_updown_recorder.py` | compute the live slug deterministically (`anchor=now//dur*dur`) + fetch via new `GAMMA_SLUG` |
| 4 | **Advice contradicted P(hold)**: card showed P(hold UP)=99% yet advised "reversal possible, hold" a losing DOWN bet | `price_to_beat.py:_advice` | thread `p_hold`; when cross-back ≤15%, say "likely lost", not "hold" |
| 5 | **P(hold)=100% display** (reads broken; clashes w/ HIGH big-drop) | `price_to_beat.py:893`, `main.js` | cap display at 99% (raw value untouched — all gates still use it) |
| 6 | **analytics.py used the dual-semantic `hit`** for "accuracy" | `analytics.py` (7 sites) | switch to sign-truth `lean_hit` (NEUTRAL excluded); skipped 2 sites correctly (diff table / neutral buckets) |

Lean-arrow colors restored per operator preference; full codebase scan (153 files) clean.

---

## 2. The direction-ceiling investigation — every approach tested

All on `data/research/binance_updown_rounds.parquet` (25,919 5m + 8,639 15m clean OHLCV bars),
leak-free temporal walk-forward, shuffle-null, cost-aware (Polymarket spread ⇒ AUC≥0.55 to be tradeable).

| Approach | Tool | Result |
|---|---|---|
| 50+ classic-TA "predict everything" matrix | `probe_ta_matrix.py` | direction 0.525 (sub-cost); price/levels/MA **un-learnable** (worse than persistence); volume +0.25; big_move 0.60 |
| **13-model bakeoff** (xgb/lgb/cat/histgbm/lr + RF/ET/GB/KNN/NB/MLP-sklearn/MLP-torch) | `probe_model_bakeoff.py` | **every** model 0.50–0.535 on direction; 0.56–0.62 on big_move. Not the model. |
| **Meta-labeling** (López de Prado) | `probe_model_bakeoff.py` | top-confidence subset stays 0.47–0.49 — selection can't manufacture direction |
| **Fractional differentiation** | `probe_ta_matrix.py --fracdiff` | no direction lift (fixes non-stationary levels only) |
| **Volatility-estimator literature** (Yang-Zhang, Garman-Klass, Rogers-Satchell, semivariance, bipower-jump, HAR) | `probe_vol_features.py` | **redundant** — +0.01 on big_move (within noise); vol-only weaker than baseline ("one signal, five ways") |
| **Perp-FUTURES flow + basis** (new information!) | `probe_futures_flow.py` | futures-only 0.50–0.51; ohlcv+futures = **no lift** over spot. The geo-blocked feed would not help direction. |
| Conditional pockets (hour/regime/streak/prior-move), OOS | `probe_conditional_direction.py` | every pocket collapses OOS; only 15m `bigPriorDN` reversion survives weakly (52.9%, sub-cost) |

**Cross-asset (ETH→BTC):** literature is explicit — BTC *leads*, alts lag; no usable reverse lead-lag.
**Peer-reviewed crypto-ML literature** independently agrees: short-horizon ML is "marginally better than
random" and "negative returns after transaction costs."

**Conclusion: the ceiling is informational, confirmed against every dataset reachable here.**

### 2b. Strategies on their NATIVE objective (`probe_strategy_bench.py`)

Testing strategy *families* on what they were published to do (not forced onto 5m direction):
- **Momentum/reversion (Moskowitz/Lo-MacKinlay):** coin-flip at 5m/30m/1h/2h; statistically-significant
  but **sub-cost** mean-reversion at 15m (z=−4.6, ~52.5%); mild **trending only at 4h** (Hurst 0.57,
  VR 1.10, n=539 — weak, off-product). No usable direction edge at any product horizon.
- **Hurst/Variance-Ratio:** H≈0.46–0.51, VR<1 at short horizons = efficient random walk.
- **Seasonality (USABLE):** volatility **1.9× higher** at peak hour (14 UTC) vs trough (06 UTC) and 1.9×
  across days; **no hour is directionally biased** (max z=2.1). Real vol seasonality, zero direction
  seasonality.
- **Vol clustering (ARCH):** `autocorr(|ret|)`=+0.20–0.26 vs `autocorr(ret)`≈−0.03 — predictable vol,
  coin-flip direction, the thesis in one line.

**App-fit:** the only strategies that pass their own native test are volatility/timing/seasonality, and
they plug into the existing big_move / touch / P(hold) heads (e.g. time-of-day vol scaling), never a
direction model.

---

## 3. The constructive finding — PATH / trendline prediction

Reframe from "up/down at expiry" to "what's the intra-window **path**" (`probe_path_prediction.py`):

| Path target (5m / 15m) | Metric | Family |
|---|---|---|
| max_up_bps (high) | skill **+0.44 / +0.42** | volatility — **predictable** |
| max_down_bps (low) | skill **+0.42 / +0.41** | volatility — **predictable** |
| range (travel) | skill **+0.31 / +0.30** | volatility — **predictable** |
| touch +$50 | AUC **0.70 / 0.65** | volatility — **predictable** |
| touch +$100 | AUC **0.75 / 0.67** | volatility — **predictable** |
| touch ±$50 (moves at all) | AUC **0.81 / 0.83** | volatility — **predictable** |
| up-excursion > down (which way) | AUC **0.53 / 0.51** | direction — **coin-flip** |

**You can predict HOW FAR and WHETHER it touches a barrier, not WHICH WAY.** Usable as: touch/no-touch
& straddle-style "will it move enough" structure, and as a **P(hold) input** (a high predicted excursion
means a current lead is about to be tested → lower effective P(hold) / smaller stake).

---

## 3b. The trade-plan signal + early-exit (`probe_trade_plan.py`)

Polymarket allows **mid-window exit**, so the dead close-direction need not be held. The trade plan
emits ONE stable signal per window (computed at the open, not a per-second probability):

- **Path-target bakeoff** (touch_either): HistGBM/XGB/LGBM/CatBoost all tie ~**0.808** AUC (CatBoost best
  0.809) — model-invariant again; CatBoost chosen.
- **Backtest (CatBoost, temporal 80/20) — calibrated:**
  - `touch ±$50 (moves at all)`: AUC **0.882**, calib 0.26→0.53→**0.92** across predicted bins.
  - `touch +$50` AUC 0.726 (0.21→0.50→0.75), `touch +$100` AUC 0.766, `touch -$50` AUC 0.739 — all calibrated.
  - predicted HIGH/LOW: skill **+0.46/+0.43**, MAE ~7bps; predicted level reached ~0.54–0.56 of windows.
  - `direction`: AUC **0.528**, flat calibration — honestly weak.
- **Time-of-day A/B:** adding hour/dow to touch = **+0.002 AUC = REDUNDANT** (realized-vol already encodes
  the 1.91x seasonality). Do NOT wire explicit time-of-day scaling.

**Early-exit mechanics (honest):** the plan predicts *whether/how-far* price moves (sizing + exit targets),
not *which side spikes first* (coin-flip). So it does not bypass the ceiling — it makes the **mispricing**
edge harvestable: enter on mispricing → set exit target from predicted high/low → take profit mid-window →
size by P(moves) → bound risk with predicted low.

## 4. The probe toolkit (permanent, read-only, each with `--selftest`)

- `probe_ta_matrix.py` — hardened ceiling-monitor (walk-forward + shuffle-null + cost-aware; `--fracdiff`)
- `probe_model_bakeoff.py` — 13 model families + meta-labeling + `--tcn` sequence model
- `probe_vol_features.py` — vol-estimator A/B (Yang-Zhang/GK/RS/semivariance/jump/HAR)
- `probe_futures_flow.py` — perp-futures flow/basis new-information direction test
- `probe_path_prediction.py` — intra-window path / touch / trendline prediction
- `probe_conditional_direction.py` — OOS conditional-direction pockets

---

## 5. Quant techniques researched (the legitimate frontier)

Range-vol estimators (Parkinson/GK/RS/Yang-Zhang), HAR-RV/HAR-RS-J, realized semivariance, bipower
jumps, Amihud/Roll/Corwin-Schultz/Kyle liquidity, CUSUM/SADF structural breaks, HMM/UMAP regimes,
Hurst/variance-ratio, entropy/Lempel-Ziv, fractional differentiation, triple-barrier + meta-labeling,
bet sizing. **Every direction-oriented one hits the ceiling; the vol/selectivity/sizing ones target the
edges already in the product.** Bet sizing (size ∝ edge) is the directly-usable one for Polymarket.

---

## 6. The forward path

1. **Polymarket mispricing** — the only new tradeable edge; `analyze_pm_recorder.py` once the (now-fixed)
   recorder has a day+ of rounds. Edge = P(hold) − recorded ask − buffer.
2. **Path/excursion → P(hold) sizing** — feed predicted next-window range into stake sizing / abstention.
3. **GEX/dealer-gamma** — the only untested new-information source (no data yet); long-shot for direction.

**Do NOT** run more direction models/features/papers on spot or futures OHLCV — it is settled.
