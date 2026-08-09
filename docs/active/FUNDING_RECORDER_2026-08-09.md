# Funding recorder — and the carry verdict it changed

**Date:** 2026-08-09
**Built:** `backend/funding_recorder.py`
**Changed:** `research/market_neutral_carry_lane.py` (section 2 was "UNMEASURED", now measured)
**Data:** `data/funding.duckdb` — 3,500 real settlements, 2023-05-31 .. 2026-08-09

---

## Why this existed as a gap

The eight-lane sweep closed seven lanes. The eighth, market-neutral carry, split into two
terms and could only answer one:

| term | status before today |
|---|---|
| basis convergence | **CLOSED** — 2.89 bps p05→p95 range against a 24 bps four-leg round trip |
| funding | **UNMEASURED** — `funding_velocity` 90% zeros, `binance_paper_funding_events` 0 rows |

Funding is the dominant term in a real carry book, and this repository had never recorded it.
Carry was the only lane blocked on data collection rather than on economics.

## The timeline assumption was wrong, in our favour

The carry study's own remedy said *"record the actual funding rate and mark, every 8h, for a
few months."* That framed this as a months-long forward wait. It is not:
`/fapi/v1/fundingRate` serves **history**. Three years came back in one run, and the carry
question became answerable the same day.

## What it records

Public endpoints only. No credentials, no orders, forward-only writes.

```
/fapi/v1/fundingRate    fundingTime, fundingRate, markPrice      (history, paginated)
/fapi/v1/premiumIndex   markPrice, indexPrice, nextFundingTime   (current state)
```

| table | holds |
|---|---|
| `funding_settlements` | one row per 8h settlement, rate in raw AND bps, mark at settlement |
| `funding_basis_samples` | mark, index, premium, basis_bps, seconds to next funding, transport lag |
| `funding_gaps` | missing settlements and endpoint failures |
| `funding_heartbeats` | liveness + counters + `endpoints_healthy` |
| `funding_runs` | host, platform, endpoints, provenance note |

Conventions mirror `btc_tick_recorder.py` / `l2_recorder.py`: single-writer DuckDB, `seq` PK
resumed from `max()`, `recv_ts_ns` and `exchange_ts_ms` kept apart.

### Gap detection is by TIME, not by an id

Funding carries no per-message counter. Settlements land on a fixed 00:00/08:00/16:00 UTC
schedule, so a hole is detectable only by spacing. This is the distinction the tick recorder
got wrong once — it read `bookTicker`'s `u` as a per-message counter when it is an
order-book update id across all levels, and reported 366 false gaps. Using an id-continuity
test here would have repeated that. Measured: **0 schedule holes in 3,500 settlements.**

---

## The measurement

```
mean  +0.6637 bps/8h  ->  +1.991 bps/day  ->  +7.27% annualized on notional
positive 85.2% of settlements       sign flips 486
```

The sign is right and the magnitude clears the execution cost. **That is the first thing in
this entire sweep that does**, so it got checked harder rather than celebrated — and the
check found a cost the study had not counted.

### The two terms are not independent, and that costs money

The file header used to call basis and funding "two independent P&L terms". Recording the
basis disproved it. Binance *derives* funding from the premium:

```
F = premium + clamp(interest - premium, -0.05%, +0.05%)      interest = 0.01% per 8h
```

Checked against live samples, predicted vs actual agreed in sign and to within ±0.5 bps. The
residual is expected — `lastFundingRate` is a TWAP of the premium over the prior window, not
the instantaneous sample.

The consequence: recorded mark-vs-index sits at **−4.76 bps**, i.e. the perp trades *below*
spot. A funding-collecting hedge is long spot and short perp, so it **sells the perp below
spot** and returns exactly that difference on convergence. The entry basis is a **4.76 bps
cost of the funding-collecting direction, not a gain.** The reverse hedge captures it but
then pays funding instead of collecting it. One or the other, never both.

True hurdle: **24.0 bps (four legs) + 4.8 bps (adverse entry basis) = 28.8 bps.**

| hold | mean net | median | profitable |
|---:|---:|---:|---:|
| 5d | −18.79 bps | −20.65 | 4.8% |
| 15d | +1.16 bps | −4.52 | 38.4% |
| 30d | +31.21 bps | +19.43 | 70.9% |
| 60d | +91.44 bps | +57.75 | 88.1% |
| 90d | +152.60 bps | +106.23 | 91.5% |

**Breakeven: 14.4 days of holding before the first cent.**

### A side effect worth keeping

The live basis median of −4.76 bps **independently corroborates** the research matrix's
`perp_spot_basis_bps` (monthly means −4.46 to −4.55), a column whose construction could never
be verified and whose persistent negative level nobody had explained. It is now both
confirmed by direct measurement and explained by the funding formula.

---

## The two things that decide it

**REGIME.** The rate is not a constant, and a three-year mean hides that.

```
rolling 90d annualized    min -1.58%   median +5.47%   max +23.28%   latest +4.76%
last 180d                 +1.76% annualized, 34.4% of settlements NEGATIVE
a 30-day hold in THAT regime nets -16.27 bps, only 32.1% profitable
```

More than an order of magnitude between best and worst 90-day windows. I initially read the
yearly means (2024 +11.9% → 2025 +5.1% → 2026 +2.1%) as a decaying edge. **The rolling window
refutes that** — it oscillates, and the low 2026 mean was a trough that has since recovered.
This is a risk premium that varies with leverage demand, not a decaying inefficiency and not
a constant yield.

**SCALE.** Configured Binance paper allocation is $500. An unlevered hedge needs capital on
both legs, so matched notional is $250:

| | rate | per year | per day |
|---|---:|---:|---:|
| 3.2y mean | +7.27% | $18.17 | $0.050 |
| last 180d | +1.76% | $4.41 | $0.012 |

---

## Verdict

**The funding half is OPEN but bounded.** It is the only lane of the eight not closed, and it
is not closed because the arithmetic works. It is real, positive, and clears its cost at a
long enough hold — and it is single- to low-double-digit dollars per year at this capital,
earned by holding a position through leverage-demand shocks. It is a well-known risk premium,
not a mispricing anyone is missing. Small, not absent.

---

## Defects found and fixed while building this

**1. Heartbeat suppressed on endpoint failure made a live recorder read STALLED.**
`recorder_health.STALL_AFTER_MS` is a single 15-minute threshold across every recorder, and
the loop only wrote a heartbeat when *all* endpoints succeeded. Since `history_ok` persists
across the 300s refresh interval, one failed history fetch suppressed **every** heartbeat
until the next refresh — long enough to cross the stall threshold while the process was alive
and successfully sampling basis. Same defect class this repo keeps finding: a check reporting
on the wrong property.

Fixed by separating the two facts. The heartbeat is now unconditional (it answers "is this
process alive"), and endpoint health rides along as `endpoints_healthy` while failures
continue to be recorded as gap rows. The fix belongs in the recorder, not in a weakened
threshold.

**2. The cadence assertion had no teeth.** `stall_budget_seconds()` now *imports*
`recorder_health.STALL_AFTER_MS` rather than restating it, so tightening that threshold below
this recorder's poll cadence breaks the selftest here instead of silently turning a healthy
recorder red.

Both fixes are mutation-tested — reverting either makes the selftest fail:

```
CAUGHT  suppress heartbeat when an endpoint is failing (the original defect)
CAUGHT  slow the poll cadence past the stall threshold
```

**3. Scale arithmetic treated capital as notional.** The first version multiplied the
annualized rate by total capital; a cash-and-carry needs capital on both legs, so deployable
notional is about half. Corrected — which makes the finding smaller, the honest direction.

---

## Still open, and not fixed here

`start.bat` only runs `--selftest` on the recorders — it never *starts* them. The evidence
audit's `wired_recorders()` regex matches those selftest lines, so a recorder counts as
"wired into the launcher" on the strength of a selftest invocation alone. That is exactly the
gap the evidence check's own footer names:

> A passing selftest proves the code is correct. It says nothing about whether the process
> ever started, which is the question that went unasked for weeks.

Current state: 3 recorders NEVER_RAN, 6 STALLED, 1 ADVANCING (this one, because it was just
run by hand). Left as-is — changing what `start.bat` launches is a bigger decision than this
turn's scope.

---

## Running it

Backfill three years, then run as a daemon:

```bash
python backend/funding_recorder.py --backfill-days 1100 --forever
```

Re-read the verdict at any time:

```bash
python research/market_neutral_carry_lane.py
```
