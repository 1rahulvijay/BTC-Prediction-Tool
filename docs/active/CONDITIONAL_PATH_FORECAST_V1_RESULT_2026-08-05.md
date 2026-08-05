# RESULT — CONDITIONAL_PATH_FORECAST_V1 (15m and 12m)

**`2026-08-05`.** `research/conditional_path_forecast_v1.py`. 200,000 1m bars, 13,317 15m rounds
and 16,646 12m rounds, temporal 70/30 split with a one-round purge gap.

## Verdict

**The forecast lattice adds nothing beyond geometry, at both round lengths.**

```text
15m   AUC improvement (ML vs structural baseline):  -0.0071
      Brier improvement:                            -1.37%
12m   AUC improvement (ML vs structural baseline):  -0.0060
      Brier improvement:                            -1.03%
```

Both negative. The model is slightly **worse** than a closed-form baseline with no parameters.

## The number that looks like a finding, and is not

Forecast skill for the settlement checkpoint rises steeply as the round runs out:

```text
15m target, Full ML AUC:   0.500 -> 0.592 -> 0.674 -> 0.723 -> 0.789 -> 0.873
                          (obs 0     1       3       5       7       10)
```

Read alone, that is a spectacular result — 0.87 AUC on BTC direction. It is not one. Printed
beside the structural baseline:

```text
15m target      obs=0   obs=1   obs=3   obs=5   obs=7   obs=10
structural      0.500   0.613   0.683   0.736   0.797   0.877
Full ML         0.500   0.592   0.674   0.723   0.789   0.873
ML - structural +0.000  -0.020  -0.010  -0.014  -0.009  -0.004
```

```text
12m target      obs=0   obs=1   obs=3   obs=5   obs=7   obs=9
structural      0.500   0.633   0.729   0.791   0.855   0.914
Full ML         0.507   0.620   0.720   0.786   0.853   0.913
ML - structural +0.007  -0.013  -0.009  -0.004  -0.002  -0.001
```

**Every point of that rise belongs to the baseline**, which is:

```text
z      = (price_now - anchor) / (sigma_1m * sqrt(minutes_remaining))
P_base = Phi(z)
```

A driftless random walk. No training, no features, no data beyond the current price and a
volatility estimate. Being far above the anchor with one minute left means you settle up —
that is arithmetic, and a model does not need to learn it.

The revision table originally printed only the ML row. That is why the baseline row and the
`ML - structural` gap were added: the rise is real and it is worthless, and the output has to
make both true at once.

## Where the model is genuinely, marginally ahead

Only at `obs=0`, where the baseline is exactly 0.500 by construction (price sits on the anchor,
so `z=0`):

```text
15m: +0.015, +0.027, +0.013, +0.006, +0.000   (targets 3, 5, 7, 10, 15)
12m: +0.018, +0.012, +0.002, +0.006, +0.007
```

Best cell is **0.527** — the 5-minute checkpoint from a standing start. That is consistent with
this repository's established ceiling (13 model families, 0.50–0.535 on 5m/15m direction) and
is not new information. It also decays to nothing by the settlement checkpoint, which is the
only checkpoint a Polymarket round pays out on.

## The two questions do separate — the split was worth building

`anchor_up` and `local_up` genuinely disagree, and the selftest pins the case:

```text
price recovering from below the anchor but not yet across it
   anchor-relative: DOWN
   local:           UP
```

So the concept's two-question framing is sound. It is the *predictability* claim that fails,
not the *representation*.

## 12m vs 15m

The shorter round is uniformly **easier for the baseline** and no better for the model:

```text
settlement AUC at the last observation:   15m 0.877   12m 0.914  (structural)
```

Expected: less remaining time means less remaining volatility means a sharper `z`. It is
geometry again, not a reason to prefer 12m rounds for modelling. 12m also yields ~25% more
rounds from the same bars, which helps sample size and nothing else.

## What was NOT tested

**The live Polymarket price as a third baseline.** The concept doc names three baselines —
distance-to-anchor, remaining volatility, and the current market probability. This offline
matrix has the first two. Beating geometry is necessary but not sufficient: a model must beat
the *market's* price to be tradeable, and that comparison has not been run. Since the model
fails the easier test, the harder one was not attempted.

Also untested: MFE/MAE heads, reversal probability, path-family conditioning as a *predictor*
(path families are computed and reported, but only as a descriptive distribution), and the
ENTER/HOLD/EXIT/FLIP policy layer. All of Phases 3–5 in the proposal are downstream of a
Phase-2 result that did not arrive.

## Implication for the concept

The proposal's own kill criterion is the right one and it fires:

> Does the updated forecast at minute 3, 5, 7 or 10 add information beyond current distance to
> anchor and remaining volatility? If it does not beat those baselines, the extra model is not
> finding alpha.

It does not. On this data the correct reading is:

1. **Keep the structural baseline.** `Phi(z)` is a genuinely good late-round probability and
   costs nothing to compute. If anything in the app currently predicts late-round settlement
   with a model, it should be compared against this first.
2. **Do not build the lattice model.** Not at this data scale, not on these features.
3. The remaining live question is whether `Phi(z)` beats the *Polymarket price* late in a
   round. That is a different and much cheaper experiment: it needs recorded book snapshots,
   not a new model — and it is the one the recorder work has been unblocking.

## Reproduce

```bash
python research/conditional_path_forecast_v1.py --selftest
python research/conditional_path_forecast_v1.py --rounds 15 12 --rows 200000
```

78 selftest checks, including that the 12m lattice never observes at or after its settlement
minute, that a malformed lattice is refused rather than silently forecasting the past, and that
switching round length leaves nothing behind on the previous setting.
