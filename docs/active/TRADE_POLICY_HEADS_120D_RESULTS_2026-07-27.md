# 120-Day LONG, SHORT, and ACT/SKIP Results

Date: 2026-07-27  
Run: `data/research/trade_policy_heads_120d/20260727T192227Z`  
Status: **COMPLETE - NO PROMOTION**

## Experiment

The isolated trade-policy lane used:

```text
Source rows                  172,801 contiguous 1-minute observations
Source range                 2026-03-26 23:59 UTC -> 2026-07-24 23:59 UTC
Horizons                     5m and 15m
Features                     80 causal features
Validation                   four purged expanding-window folds
Economic observations       non-overlapping horizon-aligned decisions
Fee assumption               5 bps per side
Slippage assumption          1 bps per side
Round-trip cost              12 bps
Base families                LogReg, HistGB, ExtraTrees, XGB, LGBM, CatBoost
ACT/SKIP families            LogReg and HistGB
Frozen ACT threshold         0.58
Elapsed time                 348.2 seconds
Skipped models               none
Saved research artifacts     28
```

The first fold generated seed out-of-fold predictions. Every later ACT/SKIP
prediction was trained only on earlier out-of-fold candidate records.

## Side-Head Results

### Five minutes

| Target | Best pooled model | AUC | Average precision | Brier | ECE |
|---|---|---:|---:|---:|---:|
| LONG profitable | ExtraTrees | 0.7190 | 0.2374 | 0.0941 | 0.0049 |
| SHORT profitable | Mean ensemble | 0.7132 | 0.2366 | 0.0990 | 0.0064 |

### Fifteen minutes

| Target | Best pooled model | AUC | Average precision | Brier | ECE |
|---|---|---:|---:|---:|---:|
| LONG profitable | Mean ensemble | 0.6457 | 0.3098 | 0.1605 | 0.0090 |
| SHORT profitable | ExtraTrees | 0.6398 | 0.3190 | 0.1688 | 0.0173 |

These AUCs show useful ranking of the *probability that a chosen side clears
costs*. They do not prove that selecting the higher LONG/SHORT probability
produces positive expected value.

## Candidate And ACT/SKIP Results

| Horizon | Candidate direction accuracy | Always-ACT win rate | Always-ACT mean net | ACT model | ACT AUC | Trades | Mean net | Profit factor |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| 5m | 50.07% | 11.88% | -11.96 bps | Logistic | 0.6921 | 7 | -26.42 bps | 0.139 |
| 5m | 50.07% | 11.88% | -11.96 bps | HistGB | 0.6856 | 10 | -48.47 bps | 0.031 |
| 15m | 51.07% | 22.69% | -12.10 bps | Logistic | 0.6241 | 9 | -31.16 bps | 0.104 |
| 15m | 51.07% | 22.69% | -12.10 bps | HistGB | 0.5939 | 25 | -17.14 bps | 0.306 |

The ACT filters correctly rejected more than 99% of candidates, but the few
accepted trades still lost after costs. Coverage was 0.05%-0.58%, too small for
a stable estimate, and every retained policy had profit factor below one.

## High-Score Bucket Audit

Selecting only the highest base-ensemble candidate scores did not repair the
economics:

| Horizon | Bucket | Win rate | Mean net | Profit factor |
|---|---|---:|---:|---:|
| 5m | Top 1% | 37.79% | -9.06 bps | 0.522 |
| 5m | Top 5% | 30.94% | -13.19 bps | 0.298 |
| 15m | Top 1% | 42.11% | -12.78 bps | 0.491 |
| 15m | Top 5% | 39.72% | -13.18 bps | 0.456 |

This audit matters because it prevents a misleading conclusion from the
classification AUC. The models can rank event probability while still failing
to rank post-cost expected value.

## Fold Stability

Always-ACT mean net remained negative in every fold:

```text
5m:  -11.95, -11.94, -12.10, -11.85 bps
15m: -12.30, -12.39, -11.94, -11.79 bps
```

The final fold also had the weakest candidate win rates:

```text
5m final fold   7.52%
15m final fold 16.54%
```

No positive result is being hidden by pooling.

## Verdict

```text
LONG specialist ranking        RESEARCH SIGNAL, NOT ECONOMIC EDGE
SHORT specialist ranking       RESEARCH SIGNAL, NOT ECONOMIC EDGE
ACT/SKIP policy                FAILS POST-COST PROMOTION
Dynamic exit                   NOT TRAINED; FROZEN CLOSURE REMAINS
Live integration               REFUSED
Real-money authorization       NONE
```

Do not lower the ACT threshold after seeing these results merely to create more
trades. The post-cost evidence says the available feature set does not select a
profitable LONG/SHORT policy at these horizons. The 28 saved bundles remain
research-only shadow artifacts.

## Artifacts

```text
manifest.json
metrics.csv
summary.csv
oof_predictions.csv
oof_predictions.parquet
models/*.joblib
run.log
```

The manifest records the exact source hash, assumptions, selected features and
model inventory. No live production artifact was replaced.
