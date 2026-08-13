# POLYMARKET_RESIDUAL_V1

**Verdict: CLOSE LANE as a taker strategy.** The model is genuinely informative, and still
strictly worse than the price it would have to pay to disagree with. Trading its disagreement
loses **1.8–2.7 cents per share**, and loses *more* the larger the disagreement.

Run 2026-08-13 · `research_lanes/polymarket_residual/run.py`

> Supersedes an earlier version of this file that recorded BLOCKED. At that time
> `pm_export_settlements.parquet` was empty and the snapshot/settlement date ranges did not
> overlap. It was backfilled at 08:06 on 2026-08-13; the lane now joins 149,061 rows.

---

## Data

| | |
|---|---|
| joined snapshots | 149,061 |
| rounds | 921 |
| distinct days | 10 |
| horizons | 5m and 15m |
| settlement | `polymarket_gamma` (128,796) + `polymarket_clob` (19,945) — official only |
| base rate UP | 48.7% |

Every interval resamples whole **rounds**. ~162 snapshots share one round's single outcome, so
a row bootstrap would claim roughly 162x the independent evidence that exists.

## 1. Does the model beat the market as a forecast?

| forecast | Brier |
|---|---:|
| **market mid** | **0.1699** |
| model `p_hold_up` | 0.1832 |
| constant 0.5 | 0.2500 |

Improvement over market: **−0.0133**, 95% CI **[−0.0176, −0.0091]** across 921 rounds.

The interval excludes zero on the *negative* side. This is not "no evidence the model beats
the market" — it is evidence the model is **worse**, decisively.

Both facts matter: the model is clearly informative (0.183 against 0.250 for a constant), and
it is clearly behind the market (0.183 against 0.170). Being informative is not the bar. The
market's price is the baseline you must pay a fee to trade against.

## 2. Does trading the disagreement make money?

Buy UP at `up_ask`, or DOWN at `1 − up_bid`, whenever the model's edge exceeds a threshold.
Fees are Polymarket's crypto taker schedule, `0.07 · p · (1−p)` per share — 1.75c at p=0.50,
which is 3.5% of a 50c entry.

| edge threshold | trades | rounds | net EV / share | 95% LCB | 95% UCB |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 122,922 | 917 | **−0.0183** | −0.0369 | +0.0001 |
| 0.02 | 95,003 | 917 | −0.0195 | −0.0411 | +0.0010 |
| 0.04 | 72,455 | 917 | −0.0201 | −0.0438 | +0.0022 |
| 0.06 | 54,490 | 915 | −0.0217 | −0.0466 | +0.0043 |
| 0.08 | 40,743 | 913 | −0.0224 | −0.0490 | +0.0075 |
| 0.10 | 30,138 | 902 | **−0.0268** | −0.0595 | +0.0042 |

Every threshold is negative. No lower bound is positive anywhere.

## The finding worth keeping

**EV gets monotonically worse as the required edge grows**: −0.0183 at no threshold, −0.0268 at
10 cents. If the model carried real information the market lacked, filtering to its strongest
disagreements should *improve* results. It does the opposite.

The natural reading is that a large model-vs-market gap is more often the model being wrong
than the market being wrong. That is a concrete, measured instance of a hypothesis worth
holding generally: **a big disagreement is not automatically alpha.**

## A bug in my first run, and how it surfaced

The first version mapped P(UP) from `p_hold_cur` flipped on `current_side`, reasoning that
`p_hold_cur` is the probability the *current* side holds. That produced a model Brier of
**0.3109 — worse than always predicting 0.5.**

That number is the tell. A forecast worse than a constant is usually *inverted*, not
uninformative. `current_side` in this export is `'0.0'`/`'1.0'`, not `'UP'`/`'DOWN'`, so the
string comparison matched nothing and flipped every row.

| mapping | Brier | corr with outcome |
|---|---:|---:|
| `p_hold_up` (correct) | 0.1832 | +0.517 |
| `p_hold_cur` flipped by side (bug) | 0.3109 | −0.009 |
| `p_hold_cur` raw | 0.3192 | +0.009 |
| constant 0.5 | 0.2500 | — |

Had I not sanity-checked against the constant baseline, this lane would have reported the
model as catastrophically bad rather than modestly behind the market. The constant baseline
earned its place.

## What this does NOT close

1. **Maker.** This is a pure taker test, and the taker fee is the dominant cost. Polymarket
   charges makers zero platform fee and runs a rebate pool. `HEDGED_POLY_MM_V1` asks a
   different question and is untouched by this.
2. **Full-set arbitrage.** `Ask_YES + Ask_NO < 1` is a mechanical inconsistency requiring no
   forecast at all. Nothing here bounds it.
3. **A better model.** This tests *the app's current `p_hold_up`*, not the residual formulation
   `logit(p_true) = logit(p_market) + f(X)`. That model has never been fitted. Given the market
   is the stronger forecaster, anchoring on it and learning only the correction is the
   structurally right move — and remains untested.
4. **Delta-hedged variants.** Unhedged directional exposure is assumed here.

## Attacks applied

| attack | status |
|---|---|
| round-clustered bootstrap | yes — 921 rounds, 800 draws |
| official settlement only | yes — Gamma/CLOB, no exchange proxy |
| executable prices | yes — `up_ask` / `up_bid`, not mid |
| real fee schedule | yes — `0.07·p·(1−p)` |
| beat a market baseline | yes — and it does not |
| constant baseline | yes — and it caught a sign bug |
| second era | **no** — only 10 days exist |
| regime breakdown | **no** |

## Caveat that limits everything above

**10 distinct days.** 921 rounds is a reasonable count, but they come from ten calendar days
(06-16, 06-29→07-04, 08-09→08-13). Ten days is not enough to claim a stable regime-independent
result, in either direction. The negative Brier finding is tight enough to trust; the trading
EV interval is wide, and its upper bounds sit slightly above zero at most thresholds.

Running both recorders together to accumulate paired days remains the highest-value operational
action for this venue.
