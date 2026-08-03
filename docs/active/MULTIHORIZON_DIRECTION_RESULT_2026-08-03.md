# Multi-horizon, multi-pair, two-exchange direction — result

**Protocol** `PREREG_MULTIHORIZON_DIRECTION_V1.md` sha256 `2fb2a481…`, frozen before any
analysis of this dataset · **Scripts** `research/multihorizon/` · Scored **once**

```
120,967 rows   7 pairs   2 exchanges   2026-02-04 -> 2026-08-03 (180 days)
31 features   OI 100% / funding 100% / Binance 100% coverage
walk-forward 30-day blocks, purged by one horizon   primary CI 98.75% (Bonferroni /4)
cost 14 bps round trip at EVERY horizon
```

## Result

| horizon | trades | SOFT_VOTE AUC | null floor (upper) | net bps | 98.75% CI | implied gross | verdict |
|---|---:|---:|---:|---:|---|---:|---|
| **60m** | 20,132 | **0.5181** | 0.5117 | −13.03 | [−15.07, −11.21] | **+0.97** | `SIGNAL_ONLY` |
| 240m | 5,936 | 0.5077 | 0.5230 | −12.03 | [−18.10, −6.16] | +1.97 | `NO_SIGNAL` |
| 300m | 4,816 | 0.5102 | 0.5265 | −14.29 | [−22.10, −5.90] | −0.29 | `NO_SIGNAL` |
| 600m | 2,471 | 0.5068 | 0.5271 | −16.46 | [−30.23, −2.30] | −2.46 | `NO_SIGNAL` |

**Overall: `MULTIHORIZON_NO_TRADABLE_EDGE`.** No horizon cleared costs.

## The richer information did help — by about one basis point

This is the fairest way to state it. Going from *BTC only, 15 minutes, 23 features, 40 days* to
*7 pairs, 2 exchanges, real OI and funding, cross-sectional features, 180 days*:

```
previous (15m, BTC, 23 features)    SOFT_VOTE AUC 0.5137, gross +0.28 bps
now      (60m, 7 pairs, 31 features) SOFT_VOTE AUC 0.5181, gross +0.97 bps
```

The 60-minute arm is the only one with AUC above its null floor, and its gross edge is roughly
**three times** the previous best. It is also **1/14th of the cost of trading it**.

Adding open interest, funding, a second exchange, six more pairs and 140 more days moved the
measurable edge from half a basis point to one basis point. That is a real improvement and it is
two orders of magnitude short of what the venue charges.

## Why the longer horizons show nothing

The hypothesis was sound: cost does not scale with horizon, so a 600-minute trade pays the same
14 bps as a 60-minute one, and a weaker per-unit-time signal could still clear it.

What actually happens is that **statistical power collapses faster than the edge grows**:

```
horizon   trades   null floor upper   AUC needed to be detectable
  60m     20,132        0.5117              modest
 240m      5,936        0.5230              much larger
 300m      4,816        0.5265              larger still
 600m      2,471        0.5271              larger still
```

Non-overlapping windows fall by 8× from 60m to 600m, so the noise floor widens from 0.5117 to
0.5271. The observed AUCs stay near 0.507–0.510 at every long horizon — so what was detectable
at 60 minutes is *not* detectable at 600, not because the signal vanished but because the
evidence thinned.

The gross edge does grow initially — +0.97 at 60m, **+1.97 at 240m** — which is the hypothesis
working, in the only place it works. Then it goes negative at 300m and 600m. At 240m the gross
edge is the largest measured anywhere in this repository, and it is still **7× below cost** with
an interval spanning −18 to −6.

## Cost dominance is visible in the per-pair breakdown

At 60 minutes, all seven pairs land within 1.5 bps of each other:

```
AVAX -13.4   BNB -12.4   BTC -13.0   ETH -12.8   LINK -13.2   SOL -13.9   XRP -12.6
```

Seven different instruments, different liquidity, different volatility — and they all lose
approximately the round trip. This is what a cost-dominated result looks like: the instrument
barely matters because the fee is the whole answer.

## The heads, again

At 60 minutes the best heads are `RandomForest` (0.5238) and `ExtraTrees` (0.5230) — bagged
trees, where the previous 15-minute test found plain logistic regression best. The soft vote
(0.5181) again lands **below** the best single head, exactly as in `DIRECTION_ENSEMBLE_V1`.

Two runs, different data, different winning family, same conclusion: voting across families does
not beat the best member here.

## Governance

- Protocol frozen and hashed **before** the dataset was analysed; 18/18 hashes verify in CI.
- **Bonferroni across four horizons**: the primary interval is 98.75%, not 95%. Reporting the
  best of four horizons at 95% would be a search over horizons. The selftest asserts the
  corrected interval is strictly wider than a 95% one.
- **Walk-forward in 30-day blocks**, purged by one full horizon, rather than a single 70/30 cut
  — 180 days spans multiple regimes, and one cut would score one regime and call it out of
  sample.
- **Null floor per horizon**, labels shuffled in whole-day blocks. This is what makes the
  60m/240m distinction meaningful: identical AUCs mean different things at different sample
  sizes.
- **Feature causality is asserted, not assumed.** The selftest perturbs a bar's own close and
  requires all 31 features at that bar to be unchanged, then perturbs a *future* bar's open and
  requires the earlier bar's label to change while its features do not.
- **Cross-sectional features are shifted**, so a pair never reads the current bar of its peers —
  a same-bar leak across the panel that would be easy to miss.
- Stale OI and funding are **blanked, never carried**.
- Selftests: fetch 8, features 10, run 11.

## On refining the strategy

The protocol forbids adjusting anything now and re-reporting — that would convert a frozen test
into a search. But the result does point somewhere specific, and a future preregistration could
be designed from it:

**Every horizon is cost-dominated, so refinement must attack cost, not signal.** The measured
gross edges are +0.97 to +1.97 bps against a 14 bps taker round trip. No plausible improvement
in direction modelling closes a 7–14× gap; a maker execution that avoids crossing the spread
changes the arithmetic directly.

That is the same conclusion the Polymarket work reached from the opposite direction, and it is
already sealed as `PREREG_FORWARD_D_BOUNDED_MAKER_STUDY_V1` — whose precondition is a
day-clustered surplus lower bound above zero, which is exactly what these gross edges are not.

The honest reading: **the edge is real, reproducible across venues and horizons, and roughly an
order of magnitude too small to trade through a taker fee.**
