# research_lanes — standalone alpha laboratory

Isolated economic experiments. Each lane answers one question about whether money is
available, and must earn its way into the app rather than being wired in because a metric
looked good.

**Nothing in the serving path may import from here, and no lane may import serving code.**
That isolation is the point: a lane that imports `decision_champion` can accidentally inherit
its assumptions, and a lane the app imports can accidentally acquire authority.

## Status

| lane | question | verdict | report |
|---|---|---|---|
| `binance_cost_clearance` | does BTC move enough to pay for a trade? | **CLOSE LANE** <30m at taker cost | [REPORT.md](binance_cost_clearance/REPORT.md) |
| `polymarket_residual` | when is the market's own price wrong? | not started | — |
| `volatility_expansion` | which windows will move at all? | not started | — |
| `funding_basis_carry` | can we earn carry without direction? | not started | — |

## The bar

A lane is promotable only when the **lower confidence bound of net EV is positive** — not when
accuracy looks impressive. Accuracy is not a unit of money.

## Required scorecard

Every lane reports, at minimum:

- the statistic **and an interval built from independent units** (UTC days, or rounds for
  Polymarket — never rows)
- net EV after spread, fee, slippage and exit cost, not gross
- behaviour at 1.5x and 2x cost
- a simple baseline it must beat (market price, momentum, carry, constant)

`common/scorecard.py` provides `day_block_bootstrap`, `breakeven_accuracy`, `ev_bps` and the
forward-move helpers. Use them rather than re-deriving — the independence unit is the thing
most easily got wrong, and a row bootstrap will happily report a tight interval around a
number that means nothing.

## Why the first lane ran first

`binance_cost_clearance` fits no model. It measures the move distribution and derives the
accuracy required for EV>0. It was cheapest to run and could falsify the largest amount of
downstream work — which it did: at the shipped 12 bps round trip, a **100%-accurate 5-minute
model still loses 3.7 bps per trade**. That result was available without training anything,
and it reorders every lane behind it.

Prefer tests with that shape: cheap, unfittable, and capable of closing a direction.
