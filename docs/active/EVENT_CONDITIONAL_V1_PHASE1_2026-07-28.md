# BINANCE_EVENT_CONDITIONAL_PROFIT_V1 — Phase 1

**2026-07-28 · research only · no models, no promotion, no orders**

Phase 1 delivers the data contract, the event labels, executing tests, and a readiness
report. The readiness report is **empty by design**: the machinery exists, the evidence
does not. `PROFIT_CAMPAIGN_V1` was not altered, rerun, or imported.

---

## 1. The measurement that shaped this protocol

Before writing any detector, one question was answered from data already on disk:
**at each horizon, what fraction of timestamps can a trade clear the round-trip cost?**

```text
P(|move over h| > round_trip_cost)
```

This upper-bounds the fraction of timestamps at which a **perfect-direction oracle**
could profit. A real model is strictly worse. Measured over **129 days sampled from
2023-01-16 to 2026-07-24** (BTCUSDT perp aggTrades, 1s last-price grid, endpoint move):

| horizon | median \|move\| | taker @ 12 bps | maker @ 6 bps | maker @ 4 bps |
|---:|---:|---:|---:|---:|
| 30s | 1.97 bps | **2.49%** | 12.12% | 22.97% |
| 60s | 2.93 bps | 5.94% | 21.46% | 35.15% |
| 180s | 5.18 bps | 16.97% | 40.44% | 55.12% |
| 300s | 6.69 bps | 24.55% | 49.84% | 63.49% |
| 900s | 11.59 bps | 44.04% | 67.70% | 77.69% |
| 1800s | 16.14 bps | 55.69% | 75.66% | 83.42% |
| 3600s | 22.65 bps | 66.44% | 82.38% | 88.07% |

Stable across every year — the 30s taker ceiling is 1.94% (2023), 3.36% (2024),
2.08% (2025), 2.60% (2026). **The infeasibility of short taker horizons is structural,
not regime-specific.**

### What this explains about V1

`PROFIT_CAMPAIGN_V1` sampled every 15 seconds and traded a 30-second horizon at a
12 bps round trip. At that horizon the oracle ceiling is 2.49%, so **~97.5% of its
entries could not profit under any model, however good** — the cost exceeded almost
the entire 30-second move distribution. Its measured profit factor was exactly
`0.0000` across 374 trades. That is not a weak signal; it is arithmetic.

This reframes the two ways forward. At 30s, moving the round trip from 12 bps to
4 bps lifts the ceiling from 2.49% to 22.97% — a **9× increase in tradeable
opportunity with no improvement in prediction at all.** On this evidence the execution
lever dominates the alpha lever.

Reproduce with:

```bash
python backend/research/event_conditional_v1/measure_horizon_viability.py --stride 10
```

Raw per-day output: `data/research/event_conditional_v1_horizon_viability_129d.json`.

**Stated limitation:** this uses *endpoint* move, not maximum favorable excursion. A
strategy with a take-profit can capture moves that round-trip back, so the true ceiling
is somewhat higher and these numbers are a conservative floor. At 30s that changes
nothing; it matters for the middle horizons.

---

## 2. The horizon gate

The floor is **20%**: below it, an oracle's ceiling leaves no room for a non-oracle
model. The gate is mechanical and raises rather than warns.

| execution | admissible | refused |
|---|---|---|
| taker | 300s, 900s, 1800s, 3600s | 30s (2.49%), 60s (5.94%), **180s (16.97%)** |
| maker | 60s, 180s, 300s, 900s, 1800s, 3600s | 30s (12.12%) |

180s taker at 16.97% is a **near miss, and a near miss is a miss.** The floor was not
moved to admit it. The admissible lists are *derived* from the floor, and a selftest
assertion compares the two on every horizon so they cannot drift apart — that assertion
is what caught them disagreeing when this protocol was first written.

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
| `viability.py` | the horizon gate |
| `readiness.py` | the (empty) readiness report |
| `measure_horizon_viability.py` | the measurement above |
| `selftest.py` | 40 executing assertions |

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

**The 60–90 day clock is at day zero.** `venue_events` has never recorded a row, and
nothing is currently running. Note `start_recorder.bat` launches the *Polymarket*
recorder — not this one. The multi-venue recorder is started with:

```bash
python backend/venues/multi_venue_recorder.py
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
event_conditional_v1.selftest      exit 0   (40 assertions)
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
