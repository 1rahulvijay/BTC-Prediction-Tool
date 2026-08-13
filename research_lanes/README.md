# research_lanes — standalone alpha laboratory

Isolated economic experiments. Each lane answers one question about whether money is available,
and must earn its way into the app rather than being wired in because a metric looked good.

**Nothing in the serving path may import from here, and no lane may import serving code.**
Verified in both directions.

## Status — 7 lanes run 2026-08-13

| lane | question | verdict | report |
|---|---|---|---|
| `binance_cost_clearance` | does BTC move enough to pay for a trade? | **CLOSE** <30m at taker cost | [REPORT](binance_cost_clearance/REPORT.md) |
| `volatility_expansion` | which windows move at all? | **PARTIAL** — real, not sufficient | [REPORT](volatility_expansion/REPORT.md) |
| `spot_perp_basis` | does extreme basis revert past costs? | **CLOSE** — 15x too small | [REPORT](spot_perp_basis/REPORT.md) |
| `time_phase_alpha` | does clock phase carry structure? | **NO EFFECT** | [REPORT](time_phase_alpha/REPORT.md) |
| `polymarket_residual` | when is the market's price wrong? | **CLOSE** as taker — model is behind the market | [REPORT](polymarket_residual/REPORT.md) |
| `poly_fullset_arb` | is YES+NO ever under $1 all-in? | **REAL, NEGLIGIBLE** — $43.82 / 10 days | [REPORT](poly_fullset_arb/REPORT.md) |
| `hedged_poly_mm` | does maker quoting pay? | **INCONCLUSIVE** — upper bound only, no fill data | [REPORT](poly_fullset_arb/REPORT.md) |

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

4. **Polymarket is not bounded by the cost-clearance result — and it still fails as a taker
   strategy.** With settlement backfilled (149,061 rows, 921 rounds), the app's `p_hold_up` is
   genuinely informative (Brier 0.183 vs 0.250 for a constant) and **decisively worse than the
   market's own price** (0.170), CI [-0.0176, -0.0091]. Trading its disagreement loses 1.8-2.7c
   per share at every threshold — and loses MORE as the required edge grows, which is evidence
   that a large model-vs-market gap is usually the model being wrong.

What is NOT closed on that venue: maker economics (zero platform fee plus a rebate pool, and
the taker fee is what kills the lane above), full-set `Ask_YES + Ask_NO < 1` arbitrage, and the
residual formulation `logit(p_true) = logit(p_market) + f(X)` — which has never been fitted, and
is the structurally right move now that the market is measured as the stronger forecaster.

5. **Full-set arbitrage is real and tiny.** `ask_UP + ask_DOWN` sits at a median 1.0100.
   Gross parity violations occur in 0.390% of snapshots; after the 2.79c two-leg taker fee only
   **0.076%** survive — 114 opportunities totalling **$43.82** across ten days at top-of-book.
   Mechanically sound, worth a background scanner, too small to fund anything.

6. **The maker lane is inconclusive, and its best-looking number is not a strategy result.**
   The bid side sits ~1.3c below parity with a tight interval — but that PnL does not depend on
   the outcome at all (both legs held is a complete set worth $1), so the bootstrap measures
   quote stability, not proven edge. Capturing it needs BOTH legs to fill, which is exactly what
   quote-only data cannot show. One-sided fills carry full directional risk and both intervals
   span zero.

   Everything in that lane is an **upper bound**: guaranteed fill, no adverse selection, no
   queue. Decisive only if it had lost. It did not, so the next step is measuring toxicity —
   shadow-post quotes, record real fills, and mark out the fill price at +1s/+5s/+30s.

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
