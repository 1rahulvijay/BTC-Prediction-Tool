# Conditional EV 120-Day Results

Date: 2026-07-27

Run: `data/research/conditional_ev_120d/20260727T193951Z`

Status: **COMPLETED - REJECTED FOR DEPLOYMENT**

This was the untouched execution of the frozen design in
`CONDITIONAL_EV_120D_EXPERIMENT_2026-07-27.md`. No thresholds were changed
after results were observed. No serving model, paper policy, or live decision
path was modified.

## Data And Validation

```text
Source rows                 172,801 one-minute rows
Source range                2026-03-26 23:59 UTC to 2026-07-24 23:59 UTC
Non-one-minute gap rate     0.00000%
Source SHA-256              7643e42e502bc40937b28432d989b17bd2a9bfa7caf09246615b558a016f8274
Validation                  four expanding 15-day test folds
Purge                       one full forecast horizon
Independent OOF decisions  17,279 at 5m; 5,759 at 15m
Round-trip cost             12 bps
Runtime                     529.6 seconds
Skipped model fits          none
Quantile crossings          none
```

Every model family was trained sequentially and released before the next fit.
The outputs contain 23,038 strictly out-of-fold, horizon-spaced decisions.

## Forecast Results

| Layer | 5m | 15m | Interpretation |
|---|---:|---:|---|
| Magnitude AUC | 0.751 | 0.701 | Useful ranking signal for whether a move clears costs |
| Magnitude average precision | 0.473 | 0.636 | Better than the 0.233 / 0.440 event rates |
| Magnitude Brier | 0.152 | 0.217 | Reasonably calibrated probability forecasts |
| Magnitude ECE, 10 bins | 0.011 | 0.019 | Small aggregate calibration error |
| Conditional-direction AUC | 0.517 | 0.515 | Effectively coin-flip |
| Conditional-direction accuracy | 51.18% | 50.65% | Not an economic direction edge |
| Mean-return Spearman | 0.018 | 0.002 | No useful signed-return ranking |
| Mean-return direction accuracy | 51.07% | 49.77% | Coin-flip |
| Mean-return R-squared | -0.009 | -0.018 | Worse than predicting the test-fold mean |
| q10 empirical coverage | 11.00% | 11.55% | Close to the requested 10% |
| q50 empirical coverage | 50.69% | 51.36% | Close to the requested 50% |
| q90 empirical coverage | 89.46% | 89.39% | Close to the requested 90% |

The strongest individual magnitude models reached AUC 0.751 at 5m and 0.701
at 15m. No individual conditional-direction model solved the direction
problem: the six model-family AUC ranges were 0.502-0.528 at 5m and
0.509-0.517 at 15m.

Magnitude probability was also stable across probability deciles. For
example, the highest decile predicted/observed move rates were 55.45%/57.87%
at 5m and 71.32%/75.17% at 15m. This supports using magnitude as risk and
round-state information, not as a directional trade.

## Frozen Policy Result

The primary policy required all of:

```text
P(move clears costs) >= 0.50
conditional direction confidence >= 0.55
LONG q10 > 12 bps, or SHORT -q90 > 12 bps
```

Gate counts:

| Gate | 5m | 15m |
|---|---:|---:|
| P(move) passed | 1,388 | 1,930 |
| Both probability gates passed | 743 | 920 |
| Adverse quantile passed | 0 | 0 |
| Primary actions | 0 | 0 |

The quantile models correctly recognized that the adverse tail remained
larger than the trading cost. The 5m q10 range was -48.59 to -3.19 bps and
the q90 range was +2.26 to +80.16 bps. At 15m they were -71.71 to -3.79 bps
and +5.16 to +115.02 bps. Therefore no prediction had a conservative
post-cost lower bound.

The zero-trade result is the intended fail-closed behavior. It is not missing
data and it must not be changed by lowering the frozen thresholds.

## Economic Diagnostics

| Policy | 5m trades | 5m mean net | 5m PF | 15m trades | 15m mean net | 15m PF |
|---|---:|---:|---:|---:|---:|---:|
| Always predicted direction | 17,279 | -11.90 bps | 0.092 | 5,759 | -12.25 bps | 0.209 |
| Frozen primary q10/q90 | 0 | n/a | n/a | 0 | n/a | n/a |
| Median diagnostic | 6 | +9.16 bps | 1.557 | 54 | -3.61 bps | 0.814 |
| Mean diagnostic | 41 | -8.75 bps | 0.621 | 64 | -17.15 bps | 0.403 |

Passing only the two probability gates did not help:

```text
5m   743 decisions, 51.14% direction accuracy, -0.32 bps gross
15m  920 decisions, 50.65% direction accuracy, -1.13 bps gross
```

The six positive 5m median-diagnostic trades are not evidence. They represent
0.035% coverage, have no valid lower bound, disappear in the final fold, and
miss the 200-trade minimum by more than 30 times. The result remains rejected.

## Promotion Decision

Both horizons failed every predeclared promotion requirement:

```text
positive mean net value               FAIL
positive day-block lower bound        FAIL
profit factor above 1.10              FAIL
at least 200 independent trades       FAIL
at least 1% coverage                   FAIL
at least three positive folds         FAIL
positive final fold                   FAIL
positive 50%-higher-slippage stress   FAIL
```

No model artifact was saved for serving and no live or paper-trading behavior
was changed.

## What Was Learned

1. The existing causal features can estimate movement magnitude and volatility.
2. They still do not identify the sign of the move after costs.
3. Well-calibrated return quantiles are valuable because they expose adverse
   risk and prevent false precision.
4. Adding more tree models to the same feature set is unlikely to break the
   direction ceiling.
5. A new directional experiment is justified only by genuinely new causal
   information, such as independently validated execution-book dynamics or
   exchange lead-lag data. It must use a new frozen protocol.

Magnitude and quantile forecasts may remain informational or risk-control
inputs. They are not authorized as LONG/SHORT triggers. Dynamic exit remains
closed, and real orders remain unavailable.
