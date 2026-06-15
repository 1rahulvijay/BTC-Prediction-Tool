# Phase 13: Side-Selector Tournament (Crossvenue Divergence)

We executed the `probe_crossvenue_side_selector.py` against our new `research_matrix_1m.parquet` golden dataset, specifically targeting the cross-venue features (`cvd_divergence`, `perp_spot_basis_bps`, `funding_velocity`) and comparing them against the baseline VPIN.

## 1. Methodology
We evaluated each feature across three regimes:
1. **Global All-Regimes** (N=86,400)
2. **High Volatility / Selectivity Filter** (N=12,960)
3. **Tradable Breakout Regime** (N=6,220)

For each regime, we computed a Time-Series Logistic Regression AUC on predicting the `future_direction_5m`. We also tested an "Extreme Setup" strategy (taking only the top 5% and bottom 5% of feature values) and calculating the raw win-rate of either following the feature (DIRECT) or fading it (INVERSE).

## 2. Tournament Results

### Global Regime
* **`perp_spot_basis_bps`** showed the strongest raw signal at `0.519` AUC.
* **`cvd_spot`** and **`cvd_perp`** both indicated **INVERSE** dynamics (fading aggressive taker flow yields ~53.5% win rate), which aligns with market-maker absorption.

### High Volatility Regime (> 85th Percentile Volatility)
* **`perp_spot_basis_bps`** jumped to a **54.9% INVERSE** extreme-setup accuracy. In high-volatility environments, a high basis (perps trading excessively rich) is a strong mean-reversion (fade) signal.
* `vpin` showed a **53.5% DIRECT** accuracy.

### Tradable Breakout Regime (tradable_move_label == 1)
* **`perp_spot_basis_bps`** had the highest AUC at `0.534` but flipped to a **53.4% DIRECT** mapping. In clean breakouts, the premium leads the spot market in the direction of the breakout.
* **`vpin`** provided a **53.6% DIRECT** mapping.
* The CVD Divergence (`cvd_divergence`) remained largely **INVERSE** (~51.6%), showing that even in breakouts, excessive divergence (perps out-aggressing spot) often gets faded.

## 3. Key Takeaway & Architecture Implication

**Basis Context is Regime-Dependent:**
The most striking finding is the behavior of the `perp_spot_basis_bps`:
- During generic high volatility, a high premium implies a trap/overextension -> **FADE (Inverse)**.
- During a verified tradable breakout, a high premium leads the underlying -> **FOLLOW (Direct)**.

To fully exploit this side-selector, the model architecture must *condition* the directional prediction on the selectivity score. A standalone LogReg cannot capture this conditional flip, which explains why the global AUCs hover around 0.51.

**Next Steps:**
We need to compose the `tradability` logic *with* the `basis` and `vpin` side-selectors into a single composite decision tree or XGBoost model rather than isolating them, as the direction of the edge (fade vs follow) depends entirely on the breakout state.
