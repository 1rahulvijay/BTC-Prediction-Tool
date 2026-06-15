# Complete Research Log: What Worked vs. What Failed (Phases 1-16)

This document provides a brutally honest breakdown of every major research phase in the BTC-Prediction-Tool project. It categorizes exactly what hypotheses we tested, what succeeded and became part of the core engine, and what failed and was scrapped.

---

## Phases 1-7: The Pure Directional Trap
**Goal:** Build a single Machine Learning model to predict if the market will go UP or DOWN in the next 5 minutes using OHLCV data.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Pure Directional ML Models** | **Nothing.** | **Failed completely.** The maximum Out-of-Sample AUC we could achieve was ~0.53. Directional guessing on 1m/5m crypto bars structurally resembles a coin flip. |
| **"The Spent Move" Hypothesis** | **Nothing.** | **Failed.** The idea was that if a market recently expanded, it wouldn't move again. Reality proved the opposite: volatility clusters. |
| **Adverse Excursion Prediction** | **Nothing.** | **Failed.** Attempting to predict the depth of a pullback *before* a breakout was mathematically indistinguishable from noise. |

---

## Phase 8: The Selectivity Breakthrough
**Goal:** Stop guessing direction. Instead, predict *when* a massive absolute volatility expansion (>75th percentile) will occur.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Predicting `P(Big_Move)`** | **Massive Success.** We achieved a `0.720 AUC`. The top 1% of ML signals yielded a **73% precision** for catching massive volatility spikes, a 2.0x lift over the baseline. | N/A |
| **Kinetic Flow Features** | **Worked.** Features like `realized_vol`, `intensity` (tick pacing), `range_compression`, and `liquidity_shock` proved to be robust predictors of volatility expansion. | N/A |

---

## Phase 9: The First Side-Selectors
**Goal:** Since we can now predict *when* a move will happen, how do we pick the direction?

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **VPIN Liquidity Trap** | **Worked.** We found that VPIN (Flow Toxicity) is highly contrarian during extreme volatility setups. If toxic buy flow peaks right before a breakout, the price drops 59.1% of the time (Liquidity Trap). | N/A |
| **Universal Side Selectors** | **Nothing.** | **Failed.** We discovered that simple directional edges do not hold globally. They are highly dependent on the trading session (US vs. Asia) and the volatility regime. |

---

## Phase 10: Path & Risk Gating
**Goal:** Filter out breakouts that are too choppy to trade, and filter out immediate fake-outs.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Tradability Model `P(Tradable)`** | **Worked.** Successfully predicted clean, efficient breakout vectors (AUC `0.739`) using `range_compression` and long-term realized volatility. | N/A |
| **Invalidation Model `P(Fail_Fast)`** | **Nothing.** | **Failed as a Hard Gate.** The model was too weak (`0.537` AUC). If we used it as a hard rejection gate, it blocked too many good trades. We downgraded it to a weak, soft risk-modifier. |

---

## Phase 11: System Composition & Anchors
**Goal:** Unify the models into a single score and test VWAP Anchor theory.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Composite Scoring** | **Worked.** Multiplying the probabilities (`P(Big_Move) * P(Tradable) * (1 - P(Invalidation))`) significantly smoothed the equity curve and isolated the highest-quality setups. | N/A |
| **Anchor Bounce Strategy** | **Nothing.** | **Failed.** The hypothesis that price reliably "bounces" off the 60m VWAP Anchor failed. Instead, during high Selectivity windows, price *slices straight through* the VWAP 61.7% of the time. |

---

## Phase 12: Multi-Venue Integration
**Goal:** Migrate from OHLCV derivations to true microstructure data.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Binance Historical Backfill** | **Worked.** We successfully built a pipeline to process massive daily `aggTrades` zip files into a lightweight `research_matrix_1m.parquet` without blowing up system memory. | N/A |
| **Cross-Venue Features** | **Worked.** We successfully engineered `cvd_divergence` (Spot vs Perp flow), `perp_spot_basis_bps`, and `funding_velocity`. | N/A |

---

## Phase 13: Cross-Venue Side Selection
**Goal:** Use the new cross-venue features to find a robust directional edge.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Premium Basis (`perp_spot_basis_bps`)** | **Worked (Conditionally).** The Premium provided the strongest directional edge, but it flips based on regime: <br>1. **In Chop:** Premium is a trap -> **Fade it (54.9% win rate).** <br>2. **In Breakouts:** Premium leads -> **Follow it (53.4% win rate).** | N/A |
| **CVD Divergence** | **Nothing.** | **Failed as a trend signal.** When one venue heavily out-aggresses the other, it typically marks absorption. It is only useful as a mean-reversion (fade) signal, not a breakout follower. |

---

## Phase 14: The XGBoost Failure & Hybrid Engine
**Goal:** Train a Machine Learning model to dynamically learn the conditional "If Breakout -> Follow, Else -> Fade" logic discovered in Phase 13.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **XGBoost Classifier** | **Nothing.** | **Catastrophic Failure.** The XGBoost model completely failed out-of-sample (AUC `~0.49`). The signal-to-noise ratio in 1m crypto bars is so microscopic that the greedy tree algorithms completely overfit to the statistical variance of the training folds rather than isolating the core microstructure logic. It lost heavily to the simple hard-coded heuristics. |
| **The Hybrid Engine Architecture** | **Worked.** This failure forced our final architectural pivot: <br>1. Use **Machine Learning** strictly for Volatility/Selectivity timing (where it excels). <br>2. Use a **Deterministic Rules Engine** for Side-Selection based on the Phase 13 findings. | N/A |

---

## Phase 15: The Hybrid Engine & Paper EV Scorecard
**Goal:** Translate the Phase 13 findings into a deterministic rules engine and evaluate the full strategy stack against a brutal 14 bps round-trip slippage constraint to calculate Net Expected Value (EV).

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Deterministic Side Engine** | **Worked.** By using hard-coded logic (`Fade Basis` in Chop, `Follow Basis` in Breakout), the Gross Expected Value for Tier-1 volatility setups finally turned positive (`+0.04 bps`), meaning the engine accurately isolates a structural market edge before costs. | N/A |
| **The 14 Bps Stress Test** | **Nothing.** | **Failed.** The Net EV remained strongly negative (`-13.96 bps`). The average absolute magnitude of a 5-minute volatility breakout is `~20.7 bps`. A 14 bps slippage cost consumes 67% of the entire move, making a 5-minute holding period mathematically unviable under heavy taker fees. |
| **Live Shadow Protocol** | **Worked.** Because the offline environment cannot reliably simulate Limit-Maker execution (0 bps fee) or dynamic holding periods, we built the `live_shadow_logger.py` to move the system entirely into a Live Forward-Testing environment to empirically calibrate the execution costs. | N/A |

---

## Phase 16: Execution & Horizon Redesign
**Goal:** Prove mathematically what execution costs and holding horizons are required for the strategy to survive, and build Maker-mode tracking into the Shadow Logger.

| What We Tried | What Worked | What Failed |
| :--- | :--- | :--- |
| **Cost Sensitivity Test** | **Worked.** Proved definitively that the break-even cost of the pure 5m strategy is **`0 bps`**. The gross edge is real (`+0.04 bps`), but taker fees negate it instantly. | N/A |
| **Holding Horizon EV (15m/30m/60m)** | **Nothing.** | **Failed.** Extending a fixed holding period to 60m drops the Net EV to `-25.32 bps`. MAE (adverse drift) grows faster than MFE (favorable drift) on low timeframes, meaning blindly holding momentum exposes the trade to mean-reversion traps. |
| **MFE / TP Exit Policy** | **Worked (Conceptually).** We proved that to survive a 14 bps spread, the take-profit *must* be `>= 35 bps`. | **Failed (Execution).** Because a 35 bps MFE only happens occasionally, rigid TP/SL rules still resulted in a deeply negative Net EV (`-12.21 bps`) due to frequent SL hits. |
| **Expected Move Cost Gate** | **Worked.** We trained a Ridge CV model to forecast expected 30m MFE and successfully gated out low-volatility traps. | N/A |
| **Maker-Mode Live Shadow** | **Worked.** Updated `live_shadow_logger.py` to support `Mode A (Taker/Taker)`, `Mode B (Maker/Taker)`, and `Mode C (Maker/Maker)` EV tracking. | N/A |
