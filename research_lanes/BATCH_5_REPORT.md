# Batch 5 — Every Remaining Runnable Lane

Date: 2026-08-14
Runner: `run_batch5.py` · Raw: `batch5_results.json`
Lanes: `PATH_ASYMMETRY_V1`, `COMPETING_RISKS_V1`, `REGIME_EXIT_HAZARD_V1`,
`NEXT_ROUND_OPENING_V1`, `EXIT_EDGE_DECAY_V1`

## Headline — the result changed shape

**No lane clears its cost hurdle.** Running total: **29 lanes, 0 tradeable edges.**

But this batch is not another round of "found nothing". Two effects are now **statistically
established and survive multiplicity correction** — and both are still economically dead. That is
a sharper and more useful finding than absence, because it says the search for *predictability*
is succeeding while the search for *tradeable edge* fails. The binding constraint is cost, not
knowledge.

| Lane | Effect real? | Economically usable? |
|---|---|---|
| `PATH_ASYMMETRY_V1` | **yes** (family-wise p = 0.008 / 0.000) | no — 13× too small |
| `REGIME_EXIT_HAZARD_V1` | **yes** (0.077 vs 0.300, non-overlapping) | no — gating makes it worse |
| `COMPETING_RISKS_V1` | no | no |
| `NEXT_ROUND_OPENING_V1` | no | no |
| `EXIT_EDGE_DECAY_V1` | no | no |

## Where each lane ran, and why

```
research_matrix_1m.parquet   43,200 bars / 30 UTC days   <- best available
pm_round_snapshots          177,911 obs  / 10 UTC days
```

`PATH_ASYMMETRY`, `COMPETING_RISKS` and `REGIME_EXIT_HAZARD` are questions about BTC's path, so
they ran on the **1m matrix** for 30 day-blocks. Running them on Polymarket snapshots — the
tempting choice, since the trade settles there — would have discarded two-thirds of the
independence units to answer the same question about the same underlying.

`NEXT_ROUND_OPENING` and `EXIT_EDGE_DECAY` are about Polymarket's own quotes and had no
alternative to the 10-day sample. Their bounds are correspondingly wide.

---

## 1. `PATH_ASYMMETRY_V1` — a real effect, 13× too small

Unconditional excursion is symmetric, confirming the earlier finding: MFE 5.15 vs MAE 5.12 bps at
5m, 9.79 vs 9.71 at 15m.

Conditioning on causal state does split it, and the split is **not noise** — a max-statistic
permutation over the ten state buckets gives family-wise **p = 0.008** at 5m and **p = 0.000**
at 15m.

Best state found (15m, low realized-vol tercile):

```
MFE        5.97 bps
MAE        5.11 bps
ratio      1.1691
net       +0.863 bps      LCB95 +0.263 bps    <- a positive lower bound
hurdle    12.0   bps      round trip
```

**A statistically solid +0.86 bps against a 12 bps round trip.** The asymmetry is real, survives
correction, and has a positive lower bound — and it is roughly one fourteenth of the cost of
acting on it. No state clears the hurdle at either horizon.

This closes the asymmetric-payoff route to profitability. It was previously "unproven"; it is now
measured, significant, and insufficient.

---

## 2. `COMPETING_RISKS_V1` — cost dominates completely

A 4×4 TP/SL grid at 15m, long-only geometry with no directional signal, measuring whether any
trade *shape* is profitable before a model is applied.

| tp | sl | P(TP first) | P(SL first) | P(neither) | gross bps | net bps | LCB95 |
|---|---|---|---|---|---|---|---|
| 30 | 5 | 0.0414 | 0.5648 | 0.3938 | +0.498 | −11.50 | −11.73 |
| 30 | 10 | 0.0510 | 0.3428 | 0.6063 | +0.196 | −11.80 | −12.15 |
| 20 | 30 | 0.1329 | 0.0527 | 0.8144 | −0.170 | −12.17 | −12.59 |
| 30 | 30 | 0.0582 | 0.0538 | 0.8880 | −0.098 | −12.10 | −12.56 |

**Gross payoff across all 16 combinations is within ±0.5 bps of zero.** The competing-risks
structure is a fair game before costs; after a 12 bps round trip every cell is −11.5 to −12.6.
Zero combinations have a positive lower bound.

The geometry cannot rescue an absent edge — it just relabels where the same zero shows up.

---

## 3. `REGIME_EXIT_HAZARD_V1` — strongly predictable, and worthless

This is the most interesting negative in the batch. Regime exit is **highly** predictable from
regime age alone, a causal feature known at decision time:

| P(regime changes within 5m) | n | estimate | LCB95 | UCB95 |
|---|---|---|---|---|
| unconditional | 43,195 | 0.2224 | 0.1981 | 0.2446 |
| regime age ≤ 5m | 11,846 | 0.2998 | 0.2826 | 0.3146 |
| regime age 6–30m | 19,206 | 0.2665 | 0.2526 | 0.2801 |
| **regime age > 30m** | 12,143 | **0.0771** | 0.0613 | 0.0980 |

A **4× spread** with non-overlapping intervals. At 15m the spread is 0.5494 vs 0.1479.

Then the economic leg, using the **oracle bound** — mean |move| minus round trip, the ceiling a
*perfect* direction model could achieve in that state:

```
5m   gated (age>30m)  -6.137 bps  (LCB -7.572)   ungated  -6.111 bps  (LCB -6.814)
15m  gated            -1.827 bps  (LCB -4.215)   ungated  -1.793 bps  (LCB -3.026)
30m  gated            +2.238 bps  (LCB -1.046)   ungated  +2.471 bps  (LCB +0.728)
```

**Gating on the predictable regime makes the economics slightly worse at every horizon.** Stable
regimes are stable precisely because prices are not moving, so the same feature that predicts
persistence also predicts small moves. The information is real and points the wrong way.

Note the 30m ungated row: oracle LCB **+0.728 bps** — the only positive economic bound found in
29 lanes. It requires a *perfect* direction model to realise, so it is a ceiling, not an edge.
It does say the 30m horizon is the only one where the move-vs-cost arithmetic is not hopeless
before a model is applied.

---

## 4. `NEXT_ROUND_OPENING_V1` — no opening mispricing

The hypothesis was that a newly opened round is thin and slow to incorporate BTC state.

| window | BUY_UP mean | LCB95 | BUY_DOWN mean | LCB95 |
|---|---|---|---|---|
| 0–5s after open | −2.6188c | −4.8607 | −1.8774c | −4.2948 |
| 5–10s | −3.4978c | −6.5303 | −0.9568c | −3.9731 |
| 10–30s | −2.7398c | −4.7360 | −1.6322c | −3.7224 |
| 30–60s | −3.3259c | −4.9138 | −0.9077c | −2.4854 |
| 60–120s | −3.4146c | −4.8571 | −0.5753c | −2.1559 |

**No positive lower bound in any window.** The first five seconds are no more mispriced than the
second minute. If anything the earliest windows are worse, consistent with a wider opening spread
being charged for the privilege.

---

## 5. `EXIT_EDGE_DECAY_V1` — holding and exiting are the same trade

Comparing hold-to-settlement against selling at the bid, for a position already open:

| seconds left | n | hold − exit | LCB95 | UCB95 |
|---|---|---|---|---|
| 0–30s | 4,351 | −0.9953c | −3.5510 | +0.7731 |
| 30–60s | 8,894 | −0.4682c | −1.6256 | +1.0159 |
| 60–120s | 22,748 | −0.2884c | −1.0922 | +1.1240 |
| 120–300s | 77,214 | −1.0547c | −1.8431 | +0.2932 |

Every point estimate is slightly negative and every interval straddles zero. There is no moment
in a round where holding is detectably better or worse than exiting at the bid — which is what an
efficiently priced book should look like, and leaves no exit-timing edge to harvest.

---

## What batch 5 changes about the project's conclusion

Twenty-nine lanes have now produced **zero tradeable edges**, but the character of the failure is
now precisely located:

1. **Predictability exists.** Regime exit is 4× predictable. Path asymmetry is real at p < 0.01.
   These are not noise and they survive correction.
2. **The moves are too small.** 5m mean |move| is ~5 bps against a 12 bps round trip. The oracle
   bound — what a *perfect* model would earn — is negative at 5m and 15m, and only barely
   positive at 30m.
3. **Therefore no directional model can fix this at 5m/15m.** The ceiling is below the floor.
   This is an arithmetic statement, not a modelling one.

The only remaining routes are the ones that attack cost rather than accuracy: maker rebates,
queue position, longer horizons where |move| exceeds the round trip, or a venue with a cheaper
round trip. Every one of those needs `capture_app` data that does not exist yet.
