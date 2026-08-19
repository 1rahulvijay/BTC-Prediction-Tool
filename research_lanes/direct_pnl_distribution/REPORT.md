# DIRECT_PNL_DISTRIBUTION_V1

Date: 2026-08-14 · Runner: `run.py` · Raw: `results.json`
Full batch context: `../BATCH_4_REPORT.md`

## Question

Not "which way does BTC go" but: **what is the realized distribution of net PnL for every
executable action, at every decision moment?** Actions are `BUY_UP`, `BUY_DOWN`, `WAIT`, priced
at the **ask** with the venue's own taker fee `0.07·p·(1−p)`, settled against official outcomes.
`WAIT` is exactly 0.0 and is the benchmark the others must beat.

This is a *measurement*, not a forecaster. A model that predicts net PnL is only worth building
if some action's realized net PnL has a positive lower bound somewhere in the state space.

## Verdict

**No action survives.** Zero of the tested selections has a defensible positive lower bound.

## Data

177,911 executable snapshots / 1,053 rounds / **10 UTC days**. Snapshot coverage is 11 dates in
two disjoint clusters with a five-week hole (2026-07-05 → 2026-08-08), so the bound rests on
10 independent days, not on 1,053 rounds.

## Unconditional (cents per $1 contract)

| action | n | mean | LCB95 | p_profit | q05 | q50 | q95 | ES 5% |
|---|---|---|---|---|---|---|---|---|
| BUY_UP | 177,911 | −3.6049 | −5.7118 | 0.490 | −68.55 | −0.96 | 61.37 | −79.48 |
| BUY_DOWN | 177,911 | +0.0556 | −1.9623 | 0.510 | −65.61 | +0.28 | 64.43 | −75.74 |
| WAIT | 177,911 | 0.0 | 0.0 | — | 0.0 | 0.0 | 0.0 | 0.0 |

## The artifact this lane exists to document

Selecting the top 1–2% by the live `p_hold` signal produced **positive lower bounds** of +0.34c
to +0.75c with `p_profit` of 0.999–1.000 — the first this project had produced. All false.

**Bet-count inflation.** The 2,048 "profitable snapshots" were **94 rounds observed ~22 times
each**. A day-block bootstrap corrects day-level dependence, not one open position re-counted
within a day — and it cannot resample a loss that never occurred, so with zero losses in sample
every resampled day is profitable and the bound is positive *by construction*.

Collapsing to one bet per round exposed the hidden losses, and EV at the 95% upper bound on the
loss rate (rule of three where none observed):

| selection | bets | losses | entry | gain | loss | ratio | **EV at bound** |
|---|---|---|---|---|---|---|---|
| BUY_UP top 2% | 158 | 5 | 0.990 | 1.0c | 99.0c | 99:1 | **−4.894c** |
| BUY_UP top 1% | 87 | 1 | 0.996 | 0.4c | 99.6c | 249:1 | **−2.989c** |
| BUY_DOWN top 2% | 167 | 6 | 0.980 | 2.0c | 98.0c | 49:1 | **−4.416c** |
| BUY_DOWN top 1% | 94 | 0 | 0.990 | 1.0c | 99.0c | 99:1 | **−2.191c** |

Row 1 is negative on *observed* data alone: 5 losses in 158 rounds is 3.2% against a break-even
under 1%.

Capacity confirms it independently: 200 shares at 0.3c gross is **$0.70 per round**, ~10
rounds/day, taking the entire book with perfect fills.

## Reusable rule

Collapse to the bet, then bootstrap. When entry is near 1.0, a bootstrap cannot bound a
short-volatility payoff whose EV lives in an unobserved tail — use the rule of three on the loss
rate and evaluate EV at that bound.
