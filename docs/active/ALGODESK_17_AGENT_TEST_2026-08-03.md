# Algodesk 17-agent test — 30 days of BTC 1m bars

**Date** `2026-08-03` · **Script** `research/algodesk_17_agents_v1.py` · **Status** DIAGNOSTIC ONLY

A from-scratch implementation of the 17 agent rules published in the algodesk-bot README, run
as one continuous position state machine per agent over `data/research_matrix_1m.parquet`.
Not a promotion candidate, not a threshold search, scored once with everything frozen first.

## Result

```
window      2026-06-30 -> 2026-07-30   (43,201 1m bars, 30 days)
cost        12.0 bps round trip
exit        TP 100 bps / SL 50 bps / max hold 240m   (frozen before results)
guards      24h volume < $50M -> SKIP; 30m cooldown after each exit
```

| agent | trades | days | win% | mean bps | total bps | day-block 95% CI |
|---|---:|---:|---:|---:|---:|---|
| TREND | 0 | – | – | – | – | never fired |
| MOMO | 0 | – | – | – | – | never fired |
| BREAK | 27 | 21 | 22% | −29.8 | −806 | [−46.7, −14.7] |
| MEAN | 0 | – | – | – | – | never fired |
| VOL | 35 | 11 | 31% | −19.2 | −671 | [−32.1, −8.1] |
| SCALP | 137 | 29 | 29% | −22.9 | −3142 | [−31.4, −14.6] |
| LIQ | 0 | – | – | – | – | never fired |
| PAT | 40 | 19 | 38% | −17.2 | −690 | [−29.8, −5.1] |
| RANGE | 89 | 29 | 37% | −23.3 | −2073 | [−30.8, −15.0] |
| REGIME | 2 | 1 | 0% | −75.1 | −150 | *(one day — no CI)* |

**Six agents fired. All six lost, and every day-clustered CI lies entirely below zero.** This is
not a marginal result: the intervals do not touch zero, so it is not a sample-size verdict.

## Seven agents were not tested, and are not reported as neutral

The published conditions need four inputs. The archive has two:

| input | available | note |
|---|---|---|
| 24h change | yes | from 1m closes |
| day-range position | yes | rolling 24h high/low |
| 24h USD volume | yes | base-unit volume × close |
| **funding rate level** | **no** | only `funding_velocity`, a rate of *change* |
| **open interest** | **no** | absent entirely |

`funding_velocity` is not a stand-in for the funding rate. A velocity near zero is consistent
with any level, so the *sign* of a funding condition cannot be recovered from it. Substituting
it would have produced seven confident, meaningless numbers — and numbers get quoted.

```
FUND    needs funding rate level
OI      needs open interest
CONTRA  needs funding rate level
STAT    needs funding rate level, open interest
SENT    needs funding rate level
FLOW    needs open interest
OIDIV   needs funding rate level, open interest
```

## Every figure above is an upper bound

The published global guards block LONG when `fr > 0.005` and SHORT when `fr < −0.005`. Without
the funding level neither can be applied here. **Both guards only ever remove trades**, so the
specified system would have traded a subset of what was simulated. A negative result on a
superset does not become positive on a subset by itself, but the magnitudes are not the
specified system's magnitudes.

The volume guard (`< $50M → SKIP`) *was* applied and never bound: 24h volume ranged
$391M–$1,625M, median $1,056M.

## Why four agents never fired — the market, not my thresholds

Over the 30-day window BTC's 24h change stayed within **−4.20% to +5.61%**:

| threshold | share of bars |
|---|---:|
| \|chg24h\| > 5% | 0.13% |
| \|chg24h\| > 8% | 0.00% |
| \|chg24h\| > 10% | 0.00% |
| \|chg24h\| > 12% | 0.00% |
| \|chg24h\| > 20% | 0.00% |

TREND (>8%), LIQ (>10%), MOMO (>12%) and MEAN (>20%) were **unreachable**, not merely
unselected. Those thresholds describe a far more volatile instrument than BTC in this window —
plausibly altcoins, or an earlier BTC regime. On this instrument and this month they are inert,
and a longer window is the only way to learn anything about them.

This is worth separating from the six that lost: *"never fired"* and *"fired and lost"* are
different findings with different remedies.

## Frozen choices, declared before the first run

- **Conservative end of every published band.** Where the spec gives "change >5–8%", 8% is
  used. Taking the loose end makes each rule fire more often and is the first place a backtest
  starts quietly optimising.
- **Exit policy is mine.** The spec publishes sizing (`leverage = notional ÷ (balance ÷ max
  positions)`, `quantity = fixed risk ÷ stop distance`) but no take-profit or stop distances.
  TP 100 bps / SL 50 bps / max hold 240m are declared here, not discovered.
- **A max hold is mandatory.** Without it "dynamic exit" becomes indefinite holding: a losing
  position simply waits for recovery, which flatters any backtest and makes capital duration
  unmeasurable.
- **One continuous position.** Treating every bar as an independent hypothetical trade would
  report tens of thousands of "opportunities" one account could never have taken.
- **Executable pricing.** Entry pays half the round-trip cost, exit pays the other half.
- **Day-block bootstrap.** Trades inside one day share regime, volatility and the same 24h
  aggregates; a per-trade CI would be far too narrow. One day alone reports `nan`, not an
  interval.

## Causality

Every 24h aggregate (`high24`, `low24`, `close24ago`, `vol24`) is `.shift(1)`-ed, so no
decision bar reads its own bar. Aggregates stay `NaN` until a full 1,440-bar window exists —
never partially filled — and an incomplete window yields `SKIP` rather than a trade on `NaN`.
Asserted in the selftest (17 checks).

## What this does and does not show

It shows that, on BTC over these 30 days, at 12 bps round trip, ten published rules produced
either no trades or losses whose day-clustered intervals exclude zero.

It does not show the published system is unprofitable. Seven agents are untested, the funding
guards were inert, the instrument may be wrong for four of the rules, the exit policy is mine
rather than theirs, and 30 days is one volatility regime.

The cheapest way to close the gap is a funding-rate and open-interest recorder: it converts
seven UNAVAILABLE agents into testable ones and makes the global guards real.
