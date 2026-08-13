# Standalone Alpha Laboratory - Complete Campaign

Run: `20260813T061703Z`

Git: `25f908f286c3ab7090e8aa2229a9a8c8c1d3f50f` (dirty)

Authority: `RESEARCH_ONLY`; no serving, paper, or live strategy was modified

## Executive Verdict

**No tested strategy earned promotion.** The campaign found diagnostics and state information, but no robust executable alpha with a positive lower confidence bound after declared costs.

Accuracy or AUC alone is not treated as profit. A test is promotable only when its chronological out-of-sample net-EV lower bound is positive at executable prices and the minimum independent-day/round gates pass.

## Execution

| stage | result | seconds | log |
|---|---:|---:|---|
| phase5_42 | PASS | 103.3 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5_42.log` |
| phase5b_46 | PASS | 129.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5b_46.log` |
| phase5c_brier_decomposition_market_vs_model | PASS | 0.7 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_brier_decomposition_market_vs_model.log` |
| phase5c_effect_size_to_cost_ratio | PASS | 0.3 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_effect_size_to_cost_ratio.log` |
| phase5c_effective_independent_sample_size | PASS | 18.5 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_effective_independent_sample_size.log` |
| phase5c_jump_vs_diffusion_decomposition | PASS | 1.1 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_jump_vs_diffusion_decomposition.log` |
| phase5c_last_crossing_timing_distribution | PASS | 0.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_last_crossing_timing_distribution.log` |
| phase5c_mfe_mae_joint_distribution | PASS | 1.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_mfe_mae_joint_distribution.log` |
| phase5c_near_settlement_terminal_margin | PASS | 0.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_near_settlement_terminal_margin.log` |
| phase5c_probability_monotonicity | PASS | 0.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_probability_monotonicity.log` |
| phase5c_volatility_clustering_half_life | PASS | 1.1 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/phase5c_volatility_clustering_half_life.log` |
| binance_cost_clearance | PASS | 30.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/binance_cost_clearance.log` |
| matrix_lanes | PASS | 11.1 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/matrix_lanes.log` |
| polymarket_prior_comparison | PASS | 11.9 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/polymarket_prior_comparison.log` |
| polymarket_fullset_maker | PASS | 10.4 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/polymarket_fullset_maker.log` |
| polymarket_residual_offset | PASS | 10.8 | `data/research/alpha_lab_campaigns/20260813T061703Z/logs/polymarket_residual_offset.log` |

## Frozen Suite Results

| suite | experiments | status counts |
|---|---:|---|
| Phase 5 | 42 | BLOCKED_DATA=29, FAIL_NO_EDGE=1, FAIL_UNSTABLE=1, INSUFFICIENT_SAMPLE=11 |
| Phase 5B | 46 | BLOCKED_DATA=20, FAIL_NO_EDGE=7, FAIL_UNSTABLE=17, INSUFFICIENT_SAMPLE=2 |

These are real-data campaign statuses. `BLOCKED_DATA` is an honest result: the causal source, execution arm, independent history, or settlement join needed by the frozen question was unavailable.

### Phase 5 - all 42 results

| experiment | result | conclusion |
|---|---|---|
| `P5_01_ALPHA_EXTRACTABILITY_UPPER_BOUND` | `FAIL_NO_EDGE` | Constrained oracle ceiling plus locked classifier regret |
| `P5_02_HISTORY_LIVE_TRANSPORTABILITY` | `FAIL_UNSTABLE` | History-versus-recent domain discriminator |
| `P5_03_FEED_INCREMENTAL_ECONOMIC_VALUE` | `INSUFFICIENT_SAMPLE` | Locked BTC classifier evaluated after full costs |
| `P5_04_ORACLE_CURRENT_MODEL_DISAGREEMENT` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_05_SIGNAL_CONTEXT_SIGN_REVERSAL` | `INSUFFICIENT_SAMPLE` | Locked model plus fixed-context CVD sign-reversal diagnostics |
| `P5_06_DYNAMIC_CRYPTO_FACTOR_RESIDUAL` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_07_FACTOR_RESIDUAL_MEAN_REVERSION_VS_CONTINUATION` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_08_CROSS_EXCHANGE_DISLOCATION_DECAY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_09_SPOT_PERPETUAL_BASIS_RESIDUAL` | `INSUFFICIENT_SAMPLE` | Locked BTC classifier evaluated after full costs |
| `P5_10_SYNTHETIC_METAORDER_SEGMENTATION` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_11_METAORDER_CONTINUATION_HEAD` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_12_METAORDER_EXHAUSTION_REVERSAL` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_13_CORE_REACTION_FLOW_OVERSHOOT` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_14_PRICE_IMPACT_EFFICIENCY_DECAY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_15_LIQUIDITY_WITHDRAWAL_HAZARD` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_16_EVENT_LANGUAGE_SURPRISE` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_17_SURPRISE_PROPAGATION` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_18_SURPRISE_AS_VOLATILITY_NOT_DIRECTION` | `BLOCKED_DATA` | Magnitude predictability measured; executable magnitude instrument is not in this dataset |
| `P5_19_POLY_PROBABILITY_ELASTICITY` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: elasticity |
| `P5_20_POLY_ELASTICITY_RESIDUAL_CLOSURE` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: residual_closure |
| `P5_21_POLY_DEADLINE_CONVEXITY_SURFACE` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: deadline_convexity |
| `P5_22_POLY_NEW_ROUND_OPENING_INHERITANCE` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: opening_inheritance |
| `P5_23_POLY_ROUND_BOUNDARY_RESONANCE` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: boundary_resonance |
| `P5_24_POLY_POST_SETTLEMENT_UNWIND` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_25_POLY_CROSS_EXPIRY_CONSISTENCY` | `INSUFFICIENT_SAMPLE` | Cross-expiry monotonic complete-set lock |
| `P5_26_POLY_COMPLETE_SET_LOCK_FREQUENCY` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_27_ALPHA_CONTEXT_SPECIALIZATION` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_28_ALPHA_REDUNDANCY_AND_UNIQUE_VALUE` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_29_ALPHA_COLLISION_INTERFERENCE` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_30_ALPHA_DECAY_CHANGE_POINTS` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_31_ALPHA_REGIME_REVIVAL` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_32_REWARD_TRANSPORT` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_33_ENTER_NOW_VS_WAIT` | `INSUFFICIENT_SAMPLE` | Polymarket dynamic response: enter_now_vs_wait |
| `P5_34_HOLD_EXIT_SWITCH_LOCK_COUNTERFACTUAL` | `BLOCKED_DATA` | Prerequisite audit |
| `P5_35_CONFORMAL_NET_EV_GATE` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_36_CAPACITY_CURVE` | `BLOCKED_DATA` | Required data is unavailable |
| `P5_37_ANYTIME_VALID_FORWARD_EVIDENCE` | `INSUFFICIENT_SAMPLE` | Forward ledger has insufficient outcomes |
| `P5_38_PLACEBO_TIMING` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_39_SIGN_AND_LABEL_RANDOMIZATION` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_40_PARAMETER_NEIGHBORHOOD_STABILITY` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_41_PROFIT_CONCENTRATION_AND_EVENT_DEPENDENCE` | `BLOCKED_DATA` | Candidate-dependent audit |
| `P5_42_NEGATIVE_CONTROL_STRATEGY` | `BLOCKED_DATA` | Candidate-dependent audit |

### Phase 5B - all 46 results

| experiment | result | conclusion |
|---|---|---|
| `P5B_43_FORECAST_REVISION_PATH` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_44_FORECAST_REVISION_OVERSHOOT` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_45_FORECAST_STABILITY_VS_ACCURACY` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_46_MINORITY_MODEL_CORRECTNESS` | `FAIL_NO_EDGE` | Minority-versus-consensus selector on resolved model votes |
| `P5B_47_SHARED_INFORMATION_FALSE_CONSENSUS` | `FAIL_UNSTABLE` | Effective ensemble independence diagnostic |
| `P5B_48_TIME_TO_EXPIRY_CALIBRATION_SURFACE` | `FAIL_NO_EDGE` | Time-to-expiry calibration surface on untouched checkpoints |
| `P5B_49_MODEL_CONFIDENCE_COLLAPSE_HAZARD` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_50_PREDICTION_FRESHNESS_DECAY` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_51_MARKET_STATE_NOVELTY_GATE` | `INSUFFICIENT_SAMPLE` | Novelty veto against an unchanged direction policy |
| `P5B_52_LOCAL_SAMPLE_SUPPORT` | `FAIL_NO_EDGE` | Local analogue support on untouched states |
| `P5B_53_FEATURE_RELATIONSHIP_SIGN_STABILITY` | `FAIL_UNSTABLE` | Feature sign stability across frozen environments |
| `P5B_54_WORST_ENVIRONMENT_MODEL_SELECTION` | `FAIL_NO_EDGE` | Worst-environment model-selection diagnostic |
| `P5B_55_FEATURE_VALUE_DRIFT` | `FAIL_UNSTABLE` | Frozen monthly feature-value drift diagnostic |
| `P5B_56_INFORMATION_TIME_CLOCK` | `FAIL_UNSTABLE` | Information-clock comparison on one untouched window |
| `P5B_57_INFORMATION_EXHAUSTION` | `FAIL_UNSTABLE` | Information Exhaustion predictive diagnostic |
| `P5B_58_EVENT_BURSTINESS_PREDICTABILITY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_59_MARKET_RESILIENCE_AFTER_AGGRESSIVE_FLOW` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_60_BID_ASK_REPLENISHMENT_ASYMMETRY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_61_SPREAD_SHOCK_DIRECTIONAL_ASYMMETRY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_62_MICROPRICE_MARKOUT` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_63_BUY_SELL_IMPACT_ASYMMETRY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_64_TOXIC_FLOW_VETO` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_65_PATH_EFFICIENCY_RATIO` | `FAIL_NO_EDGE` | Path Efficiency predictive diagnostic |
| `P5B_66_TRADE_SIGN_ENTROPY` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_67_PATH_ROUGHNESS_AND_TERMINAL_OUTCOME` | `FAIL_NO_EDGE` | Path roughness versus terminal-direction baseline |
| `P5B_68_VOLATILITY_OF_VOLATILITY_TRANSITION` | `FAIL_UNSTABLE` | Volatility Of Volatility predictive diagnostic |
| `P5B_69_ANCHOR_PINNING_VS_ESCAPE` | `FAIL_UNSTABLE` | Anchor pinning versus escape state classifier |
| `P5B_70_PROBABILITY_STICKINESS_NEAR_EXTREMES` | `FAIL_UNSTABLE` | Probability stickiness near bounded extremes |
| `P5B_71_YES_NO_COMPLEMENT_EXECUTION_ASYMMETRY` | `FAIL_UNSTABLE` | YES/NO settlement-equivalent execution comparison |
| `P5B_72_TOKEN_LIQUIDITY_ASYMMETRY_PERSISTENCE` | `FAIL_UNSTABLE` | Token liquidity asymmetry persistence |
| `P5B_73_POLYMARKET_QUOTE_LEAD_LAG` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_74_POLYMARKET_RESPONSE_DECOMPOSITION` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_75_SETTLEMENT_SOURCE_BASIS_HAZARD` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_76_SEQUENTIAL_VALUE_OF_INFORMATION` | `FAIL_NO_EDGE` | Sequential value of one additional checkpoint |
| `P5B_77_SKIP_REASON_ECONOMIC_VALUE` | `FAIL_UNSTABLE` | Economic value of recorded skip reasons |
| `P5B_78_DATA_QUALITY_CONDITIONED_PERFORMANCE` | `FAIL_UNSTABLE` | Data-quality-conditioned calibration surface |
| `P5B_79_MODEL_ERROR_TAXONOMY` | `FAIL_UNSTABLE` | Resolved decision error taxonomy |
| `P5B_80_PNL_SOURCE_ATTRIBUTION` | `FAIL_UNSTABLE` | Paper PnL source accounting |
| `P5B_81_CAPITAL_EFFICIENCY` | `FAIL_UNSTABLE` | Capital-duration-normalized paper PnL |
| `P5B_82_ONLINE_REGIME_DISCOVERY` | `FAIL_UNSTABLE` | Data-derived state diagnostic |
| `P5B_83_STATE_TRANSITION_GRAPH` | `FAIL_UNSTABLE` | Data-derived state diagnostic |
| `P5B_84_HORIZON_CONSISTENCY` | `INSUFFICIENT_SAMPLE` | Cross-horizon directional-divergence diagnostic |
| `P5B_85_CANDIDATE_EVIDENCE_COMPLETENESS` | `BLOCKED_DATA` | Candidate-evidence completeness gate |
| `P5B_86_COUNTERFACTUAL_ACTION_ARM_COMPLETENESS` | `BLOCKED_DATA` | Prerequisite audit |
| `P5B_87_RECORDER_GAP_SELECTION_BIAS` | `BLOCKED_DATA` | Required data is unavailable |
| `P5B_88_TIMESTAMP_UNCERTAINTY_STRESS` | `BLOCKED_DATA` | Prerequisite audit |

### Phase 5C - all path diagnostics

| diagnostic | process | reported conclusion |
|---|---|---|
| `brier_decomposition_market_vs_model` | PASS | and it is why the market-prior residual is the only supported direction. |
| `effect_size_to_cost_ratio` | PASS | below 0.75x, which is why none of them converted. |
| `effective_independent_sample_size` | PASS | on this window, however many rows it reports. |
| `jump_vs_diffusion_decomposition` | PASS | diffusion-style exit is not structurally wrong. |
| `last_crossing_timing_distribution` | PASS | above is the whole of the settlement-fragility problem. |
| `mfe_mae_joint_distribution` | PASS | not extract such a subset; revisit only if a prefiltered effect clears costs. |
| `near_settlement_terminal_margin` | PASS | exposed to oracle basis rather than to a forecast. |
| `probability_monotonicity` | PASS | AUC 0.8731. |
| `volatility_clustering_half_life` | PASS | that justified entry. Fixed holding constants are not obviously the right shape. |

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

- Market prior: Brier 0.1017, log loss 0.3177.
- Full residual: Brier 0.1160, log loss 0.3804.
- Residual executable actions: 191; net PnL +0.265 shares; round/day-block lower bound -0.0583 per action.
- Verdict: **market remains champion; no residual promotion**.

### Polymarket Complete Set and Maker Upper Bound

- Full-set all-in opportunities: 114; rate 0.076%; theoretical top-of-book total $43.82 across 10 days.
- Verdict: mechanically real but economically negligible; stale/crossed-book artifacts can only reduce the realizable total.
- Two-sided maker upper-bound EV: +0.0127 per share, but both-leg fill probability, queue position and adverse-selection markout are unobserved. This is **not a strategy result**.

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
| Complete-set arbitrage | POLY_FULLSET_ARB_V1 | `TESTED_NEGLIGIBLE` |
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

- PM snapshot rows at copy: 155,188
- PM settlement rows at copy: 3,196
- PM snapshots SHA-256: `ecedfffa6b4e429ef3fdaf770453ffd1f823ecbdd3d545767cd293e517414031`
- PM settlements SHA-256: `44886948ca623665c6341c1c240c08b804ad329f39b56480a386b8864ff7dd71`
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
