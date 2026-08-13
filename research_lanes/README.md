# research_lanes — standalone alpha laboratory

Isolated economic experiments. Each lane answers one question about whether money is available,
and must earn its way into the app rather than being wired in because a metric looked good.

**Nothing in the serving path may import from here, and no lane may import serving code.**
Verified in both directions.

## Status — 5 lanes run 2026-08-13

| lane | question | verdict | report |
|---|---|---|---|
| `binance_cost_clearance` | does BTC move enough to pay for a trade? | **CLOSE** <30m at taker cost | [REPORT](binance_cost_clearance/REPORT.md) |
| `volatility_expansion` | which windows move at all? | **PARTIAL** — real, not sufficient | [REPORT](volatility_expansion/REPORT.md) |
| `spot_perp_basis` | does extreme basis revert past costs? | **CLOSE** — 15x too small | [REPORT](spot_perp_basis/REPORT.md) |
| `time_phase_alpha` | does clock phase carry structure? | **NO EFFECT** | [REPORT](time_phase_alpha/REPORT.md) |
| `polymarket_residual` | when is the market's price wrong? | **BLOCKED** — zero data overlap | [REPORT](polymarket_residual/REPORT.md) |

## What the five lanes say together

1. **Sub-30-minute Binance direction is arithmetically closed at taker cost.** Mean 5m move is
   8.3 bps against a 12 bps round trip, so a 100%-accurate model still loses 3.7 bps per trade.
   No classifier improvement reaches this — the constraint is move size, not sign.

2. **Volatility filtering reopens it, partly.** Restricting to the top 1% of
   predicted-volatility windows lifts mean |move| to 21.9 bps (LCB) and drops the break-even
   accuracy to 77.4%. Possible, versus impossible. Still far above the ~50% directional base
   rate measured in this data. And ~all of the ranking is available from `rv_15m` alone —
   the model adds 0.017 AUC.

3. **Two structural hypotheses died cleanly.** Basis reversion is real and 15x too small
   (0.89 bps vs 12). Clock phase does not separate at all (LCB 8.87 vs UCB 9.10).

4. **The one venue not bounded by any of this is dark.** Polymarket has a binary payoff, so
   the cost-clearance result says nothing about it — and its quote snapshots and its official
   settlements were recorded in **non-overlapping** date ranges. Zero joinable rows.

The single highest-value action from this batch is not a model. It is backfilling Gamma
settlement for the 10 days where PM snapshots already exist, and keeping both recorders running
together from now on.

## The bar

A lane is promotable only when the **lower confidence bound of net EV is positive** — not when
accuracy looks impressive. Accuracy is not a unit of money.

## Required scorecard

Every lane reports the statistic **and an interval built from independent units** — UTC days
for Binance, rounds for Polymarket, never rows. `common/scorecard.py` and `common/pm_data.py`
provide the bootstraps; use them rather than re-deriving, because the independence unit is the
thing most easily got wrong. A row bootstrap will happily report a tight interval around a
number that means nothing.

Also required: net EV after costs, behaviour at 1.5x and 2x cost, and a simple baseline the
lane must beat. Three of the five lanes above were decided by the baseline or the cost screen
rather than by the model.

## Method note

Prefer tests that fit nothing. `binance_cost_clearance` trains no model and could not be
overfitted, yet it closed more downstream work than any fitted result would have. Two of the
other four were also settled without a model — by an interval and a cost comparison.

## Not yet run

From the proposed set: market-disagreement resolution, probability elasticity, stale-quote
detection, maker markout surface, order-flow surprise, book elasticity, liquidity
replenishment, liquidation exhaustion, cross-venue leadership, funding carry and dispersion,
options IV vs realized, counterfactual order policy, capacity curves, edge half-life.

Most PM-side lanes are blocked behind the same settlement join. Several microstructure lanes
need sequenced L2 and tick data rather than the 1-minute matrix.
