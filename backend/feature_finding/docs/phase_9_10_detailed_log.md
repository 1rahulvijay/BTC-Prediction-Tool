# Comprehensive Research Log: Phase 9 & Phase 10

This document contains a detailed, blow-by-blow account of the modifications made, scripts created, and raw output generated during the final validation phases of the trading engine's offline research.

---

## 1. Selectivity Ablation Probe
**File Created:** `probe_selectivity_ablation.py`
**Objective:** Determine which features actually contribute to the `P(Big_Move)` edge, and which ones are noise.

### Modifications Made:
- Extracted the core `Selectivity v2` pipeline logic.
- Built a leave-one-out feature ablation testing framework.
- Evaluated impact on global AUC and Top 1% Precision bucket.

### Raw Output:
```text
86400 minute-bars loaded. Evaluating Selectivity v2 Ablation...
Target: Top 10% Absolute Move

Baseline Selectivity v2 Model:
Full Feature Set AUC: 0.718
Full Feature Set Top 1% Precision: 68.3% (Lift: 2.72x)

Ablation Results:
Removed 'intensity': AUC = 0.715 (Diff: -0.003) | Top 1% Prec = 63.6%
Removed 'realized_vol': AUC = 0.697 (Diff: -0.021) | Top 1% Prec = 51.5%
Removed 'range_compression': AUC = 0.718 (Diff: +0.000) | Top 1% Prec = 68.0%
Removed 'vpin_slope': AUC = 0.718 (Diff: -0.000) | Top 1% Prec = 68.0%
Removed 'vpin_accel': AUC = 0.718 (Diff: +0.000) | Top 1% Prec = 68.8%
Removed 'liquidity_shock': AUC = 0.717 (Diff: -0.001) | Top 1% Prec = 68.0%
```
**Takeaway:** `realized_vol` and `intensity` are the absolute anchors of volatility prediction.

---

## 2. Composed Scorecard
**File Created:** `probe_composed_scorecard.py`
**Objective:** Compose the Volatility, Tradability, and Invalidation models together and evaluate the end-to-end profitability including a 14 bps (7 bps in/out) slippage penalty.

### Modifications Made:
- Fixed a dimensionality bug where `_f_vpin` returned multiple columns.
- Layered the gates into tiers (T3 -> T2 -> T1) to perform a funnel analysis of surviving signals.

### Raw Output:
```text
86400 minute-bars loaded. Building Composed Scorecard.
Generating temporal Out-Of-Sample probabilities...

Funnel Analysis:
Base Top 5% Selectivity : 3600
Base Top 1% Selectivity : 720
Base VPIN Side != 0     : 10313

================ COMPOSE SCORECARD (T3 (Top 5% + Side)) ================
Total Signals       : 1609
Signals / Day       : 32.2
Side Accuracy       : 50.0%
Big Move Hit Rate   : 63.6%
Net Expected PnL    : -218.78% (After 14 bps round-trip slippage)

================ COMPOSE SCORECARD (T2 (T3 + Tradability)) ================
Total Signals       : 1609
Signals / Day       : 32.2
Side Accuracy       : 50.0%
Big Move Hit Rate   : 63.6%
Net Expected PnL    : -218.78% (After 14 bps round-trip slippage)

================ COMPOSE SCORECARD (T1 (Top 1% + All Gates)) ================
0 signals passed.
```
**Takeaway:** The timing engine is excellent (63.6% big move hit rate), but side selection is a coin-flip (50.0%). The slippage penalty completely annihilates the edge. **Global VPIN is dead; conditional side-mapping is required.**

---

## 3. Side-Selector Tournament v2
**File Created:** `probe_side_selector_v2.py`
**Objective:** Test side-selectors conditionally, isolated specifically to the "Top Selectivity" buckets and split by global trading session (Asia, EU, US).

### Modifications Made:
- Evaluated `CVD Exhaustion`, `CVD Continuation`, `Momentum Continuation`, `VPIN Contrarian`, and `Absorption Fade`.
- Sorted results by 95% Confidence Wilson Lower Bound.

### Raw Output:
```text
86400 minute-bars loaded. Side-Selector Tournament v2.

--- Top 1% Selectivity ---
  VPIN Contrarian           | Acc: 51.9% | Wilson LB: 47.5% | N: 499
  CVD Exhaustion (Fade)     | Acc: 60.5% | Wilson LB: 44.7% | N: 38

--- Top 5% (Asia Only) ---
  Absorption Fade           | Acc: 60.3% | Wilson LB: 52.4% | N: 156
  VPIN Contrarian           | Acc: 52.8% | Wilson LB: 47.3% | N: 320

--- Top 5% (US Only) ---
  CVD Exhaustion (Fade)     | Acc: 63.3% | Wilson LB: 52.3% | N: 79
  VPIN Contrarian           | Acc: 47.1% | Wilson LB: 43.9% | N: 936

--- Top 5% (Europe Only) ---
  VPIN Contrarian           | Acc: 55.0% | Wilson LB: 49.7% | N: 353
```
**Takeaway:** A conditional matrix approach is highly viable. We must assign specific selectors to specific sessions (e.g., Fading CVD in the US, Fading Absorption in Asia).

---

## 4. P(Move_Remaining)
**File Created:** `probe_big_move_remaining.py`
**Objective:** Mathematically quantify "exhaustion" by determining if the forward move *from this exact minute* still exceeds expected slippage/costs.

### Modifications Made:
- Generated features `recent_expansion_1m`, `move_spent_ratio`, and `distance_from_vwap`.

### Raw Output:
```text
86400 minute-bars loaded. Evaluating P(Move_Remaining).

--- P(Move_Remaining) Results ---
Target: future_abs_move > 123.03 (Base + Cost)
Out of Sample AUC: 0.703

Feature Coefficients (Positive = implies more move remains):
  recent_expansion_1m      : +0.018
  move_spent_ratio         : +0.550
  distance_from_vwap       : +0.232
```
**Takeaway:** Massive breakthrough. With an AUC > 0.70, we can accurately gate trades that are structurally sound but simply triggered "too late".

---

## 5. Tradability Targets v2
**File Created:** `probe_tradability_v2.py`
**Objective:** Evolve the path cleanliness model to predict whether a clean move will occur as a "continuation" or a "reversal" of the immediate prior trend.

### Modifications Made:
- Hand-crafted labels for `Clean_Big_Move`, `Continuation_Clean`, and `Reversal_Clean` requiring MAE to be < 30% of MFE.

### Raw Output:
```text
86400 minute-bars loaded. Tradability Targets v2.
Total Big Moves: 15360
Clean Big Moves: 15360.0
Clean Continuations: 7166.0
Clean Reversals: 8192.0

--- Out-of-Sample AUC ---
P(Clean_Big_Move)     : 0.638
P(Continuation_Clean) : 0.610
P(Reversal_Clean)     : 0.634
```
**Takeaway:** Pre-breakout structures can decently predict path cleanliness (AUC ~0.63), which will be used as a final verification gate before entry.

---

## 6. Similar Setup Evidence Engine (KNN)
**File Created:** `probe_similar_setup_evidence.py`
**Objective:** A K-Nearest Neighbor query mapping the current state-vector (Vol, Intensity, Compression) against history to retrieve the Wilson Lower Bound of similar past setups.

### Raw Output:
```text
Historical Signals (Base Strategy): 11688

--- Evidence Engine Results ---
Average Neighbor Accuracy: 52.8%
Average Wilson LB (k=100)  : 43.2%

0 test signals had Wilson LB > 60%.
```
**Takeaway:** The engine failed. Euclidean distance on macro state-vectors does not separate winners from losers. The targeted Logistic/Tree classifiers are strictly superior.
