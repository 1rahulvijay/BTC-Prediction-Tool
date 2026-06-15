# Phase 8 Implementation Plan: The Production Composer Engine

The goal of this phase is to move away from pure selectivity and transition into a fully composed trading decision engine. We will systematically build out the 10 probes/models you outlined to construct the Tradability Gate, the Side-Selector Map, and the Invalidation Gate.

## Proposed Strategy & Scripts

I will build and execute the following 10 scripts in sequence. Because of the volume of tick-data processing, I will batch these into logical research runs.

### Batch 1: The Core Final Gates
1. **`backend/probes/probe_tradable_move.py`**
   * **Goal:** Build the `P(Tradable_Move)` model.
   * **Target:** $Future\_Abs\_Move > p75 \land MAE < 0.35 \cdot MFE \land Path\_Eff > 0.55 \land Move\_Remaining > Cost$
   * **Features:** p_big_move, path_efficiency, wick_ratio, move_spent_ratio, anchor_distance.

2. **`backend/probes/probe_invalidation_risk.py`**
   * **Goal:** Build the `P(Fail_Fast)` model.
   * **Target:** Fast anchor reclaim, side flip within 120s, or fast MAE crush.
   * **Features:** anchor_cross_count, vpin_exhaustion, wick_rejection, path_noise.

3. **`backend/probes/probe_side_selector_tournament.py`**
   * **Goal:** A massive conditional tournament testing Side-Selectors *only* during Selectivity Top 1%, 5%, and 10%.
   * **Candidates:** VPIN contrarian, Anchor reclaim, Perp/Spot lead, Breakout side, CVD exhaustion.
   * **Metrics:** Accuracy by tier, Regime/Session splits, Wilson lower bounds.

### Batch 2: Advanced Context Models
4. **`backend/probes/probe_regime_session_split.py`**
   * Cross-tabulate all our Selectivity Keepers across Asia/EU/US and VOLATILE/LOW_VOL/CHOP.
5. **`backend/probes/probe_anchor_reclaim.py`**
   * Specialized probe testing failed reclaims and distance-from-anchor mean reversion.
6. **`backend/probes/probe_markov_entropy.py`**
   * Test state-persistence and order-flow entropy explicitly as a *timing* (Tradability) gate.

### Batch 3: Liquidity & Options Dynamics
7. **`backend/probes/probe_perp_spot_lead_lag.py`**
   * Analyze CVD divergence and basis_slope to fade overreactive perpetual futures against spot.
8. **`backend/probes/probe_liquidation_cascade.py`**
   * Identify cascade continuation vs reversal after liquidation spikes.

### Batch 4: Training & Composition
9. **`backend/train_tradability_model.py`**
10. **`backend/train_invalidation_model.py`**
    * Train the logistic models and save the `.pkl` files to compose the final live gate.

## User Review Required
> [!IMPORTANT]
> This is a massive multi-script research undertaking. Before I begin writing `probe_tradable_move.py` and `probe_side_selector_tournament.py`, please confirm if this batching order aligns with your priorities.
> 
> Also, for the `P(Tradable_Move)` target: do you have a specific dollar value or ratio for the `fee_slippage_buffer` that I should hardcode into the target, or should I define it dynamically (e.g., as 150% of the rolling 60-period spread)?
