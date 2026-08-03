# Regime volatility control — result

**Protocol** `PREREG_REGIME_VOLATILITY_CONTROL_V1.md` sha256 `4d504551…`, frozen `2026-08-03`
before any conditional result · **Script** `research/regime_volatility_control_v1.py` ·
Scored **once**

## Result

155,427 test bars, volatility deciles fitted on train only.

| pair | unconditional | within-volatility | shrinkage | deciles | day-block 95% CI |
|---|---:|---:|---:|---:|---|
| TRENDING vs RANGE | +6.92 | **+1.11** | **16%** | 5 | [+0.20, +2.01] |
| RANGE vs THIN_LIQUIDITY | +5.74 | +0.69 | 12% | 4 | [−0.15, +1.33] |
| TRENDING vs THIN_LIQUIDITY | +12.66 | – | – | 0 | *insufficient* |

**VERDICT: `REGIME_ADDS_WEAKLY`.** The strongest surviving pair keeps **16%** of its
unconditional gap — below the 25% materiality bar declared in advance.

## What this means

**84% of the apparent regime effect was volatility.** The regime labeler's headline —
`THIN_LIQUIDITY < RANGE < TRENDING`, three cleanly separated intervals, ordering stable across
the split — was substantially a restatement of volatility clustering, exactly as suspected when
that result was published.

What survives conditioning is **+1.11 bps** of forward absolute move between `TRENDING` and
`RANGE`. Its interval excludes zero, so something is there. It is also:

- **8% of the cost of acting on it.** A Bybit round trip is 14 bps; the Polymarket cost floor
  measured in this repository is ~149 bps. A 1.11 bps conditional difference is economically
  irrelevant on either venue.
- **inconsistent in sign across strata.** Decile 9 shows −0.31 bps while deciles 5–8 are
  positive. An effect that reverses in the highest-volatility band is not a stable state
  description.
- **restricted to half the volatility range.** Only deciles 5–9 qualified, because `TRENDING`
  requires `rv_60m ≥ P50` and therefore cannot appear in the bottom half at all.

`RANGE vs THIN_LIQUIDITY` does not survive: its CI spans zero.

`TRENDING vs THIN_LIQUIDITY` — the largest unconditional gap at +12.66 bps — could not be
computed in a single decile. The two regimes never co-occur in the same volatility band, which
is itself the finding: that pair *is* a volatility contrast, with no residual to measure.

## The pre-declared materiality bar did the work

Without the 25% threshold frozen in advance, this result could honestly have been reported as
*"TRENDING vs RANGE survives conditioning on volatility, 95% CI [+0.20, +2.01], excludes zero."*
Every word true, and a router built on it would be routing on 1.11 bps against a 14 bps cost.

Statistical significance on 155,427 bars is cheap. The bar that mattered was materiality, and it
had to be set before the number was known.

## Consequence: the Strategy Router is not built on this taxonomy

Per the protocol's kill rule. Routing between states that differ by 1.11 bps — inconsistently,
across half the volatility range, against a 14 bps cost — would be decoration over a volatility
model the system already has.

This does not retire the router idea. It retires **this basis** for it. A regime definition that
does not use volatility thresholds could carry independent information, and would need its own
preregistration. Candidates that avoid the circularity: order-flow imbalance, open-interest
change, funding, and basis — none of which is a volatility threshold, and three of which are now
fetchable from Bybit.

## Governance

- Protocol frozen and hashed **before** the script was written; 13/13 hashes verify in CI.
- Decile edges fitted on **train only**.
- Day-block bootstrap resamples whole **days** and recomputes the entire stratified statistic —
  bars within a day share both regime and volatility, the two things being separated.
- Only the three pairs that separated unconditionally were examined; no new pair was searched.
- Selftest, 12 checks, includes **both** controls:
  - a constructed world where forward move depends on volatility alone → the gap collapses and
    the restatement **is** detected;
  - a constructed world with an effect independent of volatility → the gap **survives**, so the
    control is not vacuously null.

The first fixture I wrote made regime a perfect step function of `rv`. That is degenerate: it
places one regime per stratum, so no stratum can be compared and the statistic is undefined.
Corrected to a noisy threshold, which is the real situation — `TRENDING` also requires
efficiency ≥ 1.0, so an `rv` stratum genuinely contains both regimes. The degenerate case is now
its own assertion.

## Where this leaves the plan

```
Regime labeler          separable, but 84% of it is volatility  -> not a router basis
Strategy Router         NOT BUILT
Tradability head        unaffected, independent of this result  -> the next step
```

The tradability / remaining-move head never depended on regimes and can proceed. It targets
magnitude, which Phase 5 already identified as where the exploitable structure is (sign AUC
0.87, magnitude AUC 0.58 — the gap that matters is in magnitude).
