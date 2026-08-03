# Algodesk 17 agents — multi-symbol, real funding + real OI, ML/DL/RL gates

**Date** `2026-08-03` · **Package** `research/algodesk/` · **Status** DIAGNOSTIC ONLY

Seven Bybit perpetuals, 15-minute bars, 40 days: **30 train / 96-bar purge / 10 test**.
Funding rate and open interest are **fetched, not simulated**. Dataset frozen to parquet with a
sha256 manifest, so every number here is reproducible.

```
symbols   BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT AVAXUSDT LINKUSDT
rows      26,887   (4,000 klines / 4,000 OI / 200 funding prints per symbol)
coverage  open interest 100.0%   funding rate 100.0%
sha256    8ef6525088f6cbbf...
train     06-24 -> 07-24     purge 96 bars     test  07-25 -> 08-03
cost      14 bps round trip (taker 5.5 + slippage 1.5, both legs) + funding paid while held
exit      TP 300 / SL 150 bps / max hold 96 bars      max 3 concurrent positions, ONE portfolio
```

## Headline

**0 of 17 agents has a day-block lower bound above zero on the test window.** More importantly,
only **3 of 17** generated enough training signals to fit a gate at all.

| agent | train | test | rule bps | ML | DL | RL | rule 95% CI (test) |
|---|---:|---:|---:|---:|---:|---:|---|
| TREND | 5 | 0 | – | – | – | – | never fired in test |
| MOMO | 0 | 0 | – | – | – | – | never fired |
| BREAK | 23 | 8 | −11.8 | *underpowered* | | | [−164.5, +114.7] |
| MEAN | 0 | 0 | – | – | – | – | never fired |
| **FUND** | **0** | **0** | – | – | – | – | **never fired — see below** |
| VOL | 67 | 8 | −94.8 | −112.4 | −84.9 | −60.3 | [−146.3, −62.6] |
| OI | 6 | 2 | −60.5 | *underpowered* | | | [−163.9, +42.9] |
| **CONTRA** | **0** | **0** | – | – | – | – | **never fired — see below** |
| SCALP | 36 | 14 | +30.3 | *underpowered* | | | [−93.5, +158.7] |
| LIQ | 3 | 0 | – | – | – | – | never fired in test |
| PAT | 22 | 11 | −81.4 | *underpowered* | | | [−157.0, +22.3] |
| RANGE | 111 | 32 | −7.3 | −38.7 | −2.8 | **+21.1** | [−76.2, +68.9] |
| STAT | 13 | 1 | −70.9 | *underpowered* | | | *(one day)* |
| **SENT** | **0** | **0** | – | – | – | – | **never fired — see below** |
| FLOW | 44 | 13 | −107.6 | −164.2 | −135.6 | −100.2 | [−164.0, −5.8] |
| REGIME | 27 | 4 | −164.4 | *underpowered* | | | [−165.5, −164.0] |
| OIDIV | 23 | 2 | −82.6 | *underpowered* | | | [−164.0, −1.3] |

## The one apparently positive result is not a result

`RANGE` gated by RL returns **+21.1 bps/trade** on test — the only positive cell among the nine
model arms. Its day-block interval:

```
RANGE / RL    n = 16    mean +21.1    95% CI [-84.1, +114.4]
```

The interval is 200 bps wide and straddles zero. It is one positive cell out of nine fitted arms
across three agents, on sixteen trades. Under any multiplicity correction it is noise, and it is
recorded here so it cannot be re-discovered later and quoted as a finding.

Every other gated arm:

| agent | rule | ML | DL | RL |
|---|---|---|---|---|
| VOL | −94.8 [−146.3, −62.6] | −112.4 (n=2) | −84.9 [−142.7, −52.7] | −60.3 (n=1, no CI) |
| FLOW | −107.6 [−164.0, −5.8] | −164.2 [−164.7, −164.0] | −135.6 [−164.3, −80.3] | −100.2 [−164.8, +105.9] |

Note the gates mostly **shrink** the sample (ML on VOL keeps 2 of 8 trades). A gate that admits
one or two test trades cannot be evaluated, whatever its mean says.

## The published funding thresholds are 6–20× larger than real funding

This is the finding that only real data could produce. Across 7 symbols and 40 days, every
funding print Bybit published:

```
observed funding rate     min -0.000256    max +0.000100    |fr| 99th pct  0.000140

|fr| > 0.0015  (FUND, CONTRA, SENT need this)   0.000% of bars
|fr| > 0.0030  (FUND's stated band)             0.000% of bars
|fr| > 0.0050  (the global LONG/SHORT guards)   0.000% of bars
```

The largest funding rate observed anywhere was **0.000256**, six times below the smallest
threshold any funding agent requires, and twenty times below the global guards.

Consequences:

- **FUND, CONTRA and SENT cannot fire** on these instruments at these thresholds. Not "did not"
  — *cannot*, by a wide margin.
- **The published global funding guards are structurally inert.** They never block anything.
- Real perpetual funding is ~0.01% per 8h. The spec is written as though 0.15%–0.5% were an
  operating band; those levels occur only in violent squeezes.

## Why this differs from the simulated-funding implementation

An earlier third-party script derived funding from price (`funding_rate = change_8h * 0.05`) and
open interest from volume (`open_interest = vol_24h * 3.5`). Under that mapping an 8-hour move
of 3% *becomes* a funding rate of 0.0015 — so the funding agents fire constantly, and `FUND`,
`CONTRA` and `SENT` report results.

Those results describe 8-hour momentum. The thresholds are only reachable because the proxy made
them reachable. With real funding they are never reached at all. That is the difference between a
proxy and a measurement, in one number.

## Power is the binding constraint, not the strategies

**14 of 17 agents could not be gated** — fewer than the declared 40 training trades. Test
samples run 1–32 trades, and the day-block CIs are 150–300 bps wide. At these widths the
negative means are indicative, not established.

This is the honest headline: 30 days at 15-minute bars across 7 symbols does not produce enough
signals to train per-agent ML, DL or RL. The models are not "worse than the rules" — for 14 of
17 they were never fitted, and for the other 3 they were fitted on 44–111 examples, which is
small for gradient boosting and very small for a neural net or a bandit.

Fixing this needs more data, not better models: more symbols, a longer window, or agents whose
conditions fire more often. Anything else is fitting nine arms to a few dozen trades.

## What was frozen before the first run

- **Rules are never re-fitted.** The published thresholds generate signals; only the *gate* is
  learned. Re-tuning the rules on the data that judges them is the search this repository has
  spent weeks learning not to run.
- **Conservative end of every published band**, exactly as in the BTC-only run.
- `MIN_TRAIN_TRADES = 40` — below it, no model is fitted and the cell reads `undrpwr`. Declared
  before any result, so an underpowered agent cannot be rescued by lowering the bar afterwards.
- `TP 300 / SL 150 bps / max hold 96 bars`. A max hold is mandatory: without it "dynamic exit"
  becomes indefinite holding, which flatters any backtest.
- **96-bar purge** between train and test, at least one maximum hold, so no training trade can
  resolve inside the test window.
- The test window is scored **once**.

## Accounting, and what it fixes

- **One portfolio, max 3 concurrent positions across all symbols.** Holding one position per
  symbol with a full allocation each — as the earlier implementation did across 7 pairs — silently
  deploys up to 7× the intended capital and sums the PnL as if each had the whole account.
- **Entry is the next bar's open**, never the close that produced the signal.
- **Stop is checked before target**: a bar whose range spans both books the loss.
- **Funding is charged** for every 8-hour print crossed while the position is held, signed by
  side. No cost model that ignores funding can evaluate a perpetual strategy.
- Fees and slippage are charged on **both legs**.

## Causality

Every 24h aggregate is `.shift(1)`-ed. Funding is joined as-of with a 9-hour age limit and
**blanked when stale**, never carried; a later print never back-fills an earlier bar. OI is
as-of and never interpolated. An agent whose real input is missing returns `SKIP` — there is no
proxy path in the code, and the selftest asserts this for all seven dependent agents.

Selftests: data 8 checks, agents 18, backtest 12.

## Verdict

On seven Bybit perpetuals over 40 days, with real funding and real open interest, correct
portfolio accounting and full costs: no agent, and no ML, DL or RL gate over an agent, shows a
positive day-clustered lower bound out of sample.

Three of the seventeen have thresholds that real funding never reaches, so they are not
falsified — they are inapplicable to these instruments. Fourteen of the seventeen could not be
gated for lack of signals. The single positive cell has an interval twice as wide as its mean.

The limiting factor is evidence, not model class.
