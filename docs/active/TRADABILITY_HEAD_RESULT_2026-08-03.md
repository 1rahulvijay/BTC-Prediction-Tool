# Tradability head V1 — result

**Protocol** `PREREG_TRADABILITY_HEAD_V1.md` sha256 `bb48c577…`, frozen before training ·
**Script** `research/tradability_head_v1.py` · Scored **once**

```
518,366 bars   train 2025-08-05 -> 2026-04-13   purge 60   test -> 2026-07-30 (155,450 bars)
23 frozen backward-looking features   incumbent baseline: rv_60m alone
```

## Two hurdles, two different answers

### Binance hurdle, 14 bps — `TRADABILITY_HEAD_ADDS`

| model | top-decile hit | AUC |
|---|---:|---:|
| BASELINE_CONSTANT | 33.9% | – |
| BASELINE_VOLATILITY (`rv_60m`) | 64.3% | 0.700 |
| **CANDIDATE (23 features)** | **67.0%** | 0.709 |

```
gain over incumbent  +2.67 points    day-block 95% CI [+1.79, +4.08]
```

Above the 2.0-point materiality bar declared in advance, with the interval clear of zero.
**This is the first `_ADDS` verdict in this line of work.**

Read it in proportion. The volatility baseline delivers 64.3 of the 67.0 points — it does the
overwhelming majority of the work, exactly as the regime control would predict. What the other
22 features add is 2.67 points on top of it. That is real, measured against the right incumbent,
and small.

Note where the gain sits: AUC improves only 0.700 → 0.709, while the top decile improves 2.67
points. The advantage is concentrated at the top of the ranking, which is the only part a gate
uses. Evaluating this head on AUC alone would have understated it.

### Polymarket hurdle, 149 bps — `TRADABILITY_IS_VOLATILITY`

| model | top-decile hit | AUC |
|---|---:|---:|
| BASELINE_CONSTANT | **0.1%** | – |
| BASELINE_VOLATILITY | 0.3% | 0.809 |
| CANDIDATE | 0.3% | 0.836 |

```
gain over incumbent  +0.08 points    day-block 95% CI [-0.01, +0.18]   spans zero
```

## The Polymarket number is the important finding

**Only 0.1% of 15-minute windows move more than 149 bps.** The measured Polymarket cost floor
demands a move that happens roughly once in a thousand windows.

And gating does not rescue it. Ranked by the best available model:

```
top  5% of predicted windows  ->  0.5% clear the hurdle
top 10%                       ->  0.3%
top 25%                       ->  0.2%
top 50%                       ->  0.1%
```

Perfect selection down to the top 5% still leaves 99.5% of chosen rounds failing to clear costs.
The AUC is genuinely high (0.836 — rare events are easy to rank), and it does not matter,
because ranking a near-impossible event well still leaves it near-impossible.

This quantifies, in one number, why every Polymarket lane in this repository has failed after
costs. It is not that the models are weak. It is that the cost structure requires a move the
instrument almost never makes in the time available.

## What this head does not establish

**It predicts movement, not direction.** Phase 5 measured direction AUC 0.87 against magnitude
AUC 0.58; this head improves the magnitude side, and magnitude alone is not a position. A
15-minute window predicted to move 40 bps with an unknown sign is not an opportunity.

The +2.67 points on Binance is a **necessary** condition — it identifies windows where trading
is not automatically futile. Turning that into value requires a direction model that works
inside those windows, and the post-cost value of the combination is not measured here and is not
implied by this result.

## Governance

- Protocol frozen and hashed **before** the head was trained; 14/14 hashes verify in CI.
- The incumbent baseline is `rv_60m` alone, not a constant — the direct lesson from
  `REGIME_VOLATILITY_CONTROL_V1`, where 84% of an apparent effect turned out to be volatility.
- The 2.0-point materiality bar was declared before any result. The Polymarket arm shows why:
  its AUC (0.836) is *higher* than the Binance arm's (0.709), and it fails.
- Feature set frozen; nine forbidden columns (`future_*`, the label columns, the target itself)
  are asserted absent in the selftest rather than merely intended to be.
- The CI is bootstrapped on the **difference**, resampling whole days. Differencing two
  independent intervals would be far too wide, because both models rank the same bars.

### One bug the selftest caught

My AUC had no tie handling — tied scores received distinct ranks in arbitrary `argsort` order,
so a constant score returned something other than 0.5. Not hypothetical here: LightGBM with
`min_child_samples=200` emits identical probabilities for many rows, so ties are the normal case
in this run, not an edge case. Fixed to average ranks, with a constant-score assertion and a
heavily-tied-score assertion added. Selftest: 17 checks.

## Where this leaves the plan

```
Regime labeler        separable, but 84% volatility           -> not a router basis
Strategy Router       NOT BUILT
Tradability head      ADDS on Binance (+2.67 pts over rv_60m) -> keep, as a gate
                      IS_VOLATILITY on Polymarket             -> no gate is possible there
```

Two things follow, and they point in opposite directions for the two venues.

**Binance:** the movement gate is worth keeping. The natural next step is the conditional
direction question — does a direction model do better *inside* the windows this gate selects
than it does unconditionally? That is a new preregistration, and it is the first question in a
while with a positive prior behind it.

**Polymarket:** a movement gate cannot help. At 0.1% base rate and 0.5% at the tightest
coverage, no selection over these features reaches the cost floor. This is consistent with, and
sharpens, the taker-lane closure in `§11.4` — that result showed costs exceed the surplus; this
one shows the required move essentially never occurs.
