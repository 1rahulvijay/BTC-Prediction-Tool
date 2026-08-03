# Regime labeler V1 — result

**Protocol** `PREREG_REGIME_LABELER_V1.md` sha256 `c4ae06b6…`, frozen `2026-08-03` before any
regime result was computed · **Script** `research/regime_labeler_v1.py` · Scored **once**

```
bars     518,340 one-minute BTC bars
train    2025-08-05 -> 2026-04-14      purge 60 bars      test -> 2026-07-30
```

## Result

| regime | share | test bars | dwell | forward \|move\| bps | day-block 95% CI |
|---|---:|---:|---:|---:|---|
| LIQUIDATION_SHOCK | 0.5% | 785 | 2 | 38.4 | [29.5, 46.9] *underpopulated* |
| SHOCK_EXHAUSTION | 0.9% | 1,380 | 1 | 28.9 | [24.8, 32.9] *underpopulated* |
| THIN_LIQUIDITY | 8.6% | 13,330 | 10 | **7.5** | [6.8, 8.3] |
| COMPRESSION_EXPANDING | 0.0% | 7 | 1 | 8.5 | [1.1, 10.3] *underpopulated* |
| TRENDING | 11.9% | 18,473 | 3 | **20.1** | [18.7, 21.6] |
| RANGE | 78.1% | 121,452 | 5 | **13.2** | [12.4, 14.0] |

**VERDICT: `REGIME_SEPARABLE`.** Three pairs separate with non-overlapping day-block intervals,
and the ordering is identical in train and test:

```
train   THIN_LIQUIDITY < RANGE < TRENDING
test    THIN_LIQUIDITY < RANGE < TRENDING
```

This is the first positive result in this line of work. It should be read narrowly.

## Qualification 1 — the separation may largely restate volatility clustering

`TRENDING` requires `rv_60m ≥ P50`. `THIN_LIQUIDITY` selects low volume, which correlates with
low volatility. The endpoint — forward 15-minute absolute move — **is** forward volatility.

So a substantial part of this result may be: *group bars by current volatility, and forward
volatility differs*. That is volatility persistence, which Phase 5C already measured directly
(AR(1) volatility half-life ≈ 34 minutes). It is real, but it is not new information, and a
router built on it would be routing on a quantity the system already models.

**The test that would settle it** — does regime separate forward move *after controlling for
current realised volatility?* — is not run here, because the protocol is scored once and
permits no additional analysis. It needs its own preregistration. Until then, treat the
separability as established but **not established as independent of volatility**.

## Qualification 2 — it passed the dominance kill rule by 1.9 points

The protocol kills the taxonomy if any regime holds more than 80% of bars. `RANGE` holds
**78.1%**. The taxonomy is overwhelmingly its own residual class, and four of six regimes are
essentially rounding error.

A near-miss is still a pass — the threshold was frozen and it was not crossed — but a taxonomy
that assigns four in five bars to "none of the above" is describing one state and five
exceptions.

## Qualification 3 — `COMPRESSION_EXPANDING` is dead, and that is a defect

It captured **7 bars out of 155,000** (0.0%). Its two conditions — `compression_ratio ≤ P25`
(tight) and `rv_15m / rv_60m ≥ 1.20` (expanding) — are close to mutually exclusive by
construction: a bar in the tightest quartile of compression rarely also has short-horizon
volatility 20% above its hourly level.

This is a specification defect, discovered by running it. It cannot be repaired inside this
protocol; a corrected definition requires a new hash. Recorded here so the next version starts
from the measurement rather than the intent.

## Qualification 4 — these regimes flicker

Median dwell times: `SHOCK_EXHAUSTION` 1 bar, `LIQUIDATION_SHOCK` 2, `TRENDING` 3, `RANGE` 5,
`THIN_LIQUIDITY` 10.

A state with a 3-minute median lifetime is not a regime in the usual sense; it is a
classification of the current bar. For a 5–15 minute Polymarket round a 3-bar dwell may still
be actionable, but any strategy that assumes a regime persists across a decision horizon is
assuming something this measurement does not support.

## What the shock regimes suggest, without claiming it

`LIQUIDATION_SHOCK` (38.4 bps) and `SHOCK_EXHAUSTION` (28.9 bps) have the highest forward
moves and intervals well clear of `RANGE`. They are excluded from the verdict because each holds
under 1% of bars — the protocol's rule, applied as written.

They are the most interesting rows in the table and the least supported. 785 and 1,380 bars over
a 3.5-month test window is not enough to build on, and their apparent strength is exactly the
kind of finding that survives until it is tested properly. A dedicated rare-event study with its
own preregistration would be the way to pursue it.

## Governance

- Protocol frozen and hashed **before** the script was written; 12/12 hashes verify in CI.
- Admitted as a **DIAGNOSTIC** under the Phase 6 freeze by explicit operator instruction. It may
  not promote a strategy, tune a threshold, or authorise capital.
- Every percentile threshold is fitted on **train only**; the selftest mutates test rows and
  asserts the thresholds do not move — the leak found in `edge_probe.py`.
- The scoring endpoint is not among the labelling inputs.
- Selftest: 16 checks, including that the priority order holds, that a shock bar cannot be its
  own precedent for exhaustion, and that an underpopulated regime cannot decide the verdict.

## Where this leaves the Strategy Router

The necessary condition is met: at least some declared regimes have genuinely different forward
behaviour, stably across a chronological split.

It is not sufficient. Before building the router:

1. **Control for realised volatility** (Qualification 1). If the separation vanishes, the router
   is routing on volatility the system already predicts.
2. **Fix `COMPRESSION_EXPANDING`** or drop it (Qualification 3).
3. Decide whether a 3-bar dwell supports routing at all (Qualification 4).

The next step in the original plan — the tradability / remaining-move head — does not depend on
any of these and can proceed independently.
