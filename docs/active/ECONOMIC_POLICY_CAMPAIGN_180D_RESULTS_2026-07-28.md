# Economic Policy Campaign 180-Day Results

Date: 2026-07-28

Canonical run: `data/research/economic_policy_campaign_180d/20260727T201350Z`

Status: **COMPLETE - NO HISTORICAL SHADOW CANDIDATE**

The campaign executed the frozen protocol in
`ECONOMIC_POLICY_CAMPAIGN_180D_PROTOCOL_2026-07-28.md`. No threshold or model
family was changed after selection or locked-test results were observed.

## Data And Reproducibility

```text
Rows                         259,200 gap-free one-minute observations
Period                       2025-07-30 through 2026-01-25 UTC
Base training                120 days
ACT/SKIP training            15 days
Policy selection             15 days
Locked historical test       30 days
Locked decisions             8,639 at 5m; 2,879 at 15m
Features                     80 causal features
Round-trip cost              12 bps
Policy configurations        411 per horizon; 822 total
Skipped model fits           none
Runtime                      266.9 seconds
Serving artifacts changed    none
```

The first complete run exposed an audit-schema defect: the combined prediction
file did not retain its horizon column. Scoring and manifests were already
horizon-separated, but the ambiguous audit file was rejected. Commit
`9f718da` added the identity column and the entire frozen run was repeated.
Both economic results reproduced exactly:

```text
5m   same policy, 137 trades, -13.0000217 bps/trade
15m  same policy, 43 trades,  -13.5292731 bps/trade
```

The corrected Parquet contains 8,639 rows labelled `5` and 2,879 labelled
`15`.

## Direct Economic LONG And SHORT Heads

Best locked-test classifier results:

| Horizon | Target | Best seat | AUC | Average precision | Brier |
|---|---|---|---:|---:|---:|
| 5m | LONG profitable | Mean ensemble | 0.7781 | 0.1973 | 0.0632 |
| 5m | SHORT profitable | Mean ensemble | 0.7620 | 0.1925 | 0.0671 |
| 15m | LONG profitable | ExtraTrees | 0.7166 | 0.3023 | 0.1241 |
| 15m | SHORT profitable | Mean ensemble | 0.7150 | 0.2850 | 0.1247 |

All six individual families and the ensemble are recorded in
`locked_model_diagnostics.csv`. The AUCs confirm that movement intensity and
profitable-event probability are rankable. They do not establish signed
expected value.

## Expected-Net And q20 Heads

The expected-net regressors did not predict signed payoff:

```text
5m best Spearman       +0.0157
15m best Spearman      +0.0322
R-squared              negative for every family and both sides
```

The q20 models were calibrated near the requested 20% tail:

```text
5m empirical coverage   18.3%-19.1%
15m empirical coverage  18.2%-19.1%
```

However, every maximum predicted q20 net return remained negative:

```text
5m LONG max q20    -11.24 bps
5m SHORT max q20   -12.72 bps
15m LONG max q20   -11.22 bps
15m SHORT max q20  -13.66 bps
```

Therefore no q20 policy could claim a conservative post-cost edge.

## ACT/SKIP Heads

Locked-test ACT/SKIP diagnostics:

| Horizon | Model | AUC | Average precision | Brier | Profitable-candidate base rate |
|---|---|---:|---:|---:|---:|
| 5m | Logistic Regression | 0.7467 | 0.1837 | 0.0645 | 7.29% |
| 5m | HistGB | 0.7317 | 0.1700 | 0.0654 | 7.29% |
| 15m | Logistic Regression | 0.6910 | 0.2917 | 0.1293 | 16.19% |
| 15m | HistGB | 0.6107 | 0.2448 | 0.1361 | 16.19% |

These AUCs did not translate into an executable positive-EV threshold. Across
the complete selection catalog:

```text
5m selection passes    0 / 411
15m selection passes   0 / 411
```

At 5m, only direct probability policies had enough coverage; the best still
lost 8.73 bps per trade in selection. At 15m, the strongest apparent
probability cell earned +2.03 bps over only 34 trades, but its day-block lower
bound was -5.88 bps, so it failed before the locked test.

## Locked-Test Policies

### Five Minutes

```text
Policy                XGBoost direct probability, SHORT only
Threshold             0.30
Trades                137
Coverage              1.59%
Win rate              23.36%
Mean net              -13.00 bps
Day-block interval    [-17.53, -9.95] bps
Profit factor         0.271
Positive weeks        0 / 4
BH q-value            1.00
```

### Fifteen Minutes

```text
Policy                Logistic direct probability, SHORT only
Threshold / gap       0.40 / 0.05
Trades                43
Coverage              1.49%
Win rate              27.91%
Mean net              -13.53 bps
Day-block interval    [-24.08, -5.64] bps
Profit factor         0.299
Positive weeks        1 / 4
BH q-value            1.00
```

Both policies failed mean value, lower bound, profit factor, weekly stability,
slippage stress and BH gates. The 15m policy also failed minimum sample size.

## Dynamic Exit Versus HOLD

The fixed dynamic-exit challenger was evaluated on the identical selected
entries:

| Horizon | Entries | Early exits | Dynamic mean net | Mean improvement vs HOLD | Paired day LB | Verdict |
|---|---:|---:|---:|---:|---:|---|
| 5m | 137 | 46.72% | -12.63 bps | +0.37 bps | -1.45 bps | HOLD wins |
| 15m | 43 | 41.86% | -11.54 bps | +1.99 bps | -1.31 bps | HOLD wins |

Dynamic exit slightly reduced the loss point estimate, but remained absolutely
unprofitable and did not beat HOLD statistically. It cannot rescue a failed
entry policy.

## Final Decision

```text
Directional LONG/SHORT ensemble    already present; no new seat required
Economic LONG specialist           trained and rejected
Economic SHORT specialist          trained and rejected
ACT/SKIP Logistic                  trained and rejected
ACT/SKIP HistGB                    trained and rejected
Expected-net specialists           trained and rejected
q20 conservative specialists       trained; correctly abstained
Dynamic exit challenger            tested; failed to beat HOLD
Historical shadow candidate        none
Live/paper integration             refused
Real-order authorization           none
```

This closes further threshold/model-family searching on the same historical
matrix. The direct economic heads have now failed in the recent 120-day lane,
the conditional-EV lane, and this separate older 180-day era. Continuing until
one backtest happens to pass would be multiple-testing overfit.

The next admissible ACT/SKIP experiment requires at least 500 independently
resolved paper candidates across eight weeks, with actual candidate-to-fill
expiry, spread, slippage and regime state. A new historical direction test is
justified only by genuinely new causal data, not another model family.
