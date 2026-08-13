# Multi-Engine Research Brief Batch Report

Date: 2026-08-13
Authority: RESEARCH ONLY
Capital authority: **false**

## Scope

This report covers the 40-question multi-engine brief supplied on 2026-08-13. It records which
questions were tested now, which were already tested, and which cannot be answered honestly with
the current historical stores. No result in this report is wired into serving or trading.

Canonical runner:

```text
python research_lanes/run_multi_engine_brief_batch.py
```

Canonical result:

```text
research_lanes/results/multi_engine_brief_batch_20260813T072836Z.json
SHA256 D224C7F6586F8C530215215FEAEFEA9D0E1C9651A49FC31808686B46C1506324
```

The earlier `20260813T072623Z` artifact is retained as an audit artifact. Its calculations are
identical; the canonical rerun only corrected wording that had overstated knowledge of the exact
rule-specified settlement oracle.

## Frozen Protocol

- Binance matrix: 518,400 one-minute rows, approximately 360 days.
- Polymarket snapshots: recorded Pyth/Chainlink app reference, top-level market state and official
  settlement outcomes.
- Funding matrix: 270 observed `hours_to_funding` reset events.
- Chronology: 70/30 day split, or 60/10/30 train/calibration/test for confidence thresholds.
- Binance round-trip cost: 12 bps deducted from every directional trade.
- Uncertainty: day-clustered bootstrap with 8,000 draws.
- Multiple testing: family size 96, alpha `0.05 / 96`.
- Promotion: positive family-adjusted lower confidence bound after cost.
- No serving imports, artifact publishing, or model changes.

## Result Summary

| New family | Main result | Verdict |
|---|---|---|
| Recorded reference vs Binance prices | The recorded Pyth/Chainlink reference tracked official PM outcomes much better near expiry than a causally completed Binance minute close | `DIAGNOSTIC_ONLY` |
| Spot/perpetual CVD disagreement | Disagreement selected larger moves, but neither venue's sign made money after 12 bps | `FAIL_NO_EDGE` |
| Funding event and next-rate forecast | The learned next-rate forecast was worse than the current-rate baseline; event trades were negative and underpowered | `FAIL_NO_EDGE` |
| Psychological level crossing | $100/$500/$1,000 continuation lost about the full transaction cost; failure rates were high | `FAIL_NO_EDGE` |
| Confidence threshold economics | AUC stayed near 0.50 and no threshold produced positive net value | `FAIL_NO_EDGE` |

Promotable configurations: **0**.

## 1. Recorded Reference Price Diagnostic

The test joined 133,967 snapshots from 779 resolved rounds. It compared:

1. the app's recorded Pyth/Chainlink reference relative to the anchor;
2. the latest causally completed Binance one-minute spot close;
3. a perpetual price derived from spot plus the recorded basis;
4. the official Polymarket settled side.

Selected checkpoints:

| Horizon | Seconds left | Rounds | Recorded reference accuracy | Binance spot accuracy | Derived perp accuracy |
|---:|---:|---:|---:|---:|---:|
| 5m | 120 | 560 | 76.6% | 53.0% | 58.8% |
| 5m | 60 | 523 | 80.1% | 53.2% | 58.9% |
| 5m | 30 | 438 | 82.9% | 50.2% | 55.7% |
| 15m | 300 | 187 | 82.9% | 62.0% | 71.1% |
| 15m | 120 | 171 | 91.8% | 63.7% | 72.5% |
| 15m | 30 | 109 | 90.8% | 62.4% | 74.3% |

When the recorded reference and Binance spot disagreed, the recorded reference matched the
official outcome 74.6%-90.9% of the time across the displayed non-anchor checkpoints.

This is **not an arbitrage result**. The archive does not include immutable market rule text or
the exact rule-specified BTC oracle. It shows that the app must not substitute Binance spot for
its recorded Polymarket reference near settlement. Event-time lead/lag and executable PM repricing
remain untested.

## 2. Spot/Perpetual Flow Disagreement

The train era froze 75th-percentile absolute CVD thresholds. The test era contained 1,800 strong,
opposite-sign spot/perpetual CVD states. Each venue's sign was scored independently.

| Horizon | Selected absolute move | Baseline move | Spot-sign net | Perp-sign net |
|---:|---:|---:|---:|---:|
| 5m | 13.76 bps | 7.55 bps | -12.24 bps | -11.76 bps |
| 15m | 21.72 bps | 13.09 bps | -12.08 bps | -11.92 bps |
| 30m | 30.40 bps | 18.60 bps | -11.56 bps | -12.44 bps |

Disagreement is a useful **movement/regime diagnostic**. It did not identify which flow wins and
must not become a direction trigger.

## 3. Funding Event Study

The next-rate model used only T-30m features and was tested on the final 84 of 270 funding events.

```text
Model MAE:               0.2225 funding-rate bps
Current-rate naive MAE:  0.1145 funding-rate bps
Model improvement:      -0.1079 bps
Family-adjusted LCB:    -0.1848 bps
Family-adjusted UCB:    -0.0377 bps
```

The learned model was significantly worse than carrying forward the current rate. The
train-frozen extreme-premium event slice had only 15 test events over 12 days. Every pre-funding
unwind and post-funding reversal arm was negative after 12 bps; this slice is also too small for
an affirmative conclusion.

## 4. Psychological Levels

The test entered continuation after a completed one-minute close crossed a $100, $500 or $1,000
level, with a 30-minute cooldown.

| Level | 5m net | 15m net | 30m net | Failure-rate range |
|---:|---:|---:|---:|---:|
| $100 | -12.23 bps | -12.49 bps | -12.46 bps | 67.7%-86.3% |
| $500 | -11.92 bps | -12.00 bps | -12.33 bps | 65.7%-84.7% |
| $1,000 | -12.53 bps | -12.90 bps | -14.01 bps | 65.4%-85.3% |

There is no evidence for blind round-number continuation. Prior highs/lows, VWAP, volume nodes
and order-book-conditioned retests require different labels or unavailable L2 history.

## 5. Confidence Threshold Economics

A HistGradientBoosting direction model was trained on 60% of days. A threshold was selected on a
separate 10% era with at least 250 calls, then evaluated once on the final 30%.

| Horizon | Test AUC | Selected threshold | Test calls | Net bps | Family-adjusted LCB |
|---:|---:|---:|---:|---:|---:|
| 5m | 0.523 | 0.55 | 12,757 | -11.27 | -11.84 |
| 15m | 0.507 | 0.55 | 15,981 | -11.43 | -13.02 |
| 30m | 0.503 | 0.55 | 21,952 | -11.22 | -13.37 |

Higher thresholds reduced sample size but did not create positive economics. Confidence cannot
repair an uninformative direction target.

## Complete 40-Question Coverage

| # | Question | Status | Evidence or exact blocker |
|---:|---|---|---|
| 1 | Is PM tracking the wrong BTC price? | `PARTIAL` | New recorded-reference diagnostic ran; exact rule oracle text is not archived |
| 2 | Which price should predict PM? | `PARTIAL` | Recorded reference, causal Binance spot and derived perp compared; Coinbase/Bybit/VWAP/microprice event-time series absent |
| 3 | Synthetic PM probability from options | `BLOCKED_DATA` | No aligned strike-level options chain |
| 4 | Hedge PM using Binance | `PARTIAL` | Hedged-maker upper bounds ran previously; real PM fills, binary delta path and rehedge costs absent |
| 5 | PM overreaction/overshoot | `BLOCKED_DATA` | Needs synchronized BTC and PM revisions at 1s-60s |
| 6 | Information in a PM trade | `BLOCKED_DATA` | Aggressive PM trade events are not aligned to BTC and settlement states |
| 7 | Toxic maker fills | `BLOCKED_DATA` | Actual fills and 100ms-5s markouts absent |
| 8 | Queue position value | `BLOCKED_DATA` | Queue identity, ahead size and realized fills absent |
| 9 | Maker cancel timing | `BLOCKED_DATA` | Quote attempts, queue state, cancel/reprice and adverse outcomes absent |
| 10 | Alpha decay | `BLOCKED_DATA` | No synchronized 100ms-5s execution markouts |
| 11 | Capacity limit | `BLOCKED_EVIDENCE` | No positive executable strategy or fillable size curve exists |
| 12 | Spot or perp leads | `INSUFFICIENT_RESOLUTION` | Prior minute test found 0.992 same-minute correlation and no ordered isolated events |
| 13 | Spot/perp flow disagreement | `RAN` | New test selected larger movement but both directional policies failed after cost |
| 14 | Price + OI + funding state | `BLOCKED_DATA` | Causal historical OI state is absent |
| 15 | Pre/post funding behavior | `PARTIAL` | New rate/premium/return study ran; OI and liquidation components absent, extreme test n=15 |
| 16 | Predict next funding rate | `RAN` | New model was worse than the current-rate naive baseline |
| 17 | Forecast liquidation cascade | `BLOCKED_DATA` | No aligned liquidation and OI event history |
| 18 | Infer liquidation/stop clusters | `BLOCKED_DATA` | No position-entry distribution, OI ladder or liquidation history |
| 19 | Psychological-level acceleration | `PARTIAL` | New $100/$500/$1,000 continuation test failed; VWAP/high-low/volume-node L2 variants not tested here |
| 20 | Predict false breakouts | `RAN_PREVIOUSLY` | Action-value batch AUC 0.510-0.521 with negative net value |
| 21 | Momentum exhaustion | `RAN_PREVIOUSLY` | Continuation, recovery, absorption and path lanes found no robust executable edge |
| 22 | Forecast volatility before it arrives | `RAN_PREVIOUSLY` | Movement AUC reached 0.688-0.770, but no direction/instrument converted it to net profit |
| 23 | Is implied volatility wrong? | `BLOCKED_DATA` | No historical options IV surface |
| 24 | Does options skew predict tails? | `BLOCKED_DATA` | No historical put/ATM/call IV surface |
| 25 | Options/Binance/PM consensus | `BLOCKED_DATA` | Options-implied probability series absent |
| 26 | All-venue disagreement | `BLOCKED_DATA` | Synchronized event-time venue probabilities/prices absent |
| 27 | Trade convergence without settlement direction | `PARTIAL` | PM residual/wait lanes ran; executable causal convergence exits and fills remain absent |
| 28 | Optimal PM entry zone | `RAN_PREVIOUSLY` | Entry-price/state atlas ran; no family-wise significant executable cell |
| 29 | Best time remaining | `RAN_PREVIOUSLY` | Time-to-expiry and fixed-delay surfaces ran; no positive after-cost interval |
| 30 | Time-of-day regime | `RAN_PREVIOUSLY` | UTC/session/weekend slices were unstable or non-economic |
| 31 | When should bot stop? | `BLOCKED_EVIDENCE` | Requires a proven live strategy with resolved calibration, slippage and EV history |
| 32 | When should strategy restart? | `BLOCKED_EVIDENCE` | Same prerequisite plus independent shadow recovery evidence |
| 33 | Alpha survival after promotion | `BLOCKED_EVIDENCE` | No promoted positive strategy with 1d-30d forward windows |
| 34 | Profit concentration | `BLOCKED_EVIDENCE` | No qualified positive daily PnL series |
| 35 | Tail correlation | `BLOCKED_EVIDENCE` | No set of positive synchronized strategy PnL streams |
| 36 | Capital efficiency | `RAN_PREVIOUSLY` | Capital-minute analysis was negative; no positive candidate to allocate |
| 37 | Opportunity auction | `DESIGN_ONLY` | Allocation cannot be validated before positive LCBs, risk, duration and fill capacity exist |
| 38 | Profit while BTC is flat | `BLOCKED_EVIDENCE` | No validated portfolio of complementary positive engines |
| 39 | Profit without direction | `RAN_PREVIOUSLY` | Complete-set, basis, microbasis and maker upper-bound lanes ran; none qualified |
| 40 | Confidence threshold maximizing money | `RAN` | New 60/10/30 test: all selected thresholds were negative after 12 bps |

All 40 questions are classified. A blocked question was not approximated with minute candles when
the requested estimand requires event order, queue state, options, OI, liquidations or realized
fills.

## Decision

Do not wire a new trade trigger from this batch. The only product-level implication with strong
diagnostic support is to keep the recorded Polymarket reference distinct from Binance prices and
display source/age explicitly. Spot/perp flow disagreement can be retained as a movement warning,
not a directional call. The funding model, psychological-level continuation and direction
confidence gate are rejected for promotion.

The next evidence frontier remains operational data collection: exact market-rule metadata,
event-time multi-venue prices, PM trades and quote attempts, queue/fill markouts, historical OI and
liquidations, and a strike-level options chain.
