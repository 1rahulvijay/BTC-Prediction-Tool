# Research Results — V1–V31, measured

**Supersedes `ULTIMATE_V1_V24_RESULTS_MASTER.md`, which is untracked and still quotes figures
that reversed under measurement (+5,209,276%, 100% win rate, +32,598%).**

Reproduce everything here with:

```bash
python research/run_all_sequence.py
```

---

## What the suite established

```
scripts run                      : 31
exited non-zero                  : 0
with a real out-of-sample number : 16
POSITIVE out-of-sample            : 0
```

Sixteen distinct mathematical approaches — GMM regimes, Kalman filtering, Hawkes
self-excitation, Fourier cycles, fractional differencing, genetic search, mutual-information
selection, spiking events, regime transitions, momentum, mean reversion, volatility breakout —
all produce negative out-of-sample returns on 1-minute bars at 15-minute horizon with 9 bps
round-trip costs.

Sixteen unrelated methods failing the same way is not sixteen coincidences. It is one fact
about the horizon and the cost structure.

---

## THE CEILING, MEASURED

### The hurdle

| horizon | median abs move | cost | move / cost | taker viable? |
|--------:|----------------:|-----:|------------:|:--------------|
| 1m | 2.4 bps | 9.0 | 0.26 | **no** |
| 5m | 5.4 bps | 9.0 | 0.60 | **no** |
| 15m | 9.6 bps | 9.0 | 1.06 | **no** |
| 30m | 13.7 bps | 9.0 | 1.53 | marginal |
| 60m | 19.7 bps | 9.0 | 2.18 | marginal |
| 240m | 39.3 bps | 9.0 | 4.37 | yes |
| 1440m | 118.5 bps | 9.0 | 13.17 | yes |

**At 5m and 15m the typical move is roughly the same size as the round-trip cost.** To break
even you must capture essentially the whole move with the correct sign, every time. No model
achieves that, and no model can — the constraint is arithmetic, not statistical.

### There is no gross edge to amplify

Out-of-sample, before costs are deducted at all:

| strategy | trades | gross bps/trade | net after 9 bps |
|---|---:|---:|---:|
| momentum | 40,945 | −0.14 ± 0.1 | −9.14 |
| mean-reversion z60 | 7,776 | −0.21 ± 0.3 | −9.21 |
| volatility breakout | 2,751 | −0.05 ± 0.6 | −9.05 |
| Kalman fade | 8,795 | −0.12 ± 0.3 | −9.12 |

Gross edge is statistically indistinguishable from **zero**. This is not "we have signal but
costs eat it" — there is no directional signal in these features at this horizon to begin with.

**This is why more model sophistication cannot help.** A better estimator of a quantity that is
zero is still zero.

---

## What actually moves the ceiling, ranked by measured leverage

### 1. Maker execution — a 6× improvement in the hurdle, no prediction change

| horizon | taker move/cost | **maker move/cost** |
|--------:|----------------:|--------------------:|
| 15m | 1.06 | **6.39** |
| 60m | 2.18 | **13.11** |
| 240m | 4.37 | **26.20** |

Not crossing the spread changes 15-minute trading from structurally impossible to structurally
possible. That is a larger effect than any model improvement in this entire suite, and it is an
execution problem, not a prediction problem.

**Blocked on:** passive fill modelling requires queue position, which requires sequenced L2
depth. The recorder stores top-of-book only — see `backend/venues/rl_data_readiness.py`.

### 2. Longer horizons — free, available today

At 240m the move is 4.4× the cost even as a taker. Every script in this suite tested 15
minutes. Nothing prevents testing 60m and 240m on existing data, and the hurdle table says
that is where a real edge could survive.

### 3. A different target — the lane is binary contracts, not direction

All 16 strategies predict **price direction**. The actual lane is Polymarket 5m/15m binary
contracts, where profit comes from **mispricing against settlement**, not from being right
about direction. A contract quoted at 0.40 that settles true 50% of the time pays without any
directional forecast at all. That is a different estimand and was never tested here.

### 4. Fewer, better trades

Momentum took 40,945 out-of-sample trades. At 9 bps each, that is ~368% of capital spent on
costs. Selectivity is worth more than accuracy when the hurdle is this close to the move size.

---

## What is refused, and why

| script | status | reason |
|---|---|---|
| v4 Breeden–Litzenberger | **BLOCKED** | Original computed a density from a *simulated* BS chain and called it "true market-implied probability". No Deribit per-strike chain is stored; Deribit's shortest BTC expiry is daily while this lane trades 5m/15m |
| v6 persistent homology | **BLOCKED** | A book at one instant is a monotone price axis with no non-trivial loops — Betti-1 "liquidity holes" do not exist in that object. Also no depth data |
| v7 Fisher–Rao geodesic | **BLOCKED as framed** | No order-book density, no defined crash state. Buildable as a distribution-shift *monitor*; not as a crash oracle |
| v10 GNN / L2-CNN | **BLOCKED** | No depth stream to build a graph or image from |

These print a refusal and produce **no number**, because inventing the missing data is exactly
what produced the results that reversed.

---

## Corrections carried into the code

- **Accounting.** `capital += 1000.0 * bps` on a fixed notional let accounts run past zero,
  producing −212%, −834%, −879%, −10032%. Stake is now a fraction of current capital, floored
  at zero: those became −20.79%, −39.13%, −5.30%, −52.70%. The losses were real; the
  magnitudes were fiction.
- **Units.** v3 reported mutual information as "bits". sklearn returns **nats**; a bare
  `mi < 0.05` is not a 0.05-bit rule. Both units are printed now.
- **Labelling.** v14 cannot test *cross-venue* lead-lag from a single-venue archive. It
  measures within-series lead-lag and says so.
- **Scope.** `research/harness.py` makes the chronological split and cost model mandatory, so
  three of the four audit patterns are structurally impossible rather than merely discouraged.

`backend/research/audit_research_claims.py` runs in CI and flags the four disqualifying
patterns in any future script.

---

## Standing constraint

None of this is wired into the application, and none of it may be without passing the
repository's normal promotion gates: preregistered protocol, fold-local fitting, untouched
chronological evaluation, day-block lower confidence bounds, complete cost model, forward
shadow evidence, and no automatic promotion. Real orders remain disabled
(`backend/trading_authority.py`).
