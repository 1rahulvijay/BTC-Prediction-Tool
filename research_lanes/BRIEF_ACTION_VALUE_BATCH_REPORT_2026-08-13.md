# Action-Value Research Brief Batch

Date: 2026-08-13

Authority: **RESEARCH ONLY**

This report records the runnable tests requested by the two action-value research briefs. It
does not replace an earlier report, authorize capital, or change a serving model.

## Source Questions

The briefs proposed changing the main research question from "will BTC go up or down?" to:

> Which available action has a conservative positive expected value after costs?

The highest-priority executable questions were LONG/SHORT/WAIT value, movement clearance,
entry timing, thesis survival, adverse/favorable position management, breakout quality,
model-failure risk and venue leadership.

## Frozen Protocol

| Item | Setting |
|---|---:|
| Primary matrix | `data/research_matrix_1m.parquet` |
| Matrix coverage | 360 UTC days, 518,400 one-minute rows |
| Split | 60% train / 10% calibration / 30% untouched test |
| Independent test | 109 UTC days |
| Boundary purge | 30 minutes |
| Binance round-trip cost | 12 bps |
| Confidence unit | UTC day |
| Bootstrap draws | 8,000 |
| Family size | 64 reported comparisons |
| Family alpha | 0.05 / 64 |
| Promotion rule | after-cost family-adjusted LCB > 0 |

The final result is:

`research_lanes/results/action_value_brief_batch_20260813T071004Z.json`

SHA-256:

`E0FA93777F31D92EF943B5DD72A6C4B050DDE3FEAD6E1E21A19598DC39C38ADA`

## Test 1 - Direct LONG / SHORT / WAIT Value

**Question:** Does a causal return model identify LONG or SHORT states that clear 12 bps, with
WAIT used outside the calibration-frozen top 5% of absolute predictions?

**Method:** A histogram gradient boosting regressor was fitted to backward-looking volatility,
flow, VPIN, compression, shock, CVD and spot/perpetual features. A second classifier estimated
`P(abs(move) > 12 bps)`. The movement-gated policy also required a calibration-frozen
top-decile movement probability.

| Horizon | Movement AUC | Direct calls | Direct day-equal net bps/trade | Direct LCB | Gated calls | Gated day-equal net bps/trade | Gated LCB |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m | 0.770 | 6,783 | -12.55 | -13.62 | 4,136 | -13.57 | -15.97 |
| 15m | 0.711 | 8,677 | -11.43 | -14.20 | 5,570 | -10.59 | -15.47 |
| 30m | 0.688 | 7,653 | -14.07 | -19.80 | 4,630 | -14.14 | -20.80 |

**Result:** `FAIL_NO_EDGE`. Movement is predictable, especially at 5m, but the direction and
payoff do not clear transaction costs. The movement gate does not rescue the trade.

## Test 2 - Historical Model-Failure Gate

**Question:** Can a second model identify when the base direction call will be wrong?

**Method:** The 35-day calibration era was split chronologically. Its first half trained the
failure model; its second half froze a top-decile trust threshold. The final 109 days remained
untouched.

| Horizon | Failure-gate AUC | Trusted calls | Trusted day-equal net bps/trade | Trusted LCB | Ungated day-equal net bps/trade |
|---:|---:|---:|---:|---:|---:|
| 5m | 0.507 | 2,819 | -12.11 | -14.07 | -12.55 |
| 15m | 0.506 | 1,102 | -9.63 | -15.34 | -11.43 |
| 30m | 0.495 | 852 | -14.19 | -24.41 | -14.07 |

**Result:** `FAIL_NO_EDGE`. The failure gate is approximately random. The 15m point estimate is
less negative, but its lower bound is worse and remains far below zero. This does not validate
a live ACT/SKIP model.

## Test 3 - Enter Now Versus Fixed Delay

**Question:** For the same frozen signal and original exit clock, does waiting improve value?

**Method:** Entry was delayed by 1, 3 or 5 minutes. The exit time and round-trip cost were held
constant. The table reports delayed net value minus immediate-entry net value.

| Horizon | Delay | Difference bps | Family LCB | Family UCB |
|---:|---:|---:|---:|---:|
| 5m | 1m | -0.05 | -0.53 | +0.38 |
| 5m | 3m | +0.25 | -0.60 | +1.10 |
| 15m | 1m | -0.17 | -0.66 | +0.24 |
| 15m | 3m | -0.19 | -1.23 | +0.86 |
| 15m | 5m | -0.13 | -1.67 | +1.17 |
| 30m | 1m | +0.54 | -0.16 | +1.57 |
| 30m | 3m | +0.74 | -0.59 | +2.06 |
| 30m | 5m | +0.64 | -0.99 | +2.40 |

**Result:** `FAIL_UNSTABLE`. Every confidence interval crosses zero. No fixed delay is proven
better than immediate entry.

## Test 4 - Thesis Survival Clock

**Question:** How long do selected calls remain favorable, even if the final endpoint is wrong?

**Method:** For each untouched-test call, signed close-to-close PnL was checked at every minute.
The first close at or below -5 bps was treated as a descriptive invalidation event. This is not
an executable exit strategy because the threshold and action were not independently validated.

| Horizon | Calls | Median first -5 bps or timeout | Never reached -5 bps | Favorable at endpoint |
|---:|---:|---:|---:|---:|
| 5m | 6,783 | 4m | 45.6% | 50.3% |
| 15m | 8,677 | 4m | 27.6% | 48.6% |
| 30m | 7,653 | 4m | 19.3% | 49.5% |

**Result:** `DIAGNOSTIC_ONLY`. The median selected thesis reaches a -5 bps close within four
minutes, and endpoint direction is approximately coin-flip. This supports strict abstention;
it does not establish a profitable exit clock.

## Test 5 - Adverse/Favorable Position Management

**Question:** At a 3m checkpoint for a 15m trade or 5m checkpoint for a 30m trade, can a causal
model choose HOLD, EXIT or REVERSE better than a static policy selected on calibration data?

**Cost rule:** HOLD pays one 12 bps round trip. REVERSE pays two. The comparison policy was
selected before the final test; there is no per-row hindsight oracle.

| Horizon | State | Calls | Selected static | Policy day-equal net bps/trade | Policy LCB | Policy minus static | Lift LCB |
|---:|---|---:|---|---:|---:|---:|---:|
| 15m | Adverse | 4,337 | HOLD | -21.17 | -24.41 | +0.15 | -1.08 |
| 15m | Favorable | 4,333 | HOLD | -2.66 | -5.36 | -0.67 | -2.37 |
| 30m | Adverse | 3,876 | HOLD | -24.69 | -28.65 | +1.00 | -2.12 |
| 30m | Favorable | 3,775 | HOLD | -0.22 | -4.65 | +2.73 | -1.06 |

**Result:** `FAIL_NO_EDGE`. Some policy lifts are positive point estimates, but every lift lower
bound is negative and every policy net lower bound is negative. Dynamic management is not
promotable.

## Test 6 - Breakout Continuation Versus Failure

**Question:** After price breaks at least 2 bps beyond the previous 60-minute range, can a model
select profitable continuation trades?

| Horizon | Test breakouts | Continuation AUC | Top-decile calls | Day-equal net bps/trade | Net LCB |
|---:|---:|---:|---:|---:|---:|
| 5m | 5,852 | 0.510 | 622 | -13.91 | -16.66 |
| 15m | 5,852 | 0.521 | 521 | -9.58 | -14.75 |
| 30m | 5,852 | 0.512 | 501 | -16.70 | -22.22 |

**Result:** `FAIL_NO_EDGE`. Continuation prediction is near random and all selected trades lose
after costs.

## Test 7 - Minute Spot/Perpetual Leadership

**Question:** Does an isolated one-minute spot or perpetual shock predict follower catch-up in
the next minute?

**Observed:** Spot and perpetual one-minute returns have correlation `0.99197`. Under the
predeclared rule requiring a top-5% leader shock while the follower moved no more than half as
much, the test found zero isolated events.

**Result:** `INSUFFICIENT_RESOLUTION`, not a negative alpha result. One-minute bars aggregate
both legs into the same timestamp and cannot establish which venue moved first. Sub-second
aligned prices are required.

## Test 8 - Questions Correctly Blocked by Missing Data

| Question | Missing causal input |
|---|---|
| BTC/Polymarket repricing, stale quotes, edge half-life | synchronized 50ms-1s quotes on both venues |
| Maker/taker/wait, fill markout, cancel toxicity | order acknowledgements, queue position, fills and post-fill markouts |
| Liquidation continuation/exhaustion | historical liquidation events aligned to the matrix |
| Price/OI/funding positioning states | open-interest history and actual funding payment ledger |
| Multi-venue information leadership | aligned Binance, Coinbase, Bybit and Polymarket event-time prices |
| Portfolio allocation and capacity | two independent positive-EV strategies plus executable depth/fills |

**Result:** `BLOCKED_DATA` or `BLOCKED_EVIDENCE`. These tests were not replaced with candle
proxies because doing so would answer a different question.

## Coverage of the Briefs' Top Questions

This matrix distinguishes a new result from a previously completed test and a genuine data
blocker. It prevents a long idea list from being mistaken for uncompleted executable research.

| Priority question | Evidence status |
|---|---|
| Is there enough movement to trade? | Tested here and previously: movement AUC is real, but direction does not clear 12 bps |
| Direct after-cost EV of LONG/SHORT/FLAT | Tested here: no positive lower bound |
| Expected MFE/MAE and path | Previously tested in `MFE_MAE_DISTRIBUTION_V1`; descriptive, not an executable edge |
| How long will the thesis survive? | Tested here: median first -5 bps close is 4m |
| Enter now or wait? | Tested here: all fixed-delay intervals cross zero |
| Adverse trade: hold/reduce/exit/reverse? | Tested here for HOLD/EXIT/REVERSE: no positive policy or lift lower bound |
| Favorable trade: hold/trail/reduce/exit? | Tested here for HOLD/EXIT/REVERSE and previously for giveback: no promotion |
| Breakout continuation or failure? | Tested here: AUC 0.510-0.521 and negative net value |
| Trend beginning/mature/exhausted? | Previously tested in `TREND_SURVIVAL_HAZARD_V1`: no edge after cost |
| Liquidation continuing or exhausted? | Blocked: aligned liquidation history is absent |
| Price + OI + funding state | Blocked: OI history and funding cash-flow ledger are absent |
| Which venue leads price discovery? | One-minute test here is insufficient; sub-second synchronized data is absent |
| Can liquidity provision beat taker costs? | `MAKER_MARKOUT_SURFACE_V1` remains partial because fills/queue are absent |
| Can Polymarket be hedged cheaply on Binance? | `HEDGED_POLY_MM_V1` remains an upper bound without actual fills |
| Is another trade better than holding? | Blocked by evidence: zero strategies currently have positive executable EV |

The first brief's other high priorities are also accounted for: Polymarket disagreement lost to
the market in `MARKET_DISAGREEMENT_RESOLUTION_V1`; the state atlas was underpowered; the stale
quote test had no effect with the available resolution; full-set parity was real but negligible;
and maker/taker counterfactuals remain blocked by fill and queue data.

## Final Verdict

| Measure | Result |
|---|---:|
| New runnable test families | 7 |
| Explicit blocked families | 6 |
| Promotable configurations | **0** |
| Capital authority | **false** |

The strongest repeatable observation remains movement prediction, not direction or executable
profit. The 5m movement classifier reached AUC 0.770, but selected directional trades lost
approximately the full 12 bps transaction cost. No additional model or exit layer in this batch
converted that information into positive after-cost value.

Nothing from this batch should be wired into serving or assigned capital. The next valid work is
forward capture of event-time cross-venue quotes and actual maker fill/markout data.

## Audit Note

An earlier immutable output,
`action_value_brief_batch_20260813T070733Z.json`, was generated before review. Review found that
its position-policy comparison used a per-row hindsight maximum and that zero leadership events
were not classified as a resolution failure. The runner was corrected and rerun. The earlier
file remains preserved for audit, but only the `20260813T071004Z` result is valid.
