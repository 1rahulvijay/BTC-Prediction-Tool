# Economic V2 Blueprint: Locked Results

Date: 2026-07-28

Status: **COMPLETE - BOTH EXPERIMENTS REJECTED - RESEARCH ONLY**

## Objective

This campaign tested the two highest-priority claims from the proposed profitable-prediction
blueprint:

1. Separate the common LONG/SHORT movement signal from genuine signed direction.
2. Predict only the information that Polymarket's current contract price has missed.

The campaign was frozen before the locked test. It cannot save serving models, change a
paper policy, place an order, or promote itself.

## Implementation

| Item | Path |
|---|---|
| Frozen protocol | `backend/research/economic_v2/frozen_protocol.json` |
| Campaign runner | `backend/research/economic_v2/run_blueprint_campaign.py` |
| Post-run validator | `backend/research/economic_v2/validate_result.py` |
| Windows launcher | `run_economic_v2_blueprint.bat` |
| Completed run | `data/research/economic_v2/20260728T052508Z/` |

The completed run took 6.4 seconds because it reused causal OOF/locked predictions and the
already recorded Polymarket snapshots. It did not retrain or interrupt the live application.

## Frozen Research Design

### E1: LONG/SHORT factor decomposition

For independent LONG and SHORT scores:

```text
z_long = logit(P(LONG profitable))
z_short = logit(P(SHORT profitable))

magnitude = (z_long + z_short) / 2
direction = (z_long - z_short) / 2
```

The direction score was residualized against the common magnitude score, realized volatility,
volume z-score and trade-count z-score. Its sign selected LONG or SHORT. Evaluation used the
existing post-cost net-return labels.

Two non-overlapping eras were checked:

| Era | UTC range | Decisions |
|---|---|---:|
| Older locked test | 2025-12-27 to 2026-01-25 | 11,518 |
| Recent purged OOF | 2026-05-26 to 2026-07-24 | 23,038 |

### E2: Polymarket market-price residual

The normalized UP/DOWN midpoint was treated as the market's baseline probability. Ridge and
HistGradientBoosting regressors predicted only:

```text
settlement outcome - market-implied probability
```

Inputs included market price, standardized anchor distance, time remaining, volatility,
P(Hold), spreads, overround, top-ask size and recorded depth imbalances.

Markets were separated chronologically and by slug:

```text
324 train markets
109 calibration markets
109 locked-test markets
```

The action gate subtracted:

```text
actual executable ask
+ canonical price-dependent taker fee
+ frozen 3-cent safety buffer
```

Only the first eligible action per market was counted. Entries were re-priced after
0/1/2/5/10 seconds and with 0/1 cent additional slippage.

## Result 1: Common Factor Is Magnitude, Not Tradable Direction

| Era | Horizon | LONG/SHORT correlation | Magnitude vs absolute return IC | Residual direction IC | Bucket monotonicity | Top-decile net |
|---|---:|---:|---:|---:|---:|---:|
| Recent OOF | 5m | 0.903 | 0.4092 | 0.0097 | 0.0667 | -11.45 bps |
| Recent OOF | 15m | 0.732 | 0.3877 | 0.0230 | -0.2242 | -12.09 bps |
| Older locked | 5m | 0.971 | 0.4710 | 0.0080 | -0.3818 | -11.94 bps |
| Older locked | 15m | 0.933 | 0.4764 | 0.0329 | 0.2727 | -12.84 bps |

The magnitude factor is repeatable and useful: IC was approximately 0.39-0.48. The signed
residual is too weak: IC was only 0.008-0.033, signed buckets were not consistently monotonic,
and every highest-confidence slice lost after costs.

Gate result:

| Frozen requirement | Result |
|---|---|
| Positive direction IC in both eras | PASS |
| Positive IC in at least 75% of time blocks for every slice | FAIL; minimum 60% |
| Monotonic signed-return buckets in every slice | FAIL |
| Positive top-decile net return in every era/horizon | FAIL |

**Decision:** keep the existing LONG/SHORT outputs as movement/opportunity information. Do not
promote their score difference as an economic LONG/SHORT selector.

## Result 2: The Market Residual Did Not Beat the Market

Locked-test probability quality:

| Model | Rows | AUC | Brier | Log loss |
|---|---:|---:|---:|---:|
| Market midpoint, raw | 546 | 0.8578 | 0.1544 | 0.4655 |
| Market midpoint, calibrated | 546 | 0.8533 | 0.1579 | 0.4740 |
| Residual ensemble | 546 | 0.8440 | 0.1616 | 0.4904 |
| Shuffled-residual control | 546 | 0.8550 | 0.1559 | 0.4871 |

The residual ensemble was worse than the calibrated market on Brier score and log loss. It
also failed to beat the shuffled-residual control. This means the recorded BTC/book context
did not add stable information beyond the contemporaneous contract price in this sample.

The train-versus-test adversarial AUC was 0.7518. A value this far above 0.50 indicates that
the locked period is measurably different from the training period, which further weakens any
deployment claim.

Executable settlement result for the residual ensemble:

| Stress | Trades | Mean PnL/share | Profit factor | Bootstrap lower 95% |
|---|---:|---:|---:|---:|
| Immediate, recorded ask | 109 | -0.0081 | 0.959 | -0.0905 |
| 2-second delay + 1c slippage | 108 | -0.0271 | 0.865 | -0.1059 |

Every frozen promotion requirement failed:

| Frozen requirement | Result |
|---|---|
| Lower Brier than calibrated market | FAIL |
| Lower log loss than calibrated market | FAIL |
| Positive immediate post-fee PnL | FAIL |
| Positive PnL after 2s delay and 1c stress | FAIL |
| At least 200 locked-test trades | FAIL; 108 |
| Positive bootstrap lower bound | FAIL |
| Profit factor above 1.10 | FAIL |

The market-only calibrated baseline averaged +2.68c/share over 105 immediate entries, but its
95% bootstrap lower bound was -5.45c/share and it had fewer than 200 trades. This is an
interesting observation, not proof of edge and not a promotion candidate.

## Plain-English Meaning

- The current models can recognize when BTC is likely to move.
- They still do not reliably identify which side will make money after costs.
- Polymarket's live contract price already contains most of the settlement information present
  in the recorded BTC and book features.
- Adding a residual model made the probability and trading results worse, not better.
- Higher model complexity would not repair this locked result.

## Blueprint Coverage

| Blueprint component | Repository status |
|---|---|
| Frozen protocol and immutable test | Implemented for this campaign |
| Common magnitude/direction factor | Tested and rejected for direction |
| Market-price residual | Tested and rejected |
| Executable ask, canonical fee and delay stress | Implemented in this campaign |
| Grouped chronological train/calibration/test | Implemented |
| Calibration, market bootstrap and negative controls | Implemented |
| Cost-aware LONG/SHORT targets | Previously implemented in the 120d/180d policy campaigns |
| Magnitude and conditional direction heads | Previously tested; magnitude useful, direction weak |
| First-barrier/event-time targets | Implemented in the event-time specialist campaign |
| Action-specific quantiles and conservative EV | Previously tested; useful uncertainty, no promotable EV |
| Regime and ACT/SKIP filters | Existing research infrastructure; no proven economic lift |
| Full L2 additions/cancellations and maker queue position | Forward-recorder evidence required |
| Sub-second end-to-end latency proof | Not available in the historical recorder |
| Eight-week, 500-candidate forward proof | Not complete |

## Integrity Validation

`validate_result.py` passed every mechanical check:

- protocol and campaign script hashes match the manifest;
- no production artifact changed and production eligibility is false;
- every market belongs to exactly one split;
- no duplicate market/checkpoint rows;
- locked predictions and signals contain test markets only;
- all probabilities are finite and bounded;
- at most one signal exists per policy/market;
- the full 60-row execution stress grid is present;
- all frozen gates were evaluated explicitly.

The machine-readable report is:

`data/research/economic_v2/20260728T052508Z/validation.json`

## Decision And Next Step

Neither experiment is eligible for serving, paper-policy promotion, or live trading.

The correct next step is not to lower thresholds or add another classifier to these same
features. Continue the frozen forward recorder until the Polymarket dataset supports at least
200 locked-test actions for research and 500 independently resolved candidates over eight
weeks for forward evidence. A later residual experiment must use a new preregistered period
and should first address the measured distribution drift.
