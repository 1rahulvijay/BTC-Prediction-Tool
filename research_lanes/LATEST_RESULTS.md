# Standalone Alpha Laboratory - Complete Campaign

Run: `20260813T060341Z`  
Git: `582a343acf04e7c1d47e8ee168259780920de987` (dirty)  
Authority: `RESEARCH_ONLY`; no serving, paper, or live strategy was modified

## Executive Verdict

**No tested strategy earned promotion.** The campaign found diagnostics and state information, but no robust executable alpha with a positive lower confidence bound after declared costs.

Accuracy or AUC alone is not treated as profit. A test is promotable only when its chronological out-of-sample net-EV lower bound is positive at executable prices and the minimum independent-day/round gates pass.

## Execution

| stage | result | seconds | log |
|---|---:|---:|---|
| phase5_42 | FAIL | 108.6 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5_42.log` |
| phase5b_46 | PASS | 131.0 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5b_46.log` |
| phase5c_brier_decomposition_market_vs_model | PASS | 0.7 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_brier_decomposition_market_vs_model.log` |
| phase5c_effect_size_to_cost_ratio | PASS | 0.3 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_effect_size_to_cost_ratio.log` |
| phase5c_effective_independent_sample_size | PASS | 17.0 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_effective_independent_sample_size.log` |
| phase5c_jump_vs_diffusion_decomposition | PASS | 1.1 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_jump_vs_diffusion_decomposition.log` |
| phase5c_last_crossing_timing_distribution | PASS | 0.7 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_last_crossing_timing_distribution.log` |
| phase5c_mfe_mae_joint_distribution | PASS | 1.6 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_mfe_mae_joint_distribution.log` |
| phase5c_near_settlement_terminal_margin | PASS | 0.7 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_near_settlement_terminal_margin.log` |
| phase5c_probability_monotonicity | PASS | 0.8 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_probability_monotonicity.log` |
| phase5c_volatility_clustering_half_life | PASS | 0.9 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/phase5c_volatility_clustering_half_life.log` |
| binance_cost_clearance | PASS | 25.4 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/binance_cost_clearance.log` |
| matrix_lanes | PASS | 9.1 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/matrix_lanes.log` |
| polymarket_residual | PASS | 8.0 | `data/research/alpha_lab_campaigns/20260813T060341Z/logs/polymarket_residual.log` |

## Frozen Suite Results

| suite | experiments | status counts |
|---|---:|---|
| Phase 5 | 42 | BLOCKED_DATA=28, FAIL_NO_EDGE=1, FAIL_UNSTABLE=1, INSUFFICIENT_SAMPLE=11, NO_REPORT=1 |
| Phase 5B | 46 | BLOCKED_DATA=20, FAIL_NO_EDGE=7, FAIL_UNSTABLE=17, INSUFFICIENT_SAMPLE=2 |

These are real-data campaign statuses. `BLOCKED_DATA` is an honest result: the causal source, execution arm, independent history, or settlement join needed by the frozen question was unavailable.

## New Alpha Lanes

### Binance Cost Clearance

Matrix span: 360.0 days; shipped round trip: 12.0 bps.

The measured result remains structural: below roughly 30 minutes, ordinary held-to-horizon taker direction does not provide enough movement to support the observed near-coin-flip accuracy. Better selection or lower execution cost is mandatory.

### Matrix Lanes

- Volatility expansion: AUC 0.765; simple RV15 baseline 0.748; top-decile move hit 51.9%. This predicts activity, not direction, and is not independently profitable.
- Time phase: hottest minute-of-quarter `14`; separated confidence intervals = `False`. No clock alpha.
- Spot/perp basis: 5m rich-basis reversion +0.68 bps versus a 12 bps round trip. Real reversion, economically too small.

### Polymarket Market-Prior Residual

The previous zero-overlap conclusion was a data-identity bug: it queried an older DuckDB table instead of the paired recorder exports. The corrected join uses only Polymarket CLOB/Gamma outcomes.

- Market prior: Brier 0.1016, log loss 0.3174.
- Full residual: Brier 0.1140, log loss 0.3692.
- Residual executable actions: 189; net PnL +0.300 shares; round/day-block lower bound -0.0589 per action.
- Verdict: **market remains champion; no residual promotion**.

## Proposal Coverage

Every distinct proposal in the three supplied reviews is mapped below. A proposal is not renamed and rerun when an existing frozen experiment already answers it.

| proposal family | evidence | state |
|---|---|---|
| Market disagreement / minority model resolution | Phase 5 #04; Phase 5B #46-47 | `TESTED` |
| Polymarket probability elasticity / acceleration | Phase 5 #19-20; Phase 5B #43-45,70 | `TESTED` |
| Polymarket implied volatility / deadline convexity | Phase 5 #18,21; Phase 5C volatility tests | `TESTED_DIAGNOSTIC` |
| Order-flow surprise / event propagation | Phase 5 #10-17; Phase 5B #58-68 | `TESTED` |
| Book elasticity / replenishment / resiliency | Phase 5 #15; Phase 5B #59-64,72 | `TESTED_DIAGNOSTIC` |
| Liquidity vacuum / cancellation toxicity | Phase 5B #59-64 | `TESTED_DIAGNOSTIC` |
| Cross-venue information leader / synchronized shock | Phase 5 #08; Phase 5B #73-75 | `PARTIAL_DATA_BLOCKED` |
| Polymarket stale quote / repricing lag | Phase 5 #19-20; Phase 5B #70,73-74 | `PARTIAL_DATA_BLOCKED` |
| Maker replenishment / markout / adverse selection | Phase 5B #59-64; #88 | `PARTIAL_DATA_BLOCKED` |
| Clock phase alpha | TIME_PHASE_ALPHA_V1 | `TESTED` |
| Uncertainty collapse / information clock | Phase 5B #48-50,56-57 | `TESTED` |
| Change points / volatility transitions / regime state | Phase 5B #55,68,82-83 | `TESTED_DIAGNOSTIC` |
| False breakout / continuation / exhaustion | Phase 5 #11-14 | `TESTED` |
| Polymarket settlement convexity | Phase 5 #21 | `TESTED` |
| State-value atlas / regime selector | Phase 5 #27,31; Phase 5B #51,82-83 | `PARTIAL_DATA_BLOCKED` |
| Negative alpha / placebo / randomization | Phase 5 #38-42 | `DATA_BLOCKED` |
| Counterfactual action and order policy | Phase 5 #33-35; Phase 5B #76,86 | `PARTIAL_DATA_BLOCKED` |
| Capacity curve / capital efficiency | Phase 5 #36; Phase 5B #80-81 | `PARTIAL_DATA_BLOCKED` |
| Edge half-life / alpha decay | Phase 5 #30; Phase 5B #49-50 | `DATA_BLOCKED` |
| Alpha portfolio / opportunity auction | Phase 5 #28-29; Phase 5B #80-81 | `DATA_BLOCKED` |
| Complete-set arbitrage | Phase 5 #26; dedicated complete-set research | `PARTIAL_DATA_BLOCKED` |
| Last-seconds convergence / P(flip) / anchor touch | Phase 5B #48,69-70; Phase 5C | `TESTED_DIAGNOSTIC` |
| Market-prior residual fair value | POLY_MARKET_PRIOR_RESIDUAL_V1 | `TESTED` |
| Buy now vs wait | Phase 5 #33; Phase 5B #76 | `TESTED` |
| Two-sided maker / queue fill | Phase 5B #59-64,88 | `DATA_BLOCKED` |
| Binance cost-clearance return distribution | BINANCE_COST_CLEARANCE_V1 | `TESTED` |
| Dynamic barriers / MFE-MAE / holding time | Phase 5C path diagnostics | `TESTED_DIAGNOSTIC` |
| Tradeable / no-trade / extreme selectivity | VOLATILITY_EXPANSION_V1; Phase 5 #01,27 | `TESTED` |
| Funding plus basis carry | SPOT_PERP_BASIS_V1; carry research | `TESTED` |
| Cross-exchange funding dispersion | Recorder exists; independent history gate | `DATA_BLOCKED` |
| BTC/ETH/SOL relative value | Phase 5 #06-07 | `DATA_BLOCKED` |
| Liquidation continuation / exhaustion | Decision-head research; missing executable economics | `TESTED_DIAGNOSTIC` |
| Tail-risk / jump-vs-diffusion | Phase 5C | `TESTED_DIAGNOSTIC` |
| Ensemble disagreement / model-error predictor | Phase 5B #46-47,79 | `TESTED` |
| State calibration / Bayesian updating | Phase 5B #48,52 | `TESTED` |
| Deribit implied vs realized volatility | Options research; synchronized chain history insufficient | `DATA_BLOCKED` |
| Spot-perpetual basis dislocation | SPOT_PERP_BASIS_V1 | `TESTED` |
| Volatility expansion | VOLATILITY_EXPANSION_V1 | `TESTED_DIAGNOSTIC` |

## Data Identity

- PM snapshot rows at copy: 154,370
- PM settlement rows at copy: 3,193
- PM snapshots SHA-256: `680b254723726076a50389d2415bc09950be13504e77e222accf315a1bbb80aa`
- PM settlements SHA-256: `add240cb8b7482634c4700a4c23c561003e42d633bade3b2db807da045d0e153`
- Live DuckDB databases were not stopped or copied. Lock-blocked tests fail closed.

## What To Do Next

1. Do not add any diagnostic AUC to the live ensemble as a trading vote.
2. Keep recorders running until the blocked L2, latency, settlement, and action-arm tests have enough independent days and rounds.
3. Focus new work on execution-cost reduction, maker fill evidence, and sparse volatility-window selection. Direction-model proliferation is not supported by these results.
4. Rerun this exact master command after the evidence window grows; compare immutable campaign directories rather than overwriting results.

## Reproduce

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research_lanes\run_all_experiments.py --maximum-rows 100000
```

No result in this report authorizes real-money trading.
