# Batch 3 — WAIT_VS_BUY_V1 and POLY_SETTLEMENT_CONVEXITY_V1

Run 2026-08-13 · `research_lanes/run_batch3.py` · 149,382 rows, 923 rounds, 10 days
Appended; batches 1 and 2 unrevised.

---

## WAIT_VS_BUY_V1 — **ORACLE BOUND, not a strategy**

Best executable ask reachable within the next N seconds of the same round, versus crossing now:

| wait | n | mean gain | median | % better | % worse | % no future quote |
|---|---:|---:|---:|---:|---:|---:|
| 10s | 148,162 | +0.0194 | +0.0080 | 50.4% | 17.1% | 0.8% |
| 30s | 148,420 | +0.0524 | +0.0200 | 68.4% | 10.7% | 0.6% |
| 60s | 148,444 | **+0.0833** | +0.0400 | **75.7%** | 8.2% | 0.6% |

8.3 cents of apparent improvement from waiting a minute, better three times in four. **Do not
read this as timing alpha.** Two things make it an upper bound and probably an illusion:

1. **It is an oracle.** This takes the *minimum* ask over the window. A live trader cannot know
   which second will be cheapest. The realisable number is strictly lower, and nothing here
   estimates it.

2. **A cheaper ask usually means a less valuable contract.** These are binaries drifting toward
   0 or 1. If UP is losing, its ask falls — you get a "better price" on something now worth
   less. The measurement cannot separate genuine timing improvement from the contract moving
   against you, and the second explanation is sufficient to produce the entire effect.

The rising `% better` with horizon (50% → 76%) is consistent with drift, not with timing skill:
a longer window simply gives more chances for the price to fall somewhere.

**Verdict: not evidence of entry-timing edge.** A real test conditions on the *outcome* — is the
later price better *after* adjusting for the changed probability — and compares against a
committed rule, not a hindsight minimum.

---

## POLY_SETTLEMENT_CONVEXITY_V1 — **clean structural result**

Cents of probability per basis point of BTC move, by time remaining and absolute distance from
the anchor:

| cell | n | rounds | delta (cents per bp) |
|---|---:|---:|---:|
| **<60s \| 0-3bps** | 425 | 225 | **0.7679** |
| 5-10m \| 0-3bps | 698 | 119 | 0.3100 |
| >10m \| 0-3bps | 1,524 | 222 | 0.2385 |
| >10m \| 3-8bps | 1,332 | 186 | 0.1978 |
| 5-10m \| 3-8bps | 1,057 | 159 | 0.1514 |
| >10m \| 8-20bps | 811 | 100 | 0.1106 |
| <60s \| 3-8bps | 481 | 241 | 0.1040 |
| 2-5m \| 0-3bps | 3,340 | 717 | 0.0801 |
| 60-120s \| 8-20bps | 426 | 159 | 0.0665 |
| 5-10m \| 8-20bps | 1,218 | 135 | 0.0490 |

The structure is exactly what the contract's payoff implies, which is the reassuring part:

- **Sensitivity peaks near the anchor in the final minute** — 0.77 c/bp at `<60s | 0-3bps`,
  roughly **7× the next-nearest time bucket at the same distance**.
- It **decays with distance** at every horizon. Far from the anchor the outcome is close to
  decided and BTC moves barely reprice it.
- It **decays with time remaining** at close distance — a move now matters more than the same
  move with ten minutes left for it to be undone.

Concretely: within a minute of settlement and within 3 bps of the anchor, a **10 bp BTC move
(~$100 at $100k) reprices the contract by ~7.7 cents.**

### What this is good for, and what it is not

This is a **risk and sizing input**, not a signal. It says where the contract is fragile — which
is where a stale quote is most costly, where adverse selection on a resting maker order is worst,
and where position size should be smallest for a given probability view.

It does **not** say BTC will move. Pairing it with a directional forecast reintroduces every
problem the earlier lanes found: `MARKET_DISAGREEMENT_RESOLUTION_V1` showed the model loses
disagreements at every magnitude.

**Where it does connect to a live lane:** `HEDGED_POLY_MM_V1`. The cells above are precisely
where a resting quote is most toxic. Any maker fill-and-markout study should stratify by this
surface rather than pooling, because pooled toxicity will average a benign regime with a lethal
one.

### Caveat

Cell counts are small — 425 rows / 225 rounds in the headline cell. The slopes are OLS fits with
no interval attached, and the monotone structure across both axes is the main reason to believe
them, not any single estimate. Ten days again.

---

## MAKER_MARKOUT_SURFACE_V1 — partial: the fill half is missing

**No fill data exists.** Confirmed: no fill/maker/order tables in the canonical store, only
`polymarket_quotes` (2,348 rows). The study as designed cannot run.

What *is* computable is markout **conditional on a hypothetical fill** at the resting bid.
Cents, positive = favourable:

| cell | +5s | +15s | +30s | n@30s |
|---|---:|---:|---:|---:|
| >5m \| 3-8bps | 0.93 | 1.01 | 1.12 | 18,762 |
| <60s \| 3-8bps | 0.76 | 1.10 | **1.82** | 3,770 |
| 60-120s \| >8bps | 0.64 | 0.81 | 0.83 | 2,396 |
| 2-5m \| 0-3bps | 0.56 | 0.56 | 0.61 | 35,583 |
| 2-5m \| >8bps | 0.42 | 0.25 | **0.06** | 9,105 |
| <60s \| 0-3bps | 0.69 | 0.65 | **0.14** | 5,940 |
| <60s \| >8bps | — | — | **−0.51** | 503 |

### Reading it honestly

Most cells sit at **0.5–1.1c and do not erode** — but that is largely mechanical. Buying at the
bid and marking to mid captures half of a 1c spread, so ~0.5c is the spread itself, not alpha.

Three cells decay toward or below zero: `2-5m | >8bps` (0.42→0.06), `<60s | 0-3bps` (0.69→0.14)
and `<60s | >8bps` (−0.51). The second is the one `POLY_SETTLEMENT_CONVEXITY_V1` predicted —
0.77 c/bp sensitivity, the most fragile cell on the surface — and it is where markout decays
fastest. The two lanes agree, which is mild corroboration for both.

### Why this is still an upper bound

**Real fills are adversely selected and these are not.** A resting bid fills preferentially when
the market is coming to hit it — i.e. when the contract is about to be worth less. This
measurement takes every snapshot as an equally likely fill, so it systematically **understates
toxicity**. True maker markout is worse than every number above, by an unknown amount.

That keeps `HEDGED_POLY_MM_V1` where it was: upper bound favourable, therefore inconclusive.
The deciding measurement is unchanged — shadow-post real quotes, record which ones actually fill
and at what queue position, then mark those out. Nothing in the historical data substitutes.
