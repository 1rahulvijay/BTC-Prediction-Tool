# The profitability surface — does a profitable subset exist? — `2026-08-08`

The selectivity architecture — reliability models, OOD detectors, error predictors,
contradiction engines, state specialists, precision frontiers — is all machinery for
**concentrating capital into a profitable subset**. None of it creates one.

So this tests the premise before any of it gets built: **is there any state where the
market's own price is mispriced?**

**Answer on 106,053 observations across 547 settled rounds: no cell survives.**

---

## Deliberately model-free

It does not ask "where is our model right". This repository's own measurement says the model
is worse than the ask, so selecting states by model agreement would select **where its error
happens to be favourable** — which is exactly how a backtest finds alpha that does not exist.

Instead, per cell, the only question that cannot be gamed: *if you had bought this side at the
recorded ask, in this state, every time — what did it pay after the real taker fee?*

State space: `seconds remaining` × `|distance| / (σ√T)`. The second axis is scale-free, which
is what a barrier-at-expiry contract actually depends on — a $50 move at 10s and a $200 move
at 300s belong in the same cell when they are equally decisive.

## The surface (net cents/share, after fees)

```text
UP side              <0.25   0.25-.75   0.75-1.5     1.5-3        >3
  <10s                   -          -          -         -    -2.01c
  10-30s                 -      0.72c     -1.96c    -6.41c     0.54c
  30-60s            -5.44c      2.11c     -4.36c    -2.76c    -1.82c
  1-2m              -2.95c     -1.07c     -1.35c    -4.54c    -1.13c
  2-5m              -6.27c     -3.90c     -0.95c    -5.09c     0.02c
  5-15m            -11.91c    -10.56c     -2.97c    -2.46c    -0.74c

DOWN side            <0.25   0.25-.75   0.75-1.5     1.5-3        >3
  <10s                   -          -          -         -    -1.84c
  10-30s                 -     -4.17c     -1.12c     3.76c    -2.45c
  30-60s             1.22c     -5.86c      1.18c     0.25c     0.47c
  1-2m              -1.43c     -2.82c     -1.91c     2.18c    -0.08c
  2-5m               1.80c     -0.26c     -2.38c     2.76c    -1.04c
  5-15m              7.46c      6.48c     -0.23c     0.37c    -0.39c
```

## None of the 15 positive cells survives

```text
side  time     geometry      mean   5th pct        n  rounds
DOWN  5-15m    <0.25        7.46c    -1.19c   10,541     135
DOWN  5-15m    0.25-.75     6.48c    -1.00c   11,338     135
DOWN  10-30s   1.5-3        3.76c    -0.94c      806     173
DOWN  2-5m     1.5-3        2.76c    -0.64c    7,454     277
UP    30-60s   0.25-.75     2.11c    -5.08c      789     128
...
0 of 15 positive cells have a positive 5th percentile.
```

The bootstrap resamples **rounds**, not snapshots. Snapshots inside a round share one outcome,
so resampling them independently would shrink every interval by roughly the snapshot count and
turn all fifteen of these into "significant".

## The trap in that table

The DOWN column looks systematically better than UP. **It is not a state effect.**

```text
settled rounds 547   UP won 47.5%   DOWN won 52.5%
mean up_ask 0.5079   mean down_ask 0.5025

blanket DOWN buyer, NO state selection:  +2.22c/share gross
blanket UP buyer,   NO state selection:  -3.26c/share gross
that imbalance is 1.2 standard errors from an even split (NOISE)
```

A reader scanning the surface would build a DOWN-biased strategy out of **this sample's own
coin flips**. Any cell whose mean is below +2.22c is worse than not selecting at all — which
disqualifies eleven of the fifteen immediately. The control now prints beside the table so it
cannot be read without it.

---

## What this means for the selectivity architecture

It does not say selectivity is wrong. It says **selectivity has nothing to select here yet**,
and that ordering matters:

- A **reliability model** trained on this surface would learn which cells had favourable
  noise. That is a fitted artifact, and it would backtest beautifully.
- An **OOD detector** would correctly flag unusual states, but flagging them changes nothing
  when the in-distribution states are not profitable either.
- **State specialists** by `time × geometry` are exactly the cells above. They were measured
  directly rather than trained, which is the cheaper way to find out there is nothing there.
- The **precision frontier** exists and is negative across its whole length on this data.

The honest sequencing is the reverse of intuition: **find a subset with a positive lower bound
first, then build the machinery that concentrates into it.** Machinery built first will find
a subset whether or not one exists.

## What is not tested here

Everything that is not "buy at the ask and hold to settlement":

- **Maker fills.** Different price (the bid side of the spread), no platform fee, and a
  toxicity question this cannot see.
- **Early exit.** Every cell holds to settlement. A round that trades to 0.67 before settling
  DOWN is a loss here and could be a gain with an exit rule. That is the largest untested
  source of P&L in the whole design.
- **Sub-second states.** The recorder is at ~1.95s; the L2 store at 32.5ms is not joined in.
- **Binance first-touch.** Different venue, different contract.

`research/profitability_surface.py` — read-only, standalone.
