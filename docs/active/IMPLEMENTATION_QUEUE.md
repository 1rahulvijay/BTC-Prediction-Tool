# Implementation Queue — every proposed change, training-or-not

**Created 2026-06-13.** Single source of truth for what's left to build. Operator constraint:
**no retraining right now.** So the actionable set = everything marked **NO TRAIN**. Items marked
**TRAIN** are parked until a deliberate retrain window.

## MASTER MATRIX — every roster idea (status · impact · train? · more-data?)

| Idea | What | Status | Impact | Train? | More data? |
|---|---|---|---|---|---|
| **B1** | live feature-vector log | ✅ built (restart) | enables A4 retrain | NO | IS the collector |
| **B2** | conviction-gate (50–54% → read-only) | ✅ built (restart) | precision NOW | NO | NO |
| **A1 recorder** | persistence snapshots | ✅ built (restart) | feeds A1 model | NO | IS the collector |
| per-model metric fix | committed-vote accuracy | ✅ built (restart) | honest panel | NO | NO |
| Binance·PtB tab | Binance-anchored mirror | ✅ built | visibility | NO | NO |
| **R1** Kronos remove | drop dead forecaster | ✅ v6 | cleanliness | (done) | NO |
| **R2** SGD remove | drop anti-signal | ✅ v6 | small acc GAIN+speed | (done) | NO |
| **R3** FSR-PPO mothball | stop challenger | ✅ v6 | speed | (done) | NO |
| **A6** TCN full stacker seat | OOF refits + GPU | ✅ v6 | diversity | (done) | NO |
| **F1** GPU train | lgb/xgb/cat CUDA | ✅ v6 | −1.5h → daily fresh | (done) | NO |
| **A4 / Track C** | OBI depth, cross-venue lead-lag, funding×mom, L2 | ❌ | **HIGH — ceiling lift** | YES | YES (B1/backfill) |
| **A8** | session/time features | ✅ **built (v7)** | MED (cheap; time-blind) | YES | NO |
| **C7** | `variance_ratio` | ✅ **built (v7)** | MED (cheap; trend/chop) | YES | NO |
| **C8** | `rv_term_structure` | ✅ **built (v7)** | MED (cheap; vol term) | YES | NO |
| **A9** | Polymarket crowd price feature | ⛔ blocked (no 5m feed) | MED-HIGH (unique) | YES | YES (need feed) |
| **A1 model** | persistence/hold classifier (T3) | ✅ **trained + WIRED** (restart) | **HIGH — 95% tier** | (own head) | YES (accruing) |
| **A1-ext** | learned path labels | ❌ | MED-HIGH | YES | YES |
| **A10** | setup fingerprints / similar-setup | ❌ | MED (evidence) | NO* | YES |
| **A7** | Optuna hyperparam search | ❌ | **HIGH — biggest tune lever** | YES (offline) | NO |
| ATR labels | triple-barrier targets | ❌ | MED (cleaner target) | YES | NO |
| **A5** | focal loss | ❌ (contingency) | LOW-MED | YES | NO |
| **R4** | drop in-loop CalibratedCV | ❌ (gated) | speed | A/B | NO |
| **R5** | drop histgb (4th clone) | ❌ (gated) | speed | A/B | NO |
| **A2** | fair value / p_up | ⛔ removed→deferred | betting (not pred.) | YES (needs A1) | — |
| A11/A12/A13/A14 | penny-sniper / pair-arb / exits / Chainlink | ❌ deferred | betting layer | — | varies |
| **T3 gate** | Wilson-LB ≥80%, n≥100 | ❌ (needs A1+A10) | the 95% tier gate | NO | YES |

\*A10 is a shrinkage-stats evidence layer, not a trained model — "more data" is what makes it work.
**Highest-EV retrain bundle: A7 + A4 + A1** (tune what's there · add new info · carve the precise subset).

---

Legend: **NO TRAIN** = serving/display/logging/infra only, safe alongside the frozen v6 measurement.
**TRAIN** = changes the feature schema or a model → needs a retrain to take effect.

---

## A. DONE this session — live after the restart (all NO TRAIN)

| # | Change | File | Effect |
|---|---|---|---|
| A1 | Per-model accuracy neutral-poisoning fix (grade committed votes only) | `model_verifier.py` | Per-model panel now reads ~40–55% (real), not ~5% |
| A2 | Price-to-beat docstrings corrected (Chainlink/Binance → Pyth) | `price_to_beat.py` | Docs match the real bet-settlement feed |
| A3 | Offside/flipped Polymarket card header (§5az-2) | `main.js` | Header matches live reality, not the stale open |
| A4 | Sign-truth grading across calibration/analytics/regime-quality/auto-learning (earlier) | several | Honest accuracy everywhere; gate blocks the right cells |
| A5 | Pyth anchor + dual Binance/Polymarket views (earlier) | `server.py`, `main.js` | Price-to-beat matches Polymarket within a few $ |

*(A1/A2 activated on the restart you just did; A3–A5 were already live.)*

## A-v7. BUILT & STAGED FOR RETRAIN — active after the next restart & retrain (v7 schema)

| # | Change / Feature | File | Effect / Target |
|---|---|---|---|
| C1 | `variance_ratio` (slot 130) | `features.py` | Trend-vs-chop regime detector (Lo-MacKinlay variance ratio) |
| C2 | `rv_term_structure` (slot 131) | `features.py` | Short-vs-long realized volatility term structure |
| A8 | Session flags & weekend (slots 132-135) | `features.py` | UTC Session (Asia/EU/US) and weekend flags (volume/liquidity proxies) |
| A1 | P(hold) serving & ⚡ late-entry gate | `price_to_beat.py`, `main.js` | Uses persistence model to gate ⚡ entries at calibrated P(hold) >= 93% |

---

## B. PROPOSED — NO TRAIN (can implement now, no retrain)

### B1. Live feature+outcome logging  ← **the one that matters most; start the clock**
- **What:** new `feature_outcome_log` store (DuckDB table) + a capture hook at prediction emit and a
  write hook at verifier resolution. Records the full `NUM_FEATURES` vector + `schema_hash` + realized
  move + sign-truth label per prediction.
- **Why:** the model is stuck at ~0.51 because the high-edge microstructure features are **constant in
  training** (`server.py:1160` broadcasts one live snapshot over 50d of history). Logging live rows is
  the ONLY way a future retrain can learn them. See [SPEC_ACCURACY_NEXT_RETRAIN.md](SPEC_ACCURACY_NEXT_RETRAIN.md) Track B1.
- **Training?** **NO** — it only records. (The eventual retrain *on* this data is TRAIN, weeks out.)
- **Immediate accuracy gain?** None — it's infrastructure. Its value is calendar-gated: every day not
  logging delays the milestone retrain. **Build now precisely because it pays off later.**
- **Risk:** touches the serving loop → all hooks try/except-wrapped (a logging failure can't crash
  serving); proven on a throwaway DB first; activates on the next restart.
- **Status:** ✅ **IMPLEMENTED & VERIFIED (2026-06-13)** — `feature_outcome_log` table +
  `log_feature_vector` helper + record-loop hook. Activates on the next restart (no retrain). See
  change-audit §5bb. Confirm post-restart: `SELECT COUNT(*) FROM feature_outcome_log` climbs.

### B2. Track A — conviction-gate tightening (selectivity)
- **What:** reserve "actionable/high-conviction" for regime×horizon cells with *proven* edge
  (≥~54%); coin-flip cells (50–54%, e.g. 5m LOW_VOL 51.7%) show a read but never a confident bet.
  Options A (informational-only) / B (full silence) — see [MEASUREMENT_WINDOW_2026-06-13.md](MEASUREMENT_WINDOW_2026-06-13.md) §5.
- **Why:** the only lever that raises **effective precision today** — commit just the cells that win
  (3m/10m LOW_VOL ~54–56%), stay quiet elsewhere. Directly serves "precision in prediction."
- **Training?** **NO** — pure serving-side gate logic.
- **Status:** ✅ **IMPLEMENTED & VERIFIED (2026-06-13)** — Option A in `apply_live_quality_filters`:
  cells measured 50–54% (READY) keep the directional read but lose `actionable` (`convictionCapped`).
  Activates on the next restart (no retrain). See change-audit §5bb.

### B3. Partial-candle staleness — investigate (maybe a NO-TRAIN serving fix)
- **What:** 5m sign-acc by second-of-minute shows fresh-bar 62.5% vs late-bar (45–59s) 36% (small n).
  If late-bar predictions use a stale partial candle, that's a serving-side data-freshness fix.
- **Training?** **NO** if it's a freshness/plumbing bug. Investigate first; n is still small.
- **Status:** watch; revisit when the sample grows.

### B4. WS reconnect robustness (operational, not code)
- **What:** the `WinError 121` micro-drops are cosmetic (self-heal in 2–5s). A wired Ethernet
  connection reduces them. No code or training.
- **Status:** optional ops improvement.

---

## C. PROPOSED — TRAIN (parked until a deliberate retrain window)

### C1. Track C — multi-venue backfillable FLOW features (deferred)
- Coinbase/Bybit CVD divergence, cross-venue lead-lag, multi-venue aggressive-buy ratio, large-print
  venue skew. Deferred to the A4 cross-venue flow bundle to ensure train/serve parity is 100% resolved first.
- **Training?** **YES** — new feature columns → retrain.

### C2. `rv_term_structure` (slot 131)
- ✅ **Built in v7** (slot 131) — short-vs-long realized-vol ratio (`rv5 / rv15`). Kline-derived → free full history.

### C2-ext. `variance_ratio` (slot 130)
- ✅ **Built in v7** (slot 130) — Lo-MacKinlay trend-vs-chop indicator (`VR - 1`). Kline-derived → free full history.

### C2-session. Session flags & weekend (slots 132-135)
- ✅ **Built in v7** (slots 132-135) — UTC trading-session flags (Asia/EU/US) and weekend indicator. Timestamp-derived.

### C3. Magnitude (#2) — conditional-quantile regressor
- Replaces the flat ~$40 mean with a vol-aware q10/q50/q90 (pinball loss). Sharpens "how far".
- **Training?** **YES.** Gated behind direction precision being real (V5 #2).

### C4. Path (#3) — path classifier
- DOWN_DIRECT / UP_THEN_DOWN / DOWN_THEN_UP / CHOP intra-window shape labels. Sharpens "how it
  travels". Richest, built last.
- **Training?** **YES.** Gated behind #1 and #2 (V5 #3).

### C5. Fair value re-anchored to A1 (betting layer)
- Re-anchor `p_up`/fair value to the persistence model (A1), behind a 3-gate (A1-sourced +
  calibrated + EV-positive). Deferred — betting, not prediction.
- **Training?** **YES** (needs A1). Deferred until 5m committed-lean clears ~56–60%.

### C6. The milestone retrain — on B1's live-logged data
- When B1 has ≥3–4 weeks of multi-regime rows, retrain where microstructure finally varies. The one
  most likely to break 0.51.
- **Training?** **YES** — this is THE retrain B1 exists to enable.

---

## D. Recommended order given "no training now"

1. **B1 logging** — build now (NO TRAIN). Starts the calendar clock toward C6. *Highest priority.*
2. **B3** — quick investigation (may be a free NO-TRAIN serving fix).
3. **B2** — revisit after the 24h measurement (NO TRAIN, immediate effective-precision lever).
4. Everything in **C** — bundle for the next deliberate retrain window (C1+C2 first as the cheapest
   new-edge retrain; C3/C4/C5/C6 by their gates).

**Bottom line:** the only change that *improves the model* requires training (Track C / the C6
retrain). With training off, the single most valuable thing to do now is **B1 logging** — it doesn't
help today, but it's the prerequisite that makes the eventual ceiling-breaking retrain possible, and
its value is lost for every day it isn't running.
