# VOLATILITY_EXPANSION_V1

**Verdict: REAL BUT NOT SUFFICIENT.** Filtering moves 5m from *impossible* to *needs 77–89%
directional accuracy*. That is a genuine improvement to an arithmetic wall, and still well
beyond anything the app's heads produce.

Also: **~all of the signal is already in `rv_15m`.** The gradient-boosted model beats plain
realized volatility by 0.017 AUC.

Run 2026-08-13 · `research_lanes/run_matrix_lanes.py`

---

## Question

`BINANCE_COST_CLEARANCE_V1` showed only ~23% of 5-minute windows move more than the 12 bps
round trip. So the useful question is not direction:

> Can we predict **which** windows will move enough to be worth trading?

## Method

| | |
|---|---|
| target | `\|5m forward move\| > 12 bps` |
| features | `rv_15m, rv_30m, rv_60m, compression_ratio, shock_magnitude, vpin_15m` — all backward-looking |
| model | HistGradientBoosting, 200 iters |
| split | by UTC day, 70/30, purged by the 5-bar horizon at the cut |
| train / test | 251 / 109 independent days |
| bound | day-block bootstrap, 300–400 draws |

## Result

| | |
|---|---:|
| test AUC | **0.765** |
| baseline: `rv_15m` alone | **0.748** |
| **incremental over baseline** | **+0.017** |
| base rate (windows clearing 12bps) | 18.6% |
| top-decile hit rate | 51.9% |
| **top-decile lift** | **2.80x** |

The ranking works — the top decile clears costs 2.8x as often as average. But the model earns
almost none of that. A single backward-looking volatility column gets 0.748 of the 0.765. The
honest reading is **use `rv_15m`; do not build a model** unless the 0.017 survives a cost-aware
evaluation, which it has not been given.

## The question that matters: does filtering rescue cost clearance?

Mean forward |move| and the resulting break-even directional accuracy at 12 bps, by how
aggressively the model filters:

| selection | n | mean \|move\| | LCB | break-even accuracy @12bps |
|---|---:|---:|---:|---:|
| all test rows | 156,955 | 7.5 | 7.0 | **impossible** |
| top 50% by p | 78,478 | 10.3 | 9.7 | **impossible** |
| top 20% by p | 31,391 | 13.9 | 12.8 | 96.7% |
| top 10% by p | 15,696 | 16.7 | 15.3 | 89.3% |
| top 5% by p | 7,848 | 19.3 | 17.4 | 84.5% |
| top 1% by p | 1,570 | 25.5 | 21.9 | **77.4%** |

This is the useful finding. Unconditionally, 5m is impossible at any accuracy. Filter to the
top 1% of predicted-volatility windows and it becomes *possible* — requiring 77.4% directional
accuracy.

**77.4% is not reachable here.** The app's directional keepers report AUC 0.72–0.78, and AUC is
not accuracy; measured directional base rates in this matrix sit at 49.5–50.1%. The gap between
what filtering demands and what the direction models deliver is very large.

Filtering also costs opportunity: the top 1% is 1,570 of 156,955 windows — about 14 tradeable
minutes per day.

## What would change the verdict

- **Lower costs.** The whole table is a function of the 12 bps round trip. At maker execution
  the numbers move a lot; this lane assumes taker.
- **Asymmetric payoffs.** Break-even assumes you eat the full move when wrong. A tight stop
  changes it, and is untested.
- **A direction model that works only in high-vol states.** Plausible in principle — nobody has
  shown it. That is the test this lane hands off.

## Attacks applied

| attack | status |
|---|---|
| day-block bootstrap | yes — 109 independent test days |
| purged split | yes — horizon-purged at the day cut |
| beat a trivial baseline | yes — `rv_15m`, and it barely does |
| conservative bound | yes — break-even from mean LCB |
| cost stress | partial — 12 bps only, unlike the cost-clearance lane |
| regime breakdown | **not done** |
| second-era replication | **not done** |

## Caveat

Matrix is 360 days (rebuilt to 360 by `BTC_AutoFinetune` mid-retrain), not the 1000 the app
requests. 109 independent test days is a real sample; re-run after the 900-day build.
