# Phase 14: Composite Side-Selector Model (XGBoost)

## Experiment Goal
To build a machine learning model (XGBoost) capable of automatically learning the conditional, regime-dependent edge we found in Phase 13 (i.e. "If tradable breakout -> Follow Basis; Else if high vol -> Fade Basis"). We trained models using Regime Features (`rv_15m`, `range_15m`, etc.) and Directional Flow Features (`perp_spot_basis_bps`, `cvd_divergence`, `vpin`).

## What We Tried
1. **Global Training:** We first trained a `max_depth=4`, `n_estimators=200` XGBoost classifier on all 86,400 valid minutes.
2. **Conditional Training:** We filtered the dataset to train *exclusively* on the top 20% most volatile regimes, where the edge is theoretically concentrated.
3. **Heavy Regularization:** We severely restricted the tree capacity (`max_depth=2`, `n_estimators=50`, `learning_rate=0.02`) and dropped all noisy features except `basis`, `vpin`, and `cvd_divergence` to prevent overfitting.
4. **Validation Methodology:** We used a strict 5-fold `TimeSeriesSplit` to generate Out-Of-Sample (OOS) probabilities.

## What Worked & What Didn't
**The Brutal Reality: The XGBoost model failed to out-perform simple heuristics.**

Across all experiments, the Out-of-Sample (OOS) AUC hovered around **0.49 - 0.51**. 
When we isolated the model's predictions to the *Tradable Breakout Regime*, the accuracy was **~49.6%**, which is mathematically random and significantly *worse* than the **53.4%** accuracy achieved by isolating the single `perp_spot_basis_bps` feature in Phase 13.

### Why did this happen?
1. **Signal-to-Noise Ratio:** The crypto market at the 1m frequency predicting a 5m directional move is incredibly noisy. 
2. **Greedy Splitting vs. Robust Heuristics:** Tree models are greedy. They will split on micro-patterns in the training data (e.g., `rv_15m > 0.005` AND `vpin < 0.3`) which do not generalize out of sample. Conversely, a hard-coded heuristic ("If `P(Big_Move) > 95th pct` AND `basis` is extreme -> GO LONG") is far more robust because it anchors on core market-microstructure logic rather than statistical variance.

## Final Conclusion & Next Steps
**Do not use a black-box XGBoost model for side-selection.**

The research dictates a pivot in the architecture:
1. Keep the Machine Learning strictly for the **Selectivity Model (`P(Big_Move)`)**, as volatility timing is highly predictable via ML.
2. Replace the ML Side-Selector with a **Deterministic Heuristic Rules Engine** built on the findings from Phase 13:
   - *Rule 1 (The Breakout Follow):* If `P(Big_Move)` triggers AND `tradable_move_label` conditions are met -> Trade IN the direction of `perp_spot_basis_bps` and `vpin`.
   - *Rule 2 (The Chop Fade):* If `P(Big_Move)` is high but it is *not* a clean breakout context -> Trade AGAINST the extreme `perp_spot_basis_bps` and `cvd_divergence`.

This hybrid architecture (ML for Volatility Timing + Deterministic Microstructure Logic for Direction) represents the final, optimal form of the strategy.
