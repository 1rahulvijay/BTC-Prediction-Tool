# Crossing heads — result

**Protocol** `PREREG_CROSSING_HEADS_V1.md` sha256 `762532c9…`, frozen before training ·
**Script** `research/crossing_heads_v1.py` · Scored **once**

```
15,428 labelled crossings over 5,738 rounds    2026-07-05 -> 2026-07-25
split by DAY at 2026-07-19    market features joined from the last CLOSED 1m bar
market-feature coverage 100.0%    incumbent baseline: seconds_left ALONE
```

## Result — all three targets add over the clock

| target | base rate | clock (incumbent) | candidate | gain | 95% CI on gain | null floor |
|---|---:|---:|---:|---:|---|---|
| `is_final_crossing` | 37.1% | 0.6755 | **0.7144** | **+0.0389** | [+0.0243, +0.0556] | ≤0.6102 |
| `reverted_30s` | 18.0% | 0.5196 | **0.6715** | **+0.1519** | [+0.1175, +0.1840] | ≤0.5912 |
| `reverted_60s` | 29.6% | 0.5061 | **0.6373** | **+0.1312** | [+0.1121, +0.1517] | ≤0.5664 |

**`CROSSING_HEAD_ADDS` on all three.** Every gain clears the 0.02 materiality bar declared in
advance, every interval excludes zero, and every candidate sits well above its null floor.

This is the strongest positive result in this research programme by a wide margin.

## The reversion result is the important one

The protocol predicted the clock would do most of the work on `is_final_crossing`, and it does —
0.6755 from `seconds_left` alone. The candidate still adds +0.039, but that target is
substantially mechanical.

**On reversion, the clock is worth almost nothing:**

```
reverted_30s   clock 0.5196   candidate 0.6715   the clock is barely above chance
reverted_60s   clock 0.5061   candidate 0.6373   the clock is at chance
```

Time remaining tells you essentially *nothing* about whether a crossing will be undone in the
next 30–60 seconds. Market state tells you a great deal — **+0.15 and +0.13 AUC**, on intervals
nowhere near zero.

This is not a mechanical artifact and it is not a restatement of an incumbent. It is genuine
short-horizon path information, on a target that is not forward direction.

## Why this succeeded where direction failed

Every direction test in this programme landed at AUC 0.50–0.52. This lands at 0.64–0.71. The
difference is the **target**, not the model or the features — several of these features already
appeared in the direction tests and contributed nothing there.

```
"will price go up or down"                 unpredictable      AUC 0.518
"will THIS crossing be undone in 30s"      predictable        AUC 0.672
```

Predicting unconditional direction asks the market a question it has already priced. Predicting
whether a *specific observed event* reverts asks a conditional path question, on a population
selected by something that just happened. That is exactly the class the strategy notes argued
for — path, tradability and crossing rather than direction — and it is the first time in this
repository that the argument has been supported by a measurement.

## What it does not establish

**A crossing probability is an input to a decision, not a decision.** No position follows from
it, and every action lane measured here is closed on cost:

```
Polymarket taker   ~149 bps floor; only 0.1% of 15m windows move that far
Binance taker       14 bps against a 0.97-1.97 bps gross edge
Binance maker        2 bps round trip, and fills lose 1.53 bps to adverse selection
```

An AUC of 0.67 on reversion does not create an opportunity by itself. It would have to improve
the post-cost value of a *specific action* by more than the cost of taking it, and that is a
different measurement with a different preregistration.

The honest statement is: **this is the first head worth having, and there is still nothing to
spend it on.**

## Limits

- **21 days, one venue, one market type.** 4,326–4,668 test rows over 7 test days.
- **The 5s and 15s horizons are absent**, and excluded in advance: `round_state_snapshots`
  samples every ~15 seconds, so those labels are essentially unresolvable (6 cases at 15s, none
  at 5s). Finer-cadence forward collection is the only route to them, and short horizons are
  where an executable edge would most plausibly live.
- **Not calibrated here.** AUC is discrimination; using a probability in an expected-value
  calculation requires calibration, which this protocol did not measure.
- **Not forward evidence.** This is a historical split, scored once. It is a prior for a forward
  study, not a substitute for one.

## Governance

- Protocol frozen and hashed before training; 21/21 hashes verify in CI.
- **The incumbent is `seconds_left` alone**, not a constant — the direct lesson from
  `REGIME_VOLATILITY_CONTROL_V1`, where 84% of an apparent effect was current volatility. The
  comparison is nested: the baseline feature is inside the candidate set.
- **Split by DAY, not by row.** Crossings within one round are not independent; a row split
  would put the same round on both sides.
- **Market state is joined from the last CLOSED 1-minute bar.** The selftest asserts a
  mid-minute crossing uses the *previous* bar and demonstrates that the old containing-bar rule
  fails the same assertion — the defect fixed earlier in `train_round_state_heads`.
- **Null floor per target**, labels shuffled in whole-day blocks, 200 replications.
- Selftest: 14 checks, including that a model compared against itself yields a zero-width
  difference interval.

## What follows

The measured base rates are usable priors:

```
P(crossing is final)          37.2%
P(reverts within 30s)         18.7%
P(reverts within 60s)         30.1%
```

The next question is not another head. It is whether these probabilities change the value of any
action that is currently affordable — and on present evidence, none is. The binding constraint
remains cost, not prediction.
