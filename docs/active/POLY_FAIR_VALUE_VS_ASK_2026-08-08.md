# Structural fair value vs the executable ask — `2026-08-08`

The ledger has carried this as "the only comparison that decides tradeability" for weeks and
never run it. It is now run, on **106,058 live in-window snapshots across 545 settled BTC
Up/Down rounds** with the recorded Polymarket ask on every row.

**Result: the market wins. On this evidence there is no taker lane for a structural
fair-value model.**

---

## The chain, and where it stops

Three questions in order. The second and third stop mattering if the first fails.

### 1. SKILL — does the model beat the market's own implied probability?

```text
base rate (share of UP settlements)   0.4716

                          structural   market ask       verdict
Brier (lower better)         0.18132      0.16693   MARKET better
log loss (lower better)      0.56518      0.49634   MARKET better
```

Not close. The ask is a better forecaster of the venue's own settlement than the closed-form
model is, which is what §4.5 already found for both learned model vintages. The market is
the competitor, not 50%.

### 2. EDGE — where they disagree, who is right?

```text
model over market by  [2%,5%)   n=12,385   UP settled 0.5930   avg ask 0.653   REFUTES model
model over market by [5%,10%)   n=12,271   UP settled 0.5645   avg ask 0.605   REFUTES model
model over market by [10%,∞)    n=12,202   UP settled 0.5336   avg ask 0.531   ties
```

In the two buckets where the model claims a modest mispricing, **the market is right and the
model is wrong** — the realised UP rate lands *below* the ask the model wanted to buy through.
In the largest-disagreement bucket the two are a statistical tie (0.5336 vs 0.531).

That is the worst of the three possible shapes. A model that is wrong *randomly* is noise; a
model whose claimed edge is systematically contradicted is anti-informative in exactly the
region a trader would act on.

### 3. MONEY — does anything survive the fee?

The published Polymarket crypto taker fee is `shares × 0.07 × price × (1-price)` — **1.75c per
share at 50c**, vanishing at the extremes.

```text
min raw edge        n     gross/share   fee/share   NET/share
2%             36,858         -3.28c       1.22c      -4.50c
3%             32,113         -2.79c       1.24c      -4.03c
5%             24,473         -1.89c       1.28c      -3.17c
8%             16,200         -0.42c       1.32c      -1.74c
```

Every bucket loses **before** the fee. The fee is not what kills it; it is what makes the
loss unrecoverable.

Round-clustered bootstrap on the ≥5c bucket (516 rounds, resampling **rounds** not snapshots —
snapshots inside a round share one outcome, so resampling them independently would shrink the
interval by the snapshot count and manufacture significance):

```text
5th percentile net = -7.18c/share
```

---

## The failure is diagnosable, and fixing it does not rescue the result

The calibration table names the mechanism:

```text
forecast 0.029  ->  realised 0.095
forecast 0.148  ->  realised 0.242
forecast 0.849  ->  realised 0.769
forecast 0.971  ->  realised 0.895
```

Overconfident at **both** tails — the signature of an underestimated volatility. The
distribution is too tight, so the z-score is too large and Φ saturates.

So the obvious repair is to scale σ. Tested, with the multiplier chosen **in-sample on the
test set itself** — the most generous possible version of the fix:

```text
sigma x1.0   Brier 0.18132
sigma x1.5   Brier 0.17808   <- best
sigma x2.0   Brier 0.18010
sigma x3.0   Brier 0.18809
sigma x5.0   Brier 0.20270

market ask   Brier 0.16692
```

**The optimum, fitted with hindsight, still loses to the ask by 0.011 Brier.** An honest
out-of-sample σ would be worse. The gap is not a volatility-calibration problem.

---

## What this does and does not close

**Closes:** the taker lane driven by a *structural* fair value. Φ(z) on distance, time and
realised volatility is not more informative than the price, and buying through the ask on its
disagreements loses money before fees.

**Does not close:**

- **The residual lane.** `MARKET_RESIDUAL_ALPHA` targets `outcome − market_probability`, which
  is a different and harder question than "what is fair value". This result raises its bar
  rather than removing it: the residual model must add information the ask does not already
  contain, and the ask contains a lot.
- **The maker lane.** Everything above is taker economics. Makers pay no platform trading fee,
  and the adverse-selection question is untested here — but note that a maker lane needs a
  *fill and toxicity* model, not a better probability, and the probability being worse than
  the market makes toxicity more likely, not less.
- **Cross-venue latency.** Nothing here measures whether Polymarket reprices slower than
  Binance. That is a timing question about the quote, not a forecasting question about the
  outcome, and it is untouched by this.
- **Binance first-touch.** A different contract on a different venue, unaffected.

---

## What would have to be true to reopen the taker lane

One thing, measurable: **a probability estimate that beats `Brier 0.16692` out of sample on
these rounds.** Not a better accuracy, not a better AUC — a better proper score than the price
itself. Until something clears that bar, every downstream engine (executable EV, act/skip,
sizing) is arithmetic on a number that is worse than the one already printed on the screen.

The honest reading of the last three studies together:

```text
served directional lean vs the venue's question   coin flip (p=0.82 vs shuffle)
Polymarket ask vs both model vintages             ask wins on Brier/logloss/ECE/AUC  (4.5)
structural fair value vs the ask                  ask wins on Brier and log loss     (here)
```

Three independent measurements, one direction. The information needed to beat this market's
own price has not been found in this repository yet.

---

## Artifacts

```text
backend/polymarket_fair_value.py          STRUCTURAL_FAIR_VALUE_V1 + the real fee formula
research/poly_fair_value_vs_ask.py        this study, read-only, standalone
```

`polymarket_fair_value.py` is worth keeping regardless of this result: it is the correct
baseline for anything that later claims to price these rounds, it encodes the venue's actual
fee curve, and its selftest pins the monotonicity properties (closer to expiry is worth more,
lower volatility is worth more, symmetric about the anchor, refuses under 2 seconds and on a
zero sigma) that any replacement must also satisfy.
