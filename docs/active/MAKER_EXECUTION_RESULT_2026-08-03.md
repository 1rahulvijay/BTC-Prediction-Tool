# Binance maker execution, real book and trades — result

**Protocol** `PREREG_BINANCE_MAKER_EXECUTION_V1.md` sha256 `c15b4443…`, frozen before any result ·
**Script** `research/maker_execution_v1.py` · Scored **once**

```
11,389,147 bookTicker quotes / 775,982 aggTrades   binance_perp BTCUSDT
2026-07-28 20:26 -> 2026-07-29 19:18 UTC = 22.9 hours = ONE DAY
1,347 passive orders, 0.01 BTC, one per 60s, alternating side, 250 ms latency
median spread 0.02 bps    maker fee 1.0 bps charged
```

## Result

| bound | fill rate | net / submitted | net / filled | hour-block 95% CI |
|---|---:|---:|---:|---|
| NO_FILL | 0.0% | 0.000 | – | [0.000, 0.000] |
| IMMEDIATE *(ceiling)* | 100.0% | **−0.807** | −0.807 | [−1.014, −0.589] |
| TOUCH | 68.2% | −0.892 | −1.307 | [−1.162, −0.619] |
| VOLUME_AHEAD | 50.1% | −1.163 | −2.321 | [−1.492, −0.830] |
| **OPERATIONAL** | 48.0% | **−1.119** | −2.333 | [−1.439, −0.804] |

**VERDICT: `MAKER_LOST_TO_ADVERSE_SELECTION`** — fired on the first kill rule: *even the
optimistic ceiling is negative*, so no realistic fill model can rescue it.

## Adverse selection, measured directly

Markout on `OPERATIONAL` fills, signed by side:

```
 1s   -0.702 bps
 5s   -0.856
15s   -1.092
30s   -1.322
60s   -1.333
```

Monotonically worse with time — the textbook signature. Once filled, price keeps moving against
the resting side.

The cleanest way to see it is the contrast between two bounds, both before fees:

```
IMMEDIATE    fills chosen by nobody, every order        gross  +0.193 bps
OPERATIONAL  fills that ACTUALLY occurred               gross  -1.333 bps
                                                        -------------------
adverse selection                                              1.526 bps
```

An unconditional passive fill earns +0.19 bps. A fill that *actually happens* — because volume
traded through the level and cleared the queue — loses 1.33 bps. **The 1.53 bps difference is
the price of the information you traded against.** That is not an assumption; it is the same
orders measured under two fill rules.

Fill rates are healthy (48–68%), so this is not a participation problem. The orders fill. They
fill badly.

## The lane fails twice, independently

**1. Arithmetic, before any measurement.** Binance maker fees are 1.0 bps a side, so a maker
round trip is **2.0 bps**. The largest gross edge measured anywhere in this repository —
`MULTIHORIZON_DIRECTION_V1`, 240 minutes, 7 pairs, 180 days — is **1.97 bps**.

```
2.0 bps maker round trip  >  1.97 bps best measured gross edge
```

Perfect, instantaneous, adverse-selection-free maker execution *still* does not clear it. The
`MAKER_CHANGES_THE_ARITHMETIC` verdict is unreachable by construction, and the selftest asserts
exactly that rather than leaving it as an unexercised branch.

**2. Empirically.** Real fills lose 1.33 bps gross to adverse selection, so the realised outcome
is well below even that unreachable bar.

## Why the premise collapsed: the spread is already gone

**Median spread on BTCUSDT perp: 0.02 bps.**

The maker case rests on capturing the spread. On this instrument there is essentially no spread
to capture — half a spread is 0.01 bps, against adverse selection of 1.33 bps and a fee of 1.0.

The fee saving is real and large — taker 14.0 bps round trip versus maker 2.0, a **12 bps
saving**. It is simply not enough, because the thing being funded is a 1.97 bps edge. Removing
12 bps of cost from a lane whose gross edge is 1.97 bps and whose remaining cost is 2.0 bps
leaves it negative by arithmetic.

## What this settles

`MULTIHORIZON_DIRECTION_V1` ended by naming cost as the only remaining lever, and pointed at
maker execution as the way to attack it. This tests that pointer with real book and trade data
and closes it:

```
signal side   gross edge         +0.97 to +1.97 bps   (measured, reproducible, real)
cost side     taker round trip           14.0  bps
              maker round trip            2.0  bps    (best case, fees only)
              adverse selection           1.53 bps    (measured on real fills)
```

There is no execution route on this venue that makes a 1.97 bps edge pay. Taker costs 7× the
edge; maker costs slightly more than the edge before adverse selection, and roughly twice it
after.

## Limits, stated plainly

- **One day.** 22.9 hours cannot support day-clustered inference, and none is claimed — the
  intervals are hour-blocked over 22 blocks, which cannot see day-to-day regime variation. A
  one-day result can be unrepresentative in either direction.
- **BTCUSDT only**, and it is the tightest-spread instrument on the venue. A wider-spread
  altcoin perp would have more spread to capture *and* more adverse selection; that is a
  different study with a different hash.
- **Top-of-book only.** Queue position is known at level 1, so orders larger than the visible
  best size were excluded rather than assumed to fill.
- The simulation measures **execution quality of a passive fill with no alpha attached**. It
  answers "what does a maker fill cost?", not "would strategy X profit?". That is the right
  question here, because the strategy side was already measured.

## Governance

- Protocol frozen and hashed before any result; 19/19 hashes verify in CI.
- `recv_ts` is in **seconds**; the loader converts explicitly. The same unit confusion
  previously sent 56,467 rows to 1970 in this repository.
- Order times are a **fixed 60-second grid with alternating sides** — exogenous by
  construction, so no signal can leak into the execution measurement.
- `IMMEDIATE` is tagged hindsight and reported as a ceiling only.
- **The maker fee is charged.** It is not assumed zero merely because taker fees disappear.
- Selftest 18 checks, including that with no trades `TOUCH` and `VOLUME_AHEAD` never fill while
  `IMMEDIATE` always does, that 0.05 traded cannot clear a 5.0 queue, and that the pass branch
  is arithmetically unreachable with the frozen constants.
