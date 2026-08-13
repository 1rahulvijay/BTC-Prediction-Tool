# SPOT_PERP_BASIS_V1

**Verdict: CLOSE LANE.** Mean reversion is real, consistent and statistically clean — and about
**15x too small** to pay for the trade.

Run 2026-08-13 · `research_lanes/run_matrix_lanes.py`

---

## Question

> When perp-spot basis is extreme, does it revert by more than the round trip?

Distinct from funding carry. This is a dislocation trade: short the rich leg, long the cheap
one, capture the convergence.

## Method

518,400 one-minute bars, 360 UTC days, `perp_spot_basis_bps` from the research matrix.

- **Rich** = basis at or above its 95th percentile
- **Cheap** = basis at or below its 5th percentile
- Reversion = change in basis over the next 5 / 15 / 30 bars, signed so that **positive means
  it moved toward fair**
- Day-block bootstrap, 400 draws

## Result

| horizon | rich reversion | LCB | clears 12bps? | cheap reversion | LCB | clears 12bps? |
|---|---:|---:|---|---:|---:|---|
| 5m | +0.68 bps | +0.62 | **no** | +0.66 bps | +0.61 | **no** |
| 15m | +0.80 bps | +0.72 | **no** | +0.78 bps | +0.71 | **no** |
| 30m | +0.89 bps | +0.79 | **no** | +0.87 bps | +0.80 | **no** |

The reversion is **real**: every lower bound is positive, on both tails, at all three horizons.
Basis genuinely pulls back toward fair. The effect is also pleasingly symmetric — rich and cheap
revert by almost identical amounts (0.68 vs 0.66, 0.80 vs 0.78, 0.89 vs 0.87), which is what a
genuine mean-reverting process looks like rather than a fitting artifact.

It is also **0.89 bps at best, against a 12 bps round trip.** Off by a factor of about 15.

## Why it does not become tradeable by waiting

Reversion grows sublinearly: 0.68 → 0.80 → 0.89 as horizon goes 5m → 15m → 30m. Extrapolating
that curve, reaching 12 bps would require holding far beyond any horizon where the basis is
still recognisably the same dislocation — and carrying directional risk the whole time, since
the legs are only approximately hedged in this measurement.

## What would change the verdict

- **Much lower costs.** At a 0.5 bps round trip (deep maker on both legs), the 30m LCB of
  0.79 clears. That is an execution problem, not a signal problem.
- **A more extreme tail.** This used p95/p05. p99 dislocations were not measured separately and
  would be larger — though also rarer, so the capacity question arrives immediately.
- **Funding.** This lane measured **basis convergence only**. `FUNDING_BASIS_CARRY_V1` is a
  different hypothesis: funding is a periodic cash transfer, not a price convergence, and it
  accrues without needing the basis to move. Nothing here bounds it.

## What this does NOT say

It does not say the perp-spot relationship is uninformative. `perp_spot_basis_bps` may still be
a useful *feature* — this lane tested it as a standalone trade, and only that.

## Attacks applied

| attack | status |
|---|---|
| day-block bootstrap | yes — 360 independent days, 400 draws |
| both tails tested | yes — rich and cheap, independently |
| multiple horizons | yes — 5 / 15 / 30m |
| economic screen | yes — against the shipped 12 bps |
| cost stress | implicit — the answer is a factor of 15, not a marginal call |
| regime breakdown | **not done** — unlikely to close a 15x gap |

## Caveat

Basis here is the matrix's `perp_spot_basis_bps` column, not an independently reconstructed
spread from paired books. If that column has any construction error, this lane inherits it. It
was not audited for this run.
