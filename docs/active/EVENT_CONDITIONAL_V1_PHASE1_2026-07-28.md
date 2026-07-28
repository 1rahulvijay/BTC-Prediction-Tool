# BINANCE_EVENT_CONDITIONAL_PROFIT_V1 — Phase 1

**2026-07-28 · research only · no models, no promotion, no orders**

Phase 1 delivers the data contract, the event labels, executing tests, and a readiness
report. The readiness report is **empty by design**: the machinery exists, the evidence
does not. `PROFIT_CAMPAIGN_V1` was not altered, rerun, or imported.

---

## 1. The measurement that shaped this protocol

Before writing any detector, one question was answered from data already on disk:
**at each horizon, what share of anchors can clear the round-trip cost at all?**

```text
endpoint cost-clearance rate
  = P(|endpoint move over h| > round_trip_cost)
```

This is the share of anchors at which a position held exactly `h` and closed **at the
endpoint** would clear the assumed round trip, given perfect direction. It is **not a
profitability ceiling** - it excludes maximum favourable excursion, stop/target exits,
variable holding periods, spread and depth changes, funding, partial fills, and maker
fill probability and adverse selection. It screens one execution assumption: fixed
horizon, endpoint exit.

Measured over **129 of 1,286 available days, 2023-01-16 to 2026-07-19** (BTCUSDT perp
aggTrades, 1s last-price grid). Admission uses the **day-block bootstrap 95% lower
bound**, never the point estimate - anchors within a day overlap heavily, so row counts
vastly overstate independent sample size:

| horizon | median \|move\| | taker 12bps (pt / **LB95**) | maker 6bps (pt / **LB95**) |
|---:|---:|---:|---:|
| 30s | 1.97 bps | 2.49% / **2.08%** | 12.12% / **10.80%** |
| 60s | 2.93 bps | 5.94% / **5.12%** | 21.46% / **19.59%** |
| 180s | 5.18 bps | 16.97% / **15.29%** | 40.44% / **38.07%** |
| 300s | 6.69 bps | 24.55% / **22.52%** | 49.84% / **47.53%** |
| 900s | 11.59 bps | 44.04% / **41.59%** | 67.70% / **65.72%** |
| 1800s | 16.14 bps | 55.69% / **53.25%** | 75.66% / **73.96%** |
| 3600s | 22.65 bps | 66.44% / **64.16%** | 82.38% / **81.03%** |

The low 30s taker rate was reproduced in sampled days from **every** calendar year —
1.94% (2023), 3.36% (2024), 2.08% (2025), 2.60% (2026). That supports broad temporal
robustness. Calling it *structural* remains a well-supported hypothesis rather than a
proven property: 129 of 1,286 available days were sampled.

**A 4 bps maker scenario is measured but marked `SENSITIVITY_ONLY_NOT_ADMISSIBLE`.**
It assumes an all-in 4 bps cost that is both achieved *and* filled, which cannot be
claimed before queue-aware forward evidence covers fill probability, queue position,
partial fills, adverse selection, cancel latency and taker fallback. No lower bound is
even published for it, so it cannot be gated on.

### What this explains about V1

`PROFIT_CAMPAIGN_V1` sampled every 15 seconds and traded a 30-second horizon at a
12 bps round trip. At that horizon the clearance rate is 2.49%, so **97.51% of its
anchors could not produce positive endpoint PnL under its frozen fixed-horizon
execution assumptions, even with perfect directional foresight.**

Its measured profit factor was exactly `0.0000` across 374 trades. The cost geometry
makes that **economically unsurprising for an indiscriminate 15-second system** — but it
does not mathematically force it. A sufficiently selective signal could in principle
have traded only the eligible 2.49%. The honest claim is that the design and the result
are consistent, not that the arithmetic predicted the outcome.

This does reframe the two ways forward. Cost sensitivity has large theoretical leverage:
at 30s, a 12 → 4 bps round trip lifts clearance from 2.49% to 22.97%. But that 9.2×
figure holds **only under the assumed all-in 4 bps condition, and only if the order is
still filled.** It shows execution cost is a powerful lever; it does not yet establish
that maker execution beats alpha improvement, because real maker performance must carry
fill probability, queue position, time to fill, partial fills, adverse selection, missed
opportunity cost and taker fallback.

Reproduce with:

```bash
python backend/research/event_conditional_v1/measure_horizon_viability.py --stride 10
```

Raw per-day output: `data/research/event_conditional_v1_horizon_viability_129d.json`.

**Stated limitations.** (1) *Endpoint* move, not maximum favourable excursion - a
take-profit strategy can capture excursions that round-trip back, so these rates
understate what a variable-exit strategy could reach. (2) An `abs(move)` screen: the
terminal version must compute perfect-long and perfect-short net returns separately
through the executable ask/bid with fees on the actual entry and exit notionals, rather
than folding spread and fee arithmetic into one constant. (3) A 1-second grid, so no
sub-second claim may rest on it.

**Dataset role: `DESIGN_ONLY`.** These 129 days *selected* the horizons and cost
scenarios, so they can never serve as untouched final evidence for those same choices.
The sample manifest - sampled/available days, per-year counts, sha256 of the day list,
bootstrap seed and resamples - is recorded in the protocol. The future 60-90 day
qualifying collection is the first untouched test.

---

## 2. The horizon gate

The floor is **20% on the day-block LB95**: below it, too few anchors clear cost even
with perfect direction for a real model to have room. The gate is mechanical and raises
rather than warns.

| execution | admissible (LB95 ≥ 20%) | refused |
|---|---|---|
| taker | 300s, 900s, 1800s, 3600s | 30s (LB 2.08%), 60s (5.12%), **180s (15.29%)** |
| maker | 180s, 300s, 900s, 1800s, 3600s | 30s (10.80%), **60s (19.59%)** |

Two refusals are worth naming.

**180s taker** at LB95 15.29% is a near miss, and a near miss is a miss. The floor was
not moved to admit it.

**60s maker** is why the lower bound is load-bearing rather than decorative: its point
estimate is 21.46% (**passes**) and its LB95 is 19.59% (**fails**). Admission follows
the lower bound, so it is excluded. A selftest asserts that inversion still holds, so
the gate cannot be quietly switched back to point estimates.

Admissibility is necessary but not sufficient. Admitting every eligible horizon
multiplies trials, so the grid is **capped at three per execution style**, chosen
deterministically as shortest / middle / longest eligible:

```text
taker   300s,  900s, 3600s
maker   180s,  900s, 3600s
```

The admissible lists are *derived* from the floor, and a selftest assertion compares the
two on every horizon so they cannot drift apart — that assertion is what caught them
disagreeing when this protocol was first written.

Unmeasured horizons are **refused**, not assumed viable.

---

## 3. Files

| file | purpose |
|---|---|
| `frozen_protocol.json` | preregistration: families, costs, gate, validation, promotion, stopping rule |
| `contracts.py` | typed `Action` / `FillStandard` / `DataQuality` / `Family`; no free-form decision strings |
| `execution.py` | Binance notional fees, ladder walk, causal book selection, maker/taker outcomes |
| `data_contract.py` | required streams per family, gap segmentation, live archive evaluation |
| `event_detectors.py` | the four families, fail-closed |
| `viability.py` | the horizon admission gate (LB95) |
| `readiness.py` | the (empty) readiness report |
| `measure_horizon_viability.py` | the measurement above |
| `selftest.py` | 55 executing assertions |

---

## 4. The fee trap, made explicit

`executable_surface_config.taker_fee` computes `rate * p * (1 - p)` with `p` **clamped
into [0, 1]**. At a perp price of 60000, `p` clamps to `1.0` and the fee is exactly
`0.0`. It does not raise. It silently removes every cost and inflates results by
precisely the quantity under study.

This campaign never calls it. `execution.binance_fee_usd` computes
`notional_usd * bps / 10_000`, and the selftest asserts both that the Polymarket
formula really does return `0.0` at 60000 and that the Binance fee at the same price
is nonzero and price-independent.

The Polymarket function and its callers were not modified — changing them would
re-price recorded `LATE_LEADER` and executable-surface results.

---

## 5. Readiness — current state

```text
status : NOT_READY
archive: data/multi_venue.duckdb   rows=0   span=0.00d   (need >= 60d)

LIQUIDATION_CONTINUATION  NOT_READY   binance_perp/{bookTicker,aggTrade,forceOrder}, span
LIQUIDATION_EXHAUSTION    NOT_READY   binance_perp/{bookTicker,aggTrade,forceOrder}, span
CROSS_VENUE_LEAD_LAG      NOT_READY   + binance_spot/*, bybit_perp, coinbase_spot, span
FUNDING_BASIS_OI          NOT_READY   + binance_perp/{markPrice,openInterest}, span

RESULTS: 0
```

**The 60–90 day clock is at day zero.** `venue_events` has never recorded a row.

The collector **is** correctly wired: `start.bat:383` invokes
`backend/start_recorders_once.ps1`, which launches `multi_venue_recorder.py` unless
`BTC_SKIP_VENUE_COLLECTOR=1`. So this is not a wiring defect. Diagnosis on this host:

```text
data/multi_venue_recorder.stdout.log   absent
data/multi_venue_recorder.stderr.log   absent
BTC_SKIP_VENUE_COLLECTOR               unset
BTC_DATA_DIR / BTC_VENUE_DB            unset (defaults apply)
```

No log files at all means `Start-Recorder` never ran — not that it started and crashed.
The launcher landed 2026-07-26 18:37 and `start.bat` has not been run since. The fix is
simply to run it; the collector then starts on its own.

Two operational notes. The qualifying continuous run belongs on the **always-on host**,
not a laptop — sleep produces non-qualifying episodes under the recorder's own 5-minute
episode rules. And `start_recorder.bat` is a *different* script that launches the
Polymarket recorder; it is not this collector.

Verify accrual with:

```bash
python backend/venues/multi_venue_recorder.py --report
```

Until that archive accrues, every family stays NOT_READY and no result may be reported.
No result is ever computed from the V1 one-day archive.

---

## 6. Design decisions worth keeping

- **WAIT is priced, not absent.** Every event records `WAIT` at exactly zero net PnL,
  so "nothing beat doing nothing" is a first-class row rather than a missing one.
- **Detection never claims direction.** A detector answers "did this event occur?" only.
  Direction is a later model, conditional on the movement-above-cost gate. V1's failure
  came partly from asking one model to pick a side at every timestamp.
- **Unfilled maker orders are recorded** with zero PnL and a missed-opportunity cost.
  Dropping them would bias the maker result upward by exactly the hard cases.
- **`TOUCH_PROXY` is structurally non-promotable** — carried on the outcome as
  `promotable=False`, not enforced by convention.
- **Missing inputs are named, never imputed.** A detector short an input returns
  `MISSING` with the input identified; nothing substitutes a proxy or a zero.
- **Quality precedence** is missing > stale > gap > ok, so the most disqualifying
  condition is the one reported.
- **A preregistered stopping rule** sits alongside the promotion gates: if all four
  families fail on the untouched period, that is recorded as the program's answer, and
  no V3 may reuse these families with adjusted parameters. A framework good at rejecting
  invites an unbounded V2/V3/V4 sequence; the stopping condition is declared in advance.

---

## 7. Validation

```text
event_conditional_v1.selftest      exit 0   (55 assertions)
event_conditional_v1.readiness     exit 0
profit_campaign_v1.selftest        exit 0   (unchanged - no collateral damage)
head_permissions --selftest        exit 0
venue_admissibility --selftest     exit 0
multi_venue_recorder --selftest    exit 0
test_collector_integrity           exit 0
test_paper_trading_integrity       exit 0
compileall event_conditional_v1    OK
workflow YAML                      VALID
git status backend/research/profit_campaign_v1/   clean (V1 unmodified)
```

Registered in CI in **both** jobs — `invariants` (ubuntu, bare command) and `startbat`
(windows, `|| exit /b 1`). These exit codes are local runs; GitHub Actions status is
separate and is only "verified" once a run appears against the commit.
