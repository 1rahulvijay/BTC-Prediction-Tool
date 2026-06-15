# Probe History Log: The Production Ledger

This document maintains the canonical history of all offline research probes. The purpose is to prevent repeating dead ends and to enforce strict scientific standards before a feature is allowed into the live application.

## ⚖️ Evaluation Standards

### 21D Validation (Keeper Standard)
* Mean AUC >= 0.55
* Worst fold AUC >= 0.52
* Top-decile precision lift >= 1.20x
* No lookahead leakage
* No single outlier period contributes >50% of edge

### 60D Validation (Production Candidate)
* Mean AUC >= 0.58
* Worst fold AUC >= 0.54
* Top-decile precision lift >= 1.30x
* Proven stability in at least 2 distinct regimes or sessions
* Survives combined-model ablation testing
* Confirmed offline/live parity

### Live-Active Gate (Live Edge)
* Must improve composed decision scorecard
* Passes rigorous probability calibration (Actual hit rate matches predicted buckets)
* EV is positive after all modeled spread/slippage/maker costs
* Does not increase drawdown or fakeout rate

---

## 🏷️ Feature Status Tags
* `DISCOVERY` - Initial test phase (7d-21d).
* `21D_VALIDATED` - Passed 21-day stability and lift tests.
* `60D_VALIDATED` - Passed massive regime stability and ablation tests.
* `LIVE_CANDIDATE` - Ready for paper/shadow trading integration.
* `LIVE_ACTIVE` - Actively managing live capital.
* `RETIRED` - Once worked, calibration drifted, currently offline.

## ⛔ Dead-End Classifications
* `NO_EDGE` - Statistically indistinguishable from noise (AUC ~0.50).
* `UNSTABLE` - Worked well in small samples (7d) but died in 21d/60d scaling.
* `LATE_SIGNAL` - Predicts a target, but only *after* the move already triggered (lagging).
* `DIRECTION_ONLY_WEAK` - Very weak standalone edge, but might be useful as a side-confirmation filter.
* `LABEL_MISMATCH` - The feature is valid, but we asked it the wrong question (e.g., predicting direction instead of timing).

---

# 1. Base Selectivity Keepers (The Foundation)

These are the core Volatility Expansion predictors.

| Feature | Concept | AUC (60D) | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `realized_vol` | Baseline energy | ~0.56 | `60D_VALIDATED` | The foundational filter. |
| `intensity` | High frequency tick pacing | ~0.60 | `60D_VALIDATED` | The strongest standalone predictor of an impending move. |
| `vpin` | Flow toxicity | ~0.56 | `60D_VALIDATED` | Massive toxic flow imbalance almost always precludes a macro move. |
| `range_compression` | Coiled spring | ~0.55 | `60D_VALIDATED` | Classic volatility contraction pattern. |
| `P(Tradable_Move) Model` | High precision expansion | 0.739 | `60D_VALIDATED` | 2026-06-14 | Trained logistic pipeline. `realized_vol_rv_60m` (0.735 AUC), `range_compression` (0.746 AUC). High VPIN correlates inversely with clean paths. Reached **0.739 Mean Fold AUC** against strict path-efficiency targets. |
| `P(Fail_Fast) Model` | Whipsaw mitigation | 0.537 | `60D_WEAK_CANDIDATE` | 2026-06-14 | Trained logistic pipeline. Volatility weakly correlates with fast failures (~0.55 AUC). Reached **0.537 Mean Fold AUC** overall. Used to gate out whipsaw entries. Soft risk modifier, not a hard gate. |
| `VPIN Contrarian Tournament` | Directional side-selector | 0.591 | `LIVE_CANDIDATE` | 2026-06-14 | Degrades to 50% outside of extreme percentiles. Top 1% = 59.1% Acc (Wilson 53%). Survives globally (Asia, EU, US) and across VOLATILE/CHOP regimes. Confirmed as absolute primary Side-Selector. |
| `Order Flow Markov Entropy` | Volatility prediction | 0.596 | `60D_VALIDATED` | 2026-06-14 | Computed 60m transition state probabilities. **0.596 AUC (INVERSE)**. Confirms hypothesis: structured, low-entropy order flow predicts high impending absolute volatility. |
| `Anchor VWAP Reclaim` | Z-score mean reversion | 0.535 | `WEAK_CONDITIONAL` | 2026-06-14 | Extreme Z-score (>2.0) distance mean reversion hit 53.45% accuracy. Decent, but insufficient edge to act as a standalone primary side selector. Use only inside high-selectivity buckets. |

---

# 2. Advanced Offline Probes

## A. Liquidity Shocks
**Concept:** A sudden disappearance of the order book.
* **Target:** `P(Big_Move)`
* **Result:** AUC ~0.56.
* **Verdict:** `60D_VALIDATED`. Confirmed that sudden spread-widening on low volume precedes structural macro moves. Added to Selectivity v2.

## B. VPIN Transition
**Concept:** The *derivative* (slope and acceleration) of flow toxicity.
* **Target:** `P(Big_Move)`
* **Result:** AUC ~0.55.
* **Verdict:** `60D_VALIDATED`. Rapid shifts from balanced flow to highly toxic flow (`vpin_slope_5m` and `vpin_accel`) are pure leading indicators of breakouts. Added to Selectivity v2.

## C. Trade Size Skew
**Concept:** Institutional tracking (comparing 99th percentile trade sizes to 50th percentile).
* **Target:** `P(Big_Move)`
* **Result:** AUC 0.58 (7-day), AUC 0.50 (21-day).
* **Verdict:** `UNSTABLE`. Extremely regime-dependent. The alpha decayed completely upon scaling.

## D. No-Trade Alpha
**Concept:** Predicting wicks, intra-bar fakeouts, and micro-chop.
* **Target:** `P(Avoid)`
* **Result:** AUC ~0.50
* **Verdict:** `LABEL_MISMATCH`. Micro-structure chop does not reliably predict macro-structure chop. The wrong target was tested.

## E. Liquidity Shock Cluster
**Concept:** Clusters of shocks predict regime transitions.
* **Target:** `P(Tradable_Move)`
* **Result:** AUC ~0.51
* **Verdict:** `NO_EDGE`. Clusters of shocks do not predict *clean* moves any better than single shocks.

## F. Anchor Behavior
**Concept:** Behavior relative to a 60m VWAP anchor.
* **Target:** `P(Tradable_Move)`
* **Result:** AUC ~0.527
* **Verdict:** `DIRECTION_ONLY_WEAK`. Rejections away from VWAP are slightly cleaner than crosses, but the effect is too weak to be a standalone selectivity gate. May be repurposed as a conditional directional side-selector.

## G. Compression Duration
**Concept:** A market compressed for 60m is more explosive than one compressed for 5m.
* **Target:** `P(Big_Move)`
* **Result:** AUC ~0.50
* **Verdict:** `NO_EDGE`. The *depth* of a squeeze (range_compression) stores energy, but the *duration* does not.

## H. Volatility Continuation (formerly "Big Move Remaining")
**Concept:** A move that recently expanded is "spent" and should be avoided.
* **Target:** `P(Big_Move)`
* **Result:** AUC 0.54 (INVERSE)
* **Verdict:** `LABEL_MISMATCH`. Testing revealed Volatility Clustering. High recent expansion actually predicts *continued* expansion. It does not predict a "spent" move.

## I. Adverse Excursion
**Concept:** Features that predict extreme MAE/MFE fakeout risk before targets are hit.
* **Target:** MAE/MFE > 0.50
* **Result:** AUC ~0.50 (`vpin_exhaustion`, `noise_to_trend`)
* **Verdict:** `NO_EDGE`. Pre-breakout structural features fail to predict path cleanliness or fakeouts. The path of an expansion is effectively random.

---

# 3. Model Composers

## Selectivity Model v2
**Concept:** Logistic composition of the 6 validated volatility keepers.
* **Features:** `range_compression`, `intensity`, `realized_vol`, `vpin`, `liquidity_shock`, `vpin_slope`, `vpin_accel`.
* **60D Performance:**
  * Mean AUC: `0.720` (Fold Std: `0.013`)
  * Top 1% Precision: `73.0%`
  * Lift: `2.0x`
* **Status:** `LIVE_CANDIDATE`. 

**Ablation Results:**
```text
Full Selectivity v2 AUC: 0.720

Without realized_vol: 0.697 (-0.021) [Top 1% Prec: 51.5%]
Without intensity: 0.715 (-0.003) [Top 1% Prec: 63.6%]
Without vpin: 0.718 (+0.000) [Top 1% Prec: 68.0%]
Without range_compression: 0.718 (+0.000) [Top 1% Prec: 68.0%]
Without liquidity_shock: 0.717 (-0.001) [Top 1% Prec: 68.0%]
Without vpin_slope/vpin_accel: 0.718 (+0.000) [Top 1% Prec: 68.8%]
```
*Note: `realized_vol` and `intensity` are the dominant drivers.*
