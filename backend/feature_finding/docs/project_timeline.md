# BTC Volatility & Selectivity Engine - Project History

This document chronicles the complete evolution of the project from a basic directional predictor to a multi-gate selective trading engine, and finally to a multi-venue research architecture.

---

## Phase 1-7: The Directional Dead-End
Initially, the project attempted to build a pure directional model ("Will the market go UP or DOWN in the next 5 minutes?"). 
* **Findings:** The baseline predictive accuracy for raw direction on 1m/5m horizons consistently maxed out around **~53% AUC**. Pure directional guessing structurally resembles a coin flip and leaves the system highly vulnerable to slippage, spread, and micro-whipsaws.
* **Dead Ends Logged:** 
  * "The Spent Move" Hypothesis: The idea that a market that recently expanded won't move again. (False: Volatility clusters).
  * Predicting Adverse Excursion before breakout: Proven mathematically indistinguishable from noise (AUC ~0.50).

## Phase 8: The Selectivity Breakthrough
We fundamentally shifted the primary research question from "Which way?" to "When?"
* **Concept:** Instead of guessing direction, we focused on predicting the **Top 1% of absolute volatility expansions** (`P(Big_Move)`).
* **Selectivity v2 Model:** Using `realized_vol`, `intensity` (tick pacing), `vpin` (flow toxicity), `range_compression`, and `liquidity_shock`, we achieved a massive **0.720 Out-of-Sample AUC**. 
* **Lift:** The top 1% of predictions yielded a 73% precision rate for catching massive volatility spikes, providing a 2.0x lift over baseline market conditions.

## Phase 9: Side Selection Context
Once the `P(Big_Move)` gate proved successful, we needed a way to pick a direction during these rare windows without relying on a globally weak 53% model.
* **VPIN Contrarian Trap:** We discovered that inside the Top 1% of volatility windows, the VPIN flow imbalance is highly contrarian (59.1% accuracy). If flow is extremely toxic right before a big move, the price generally moves against the toxic flow (a Liquidity Trap).
* **Session Conditioning:** We discovered universal side-selectors are weak. Side selection edge is session-dependent (e.g., US Session: CVD Exhaustion / Europe: VPIN Reversal / Asia: Trend Continuation).

## Phase 10: Path & Risk Gating
We realized that predicting a big move isn't enough if the path to get there is too choppy (`Tradability`) or if it fakeouts immediately (`Fail Fast`).
* **Tradability Model:** Built to predict `P(Tradable_Move)`. Validated with a 0.739 Mean Fold AUC. Strongest predictors: Range Compression followed by high 60m Realized Volatility.
* **Invalidation Model:** Built to predict `P(Fail_Fast)`. Validated with a 0.537 Mean Fold AUC (later reclassified as a Weak Risk Modifier).

## Phase 11: The Composed System & Conditional Strategies
We linked all the gates into a unified pipeline and probed specific failure mechanics.
* **Composition Proof:** Proved that moving from a raw VPIN signal to a full stack (Direction + Selectivity + Tradability + Fail Fast) reduced signals from 206/day to 6.7/day and dramatically improved the net expected PnL (even under heavy simulated slip).
* **Anchor Reclaim/Rejection:** Proved that inside High Selectivity windows, the price slices right through the 60m VWAP (61.7% break rate), rather than bouncing, unlocking a strong contrarian breakout setup.
* **Live Shadow Protocol Established:** We formalized that no model reaches `LIVE_ACTIVE` status without resolving 500+ trades in a live, forward-tested shadow pipeline.

---

## Phase 12: The Multi-Venue Flow Integration (Completed 2026-06-14)
The OHLCV-only structure hit its ceiling for side selection. We migrated to a **Multi-Venue Flow Architecture**.
* **Unified Pipeline:** We used Binance historical data to pull Spot and Perp `aggTrades` as well as funding rates.
* **Golden Dataset:** We built `research_matrix_1m.parquet`, fusing 60 days of OHLCV data with cross-venue microstructure features (`cvd_divergence`, `perp_spot_basis_bps`, `funding_velocity`).

## Phase 13: Side-Selection Tournaments (Completed 2026-06-14)
With the new cross-venue data, we hunted for directional edge.
* **The Premium Flip:** We discovered that `perp_spot_basis_bps` (the premium) is highly predictive but regime-dependent.
  * In High Volatility / Chop: Premium is a trap -> **Fade Basis (54.9% accuracy)**.
  * In Tradable Breakouts: Premium leads the move -> **Follow Basis (53.4% accuracy)**.
* **CVD Reversion:** Aggressive `cvd_divergence` (one venue aggressively out-trading another) typically marks absorption and generally triggers mean-reversion (fade).

## Phase 14: The Hybrid Engine Pivot (Completed 2026-06-14)
We attempted to train an XGBoost Classifier to dynamically map these side-selection rules.
* **The Failure:** The XGBoost model completely failed out-of-sample (AUC ~0.49). The greediness of tree-based models on incredibly noisy 1m crypto data caused the model to overfit variance, losing to simple, hard-coded heuristics.
* **The Architecture Decision:** The ultimate layout of the system is the **Hybrid Deterministic Engine**:
  1. Use Machine Learning strictly to identify `P(Big_Move)` and `P(Tradable)` states (where ML is proven to have massive edge).
  2. Use a deterministic Rules Engine for direction based on Phase 13 findings (i.e. strictly fade basis in chop, strictly follow basis in confirmed breakouts).
