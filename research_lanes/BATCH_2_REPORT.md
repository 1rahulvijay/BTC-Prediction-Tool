# Batch 2 — five further lanes

Run 2026-08-13 · `research_lanes/run_remaining_lanes.py` · appended to batch 1, nothing revised

| lane | verdict |
|---|---|
| `MARKET_DISAGREEMENT_RESOLUTION_V1` | **CLOSE** — the model loses disagreements, worse the bigger they get |
| `MFE_MAE_DISTRIBUTION_V1` | **CLOSES A CAVEAT** — payoff is symmetric, so stops/targets don't rescue cost clearance |
| `STATE_VALUE_ATLAS_V1` | **INSUFFICIENT DATA** — no cell separates |
| `POLY_STALE_QUOTE_V1` | **NO EFFECT** detected, and the data can barely test it |
| `IMPACT_ASYMMETRY_V1` | **REAL, NEGLIGIBLE** — sells move price ~78% more, worth 0.06 bps |

---

## 1. MARKET_DISAGREEMENT_RESOLUTION_V1 — the standout

When the model and the market disagree, who turns out closer to the truth?

| \|residual\| | rows | rounds | **model win rate** | 95% CI |
|---|---:|---:|---:|---|
| 0.02 – 0.05 | 37,949 | 905 | **0.397** | [0.378, 0.417] |
| 0.05 – 0.08 | 27,381 | 905 | **0.353** | [0.330, 0.375] |
| 0.08 – 0.12 | 23,593 | 910 | **0.333** | [0.303, 0.360] |
| 0.12 – 0.20 | 20,009 | 892 | **0.309** | [0.271, 0.352] |
| 0.20 – 1.00 | 9,413 | 681 | **0.331** | [0.266, 0.399] |

**Every band is decisively below 0.5**, and the win rate *falls* as the disagreement grows —
from 40% at a 2–5c gap to 31% at 12–20c. Not one interval touches 0.5.

So when the model disagrees with the market by 15 cents, the market is right roughly **seven
times in ten**. The disagreement is not signal; it is mostly the model being wrong, and larger
disagreements are more wrong.

This quantifies what `POLYMARKET_RESIDUAL_V1` showed indirectly (EV degrading as the edge
threshold rose) and states it as a direct win rate. It is the cleanest negative result in
either batch, and it is the one to remember: **a large model-vs-market gap is evidence against
the model.**

Implication for the residual formulation `logit(p_true) = logit(p_market) + f(X)`: anchoring on
the market is not merely a nicety, it is the only defensible structure. `f(X)` should be
strongly regularised toward zero.

---

## 2. MFE_MAE_DISTRIBUTION_V1 — closes an open caveat from batch 1

`BINANCE_COST_CLEARANCE_V1` listed "asymmetric payoffs" as something it did **not** rule out: a
strategy with a tight stop and a wide target truncates one tail and changes the arithmetic. That
caveat is now closed.

Over 518,400 bars / 360 days, for a 5-minute holding window:

| | bps |
|---|---:|
| mean MFE (max favourable excursion) | **7.97** (LCB 7.52) |
| mean MAE (max adverse excursion) | **7.99** (LCB 7.54) |
| touch +12 bps | 20.9% |
| touch −12 bps | 21.2% |
| touch **both** | 2.9% |

MFE and MAE are **the same to two decimal places**. Price is as likely to run 12 bps against you
as for you, and only 2.9% of windows touch both, so stop-versus-target ordering rarely even
arises.

There is no favourable asymmetry to harvest. A tight stop with a wide target does not rescue the
cost-clearance verdict, because the distribution it would exploit is symmetric.

**Honest limit:** bar extremes cannot say which barrier was hit *first* within the bar. The 2.9%
both-touch figure bounds how often that ambiguity matters, and it is small.

---

## 3. STATE_VALUE_ATLAS_V1 — right idea, not enough data

Partition every snapshot by `seconds_left × distance_from_anchor × market price`, then simply
count realized UP frequency against the market's own price. No model, nothing to overfit.

43 cells had ≥30 independent rounds. The largest gaps:

| cell | rounds | realized | market | gap | 95% CI |
|---|---:|---:|---:|---:|---|
| >10m \| −15..−5 \| 35-45c | 37 | 0.252 | 0.381 | −0.129 | [−0.307, +0.083] |
| 2-5m \| −15..−5 \| 35-45c | 43 | 0.507 | 0.388 | +0.119 | [−0.099, +0.303] |
| >10m \| 5..15 \| 55-65c | 44 | 0.510 | 0.619 | −0.109 | [−0.320, +0.106] |
| <60s \| flat \| 45-55c | 124 | 0.403 | 0.504 | −0.100 | [−0.219, +0.038] |
| 5-10m \| 5..15 \| >65c | 108 | 0.757 | 0.850 | −0.093 | [−0.206, +0.005] |

**Every interval spans zero.** The gaps look large — 9 to 13 cents — but with 37 to 124
independent rounds per cell they are indistinguishable from noise. Reporting the point estimates
alone would have produced five confident-looking "edges," none of which the data supports.

Note also the selection problem: these are the largest of 43 gaps. The maximum of 43 noisy
estimates is biased upward, which is why the interval matters more here than anywhere else.

**Not a negative result — an underpowered one.** The atlas is the right instrument; it needs
hundreds of rounds per cell, which means hundreds of days rather than ten.

---

## 4. POLY_STALE_QUOTE_V1 — no effect, and barely testable

Is an older book systematically mispriced relative to settlement?

| book age | rows | rounds | mean \|market − outcome\| | 95% CI |
|---|---:|---:|---:|---|
| <0.1s | 37,994 | 350 | 0.3342 | [0.3125, 0.3536] |
| 0.1–0.5s | 413 | 158 | 0.3494 | [0.3097, 0.3851] |
| 0.5–2s | 396 | 139 | 0.3220 | [0.2773, 0.3692] |
| 2–10s | 632 | 53 | 0.3100 | [0.2429, 0.4035] |

All four intervals overlap heavily. No relationship between staleness and mispricing.

**The bigger problem is that this data cannot test the hypothesis.** 37,994 of 39,435 usable
rows sit in the youngest bucket; the stale buckets have a few hundred rows each. And
`book_age_s` has a median of **−0.06s** — negative, indicating clock offset between the book
timestamp and the local clock, which makes fine-grained age unreliable.

A real stale-quote test needs synchronised event-time capture and a target of *quote revision
within N milliseconds*, not settlement. That is a recorder change, not an analysis change.

---

## 5. IMPACT_ASYMMETRY_V1 — real, and far too small

Does equal aggressive volume move price equally in both directions? Comparing the top and
bottom deciles of taker-flow imbalance:

| | move (bps) | LCB | n |
|---|---:|---:|---:|
| buy-heavy | +0.072 | +0.033 | ~51,000 |
| sell-heavy | +0.128 | +0.092 | ~51,000 |
| **asymmetry** | **−0.056** | | |

Both lower bounds are positive, so the effect is real: **sell-heavy flow moves price about 78%
more than equally buy-heavy flow.** The book is structurally more fragile downward.

And it is worth 0.056 bps against a 12 bps round trip — about one two-hundredth of a cost unit.
Potentially useful as a *feature*; not a trade.

---

## What batch 2 changes

1. The Polymarket model does not merely fail to beat the market — it **loses disagreements at
   every magnitude**, and worse the larger the disagreement. Any future PM model must anchor on
   the market price and be heavily regularised toward it.
2. The asymmetric-payoff escape hatch from `BINANCE_COST_CLEARANCE_V1` is **closed**: MFE 7.97
   vs MAE 7.99.
3. The state-value atlas is the right tool and is **data-starved**, which points at the same
   operational fix as everything else on this venue — accumulate paired recording days.

Nothing in batch 2 revises batch 1. Both stand as run.
