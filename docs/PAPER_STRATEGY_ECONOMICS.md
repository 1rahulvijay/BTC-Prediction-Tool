# Paper Strategy Economics - cost guards and model-driven paper strategies

`2026-07-31`. Reproduce every number with the commands at the end.

---

## 1. The defect: both paper strategies were arithmetically incapable of profit

`trend_following` and `breakout` each sized their stop from `volatility_bps * 2.0`, where
`volatility_bps` is the standard deviation of **one 1-second sample**.

Measured from the 518,398-bar archive:

```
1-minute log-return sd : 6.24 bps
-> 1-SECOND sd         : 0.81 bps        (the strategies sample at 1s)

stop_bps  = max(4.0, min(30.0, 0.81 * 2)) = max(4.0, 1.61) = 4.0   <- the FLOOR always bound
target    = stop * 1.5                                     = 6.0 bps
round trip = 2 x (fee 5.0 + slippage 1.0)                  = 12.0 bps

NET ON A PERFECT WINNER = 6.0 - 12.0 = -6.0 bps
```

**Every trade that reached its take-profit still lost 6 bps.** Not unlikely to be profitable —
incapable of it.

### Why nothing caught it

The accounting was correct. Fills, fees, slippage and equity were all computed properly, and the
engine's own tests verified that arithmetic in detail. What no test asked was whether a *winning*
trade wins. The defect lived entirely in the strategy's intent, where every existing check was
pointed somewhere else.

### Root cause

A stop scaled to the wrong time horizon. `volatility_bps * 2.0` sizes a stop for a **2-second**
event; the position is held for up to **300 seconds**. Because BTC's 1-second sigma is under half
the 4.0 bps floor, the volatility term never mattered at all — the stop was a constant 4.0 bps
wearing the costume of a volatility model.

---

## 2. The fix

Scale by `sqrt(holding_seconds)` — the horizon the position is actually exposed to — and floor
the target above the round trip:

```python
horizon_scale = math.sqrt(max(1.0, float(self.maximum_holding_seconds)))
stop_bps  = max(minimum_stop_bps, min(maximum_stop_bps,
                                      volatility_bps * horizon_scale * stop_sigma))
target_bps = max(stop_bps * reward_risk_ratio, self.minimum_take_profit_bps)
```

with, in `StrategyBase`:

| constant | value | meaning |
|---|---:|---|
| `assumed_round_trip_bps` | 12.0 | must be >= the engine's `2 x (fee + slippage)` |
| `minimum_target_cost_multiple` | 1.5 | target must clear cost by 50%, not merely tie |
| `minimum_take_profit_bps` | 18.0 | the derived floor |

At the measured 0.81 bps 1-second sigma, `0.81 * sqrt(300) * 1.0 = 14.0 bps` stop, `21.0 bps`
target — comfortably clear of the 12.0 bps round trip, and sized to the horizon actually held.

### The structural guard

`StrategyBase._decision` now **raises** when an opening decision carries a take-profit closer to
mark than `assumed_round_trip_bps`. It raises rather than degrading to `NO_EDGE` because this is
a programming error in the strategy, not a market condition — it must fail at the first decision
in test, not bleed quietly in paper. This matches the existing behaviour for non-finite values.

**Negative-tested.** Reintroducing the original expression verbatim produces:

```
ValueError: trend_following: take-profit is 6.00 bps from mark but the round trip
costs 12.00 bps - every winner would still lose 6.00 bps
```

---

## 3. Two new strategies

The registry held two strategies and both were **continuation** bets. A third continuation
variant would have tested the same hypothesis a third time — and the research suite already
answered it: `BREAKOUT_BRACKET_V1` lost in all nine configurations and its control lost equally,
so the loss was structural rather than a tuning failure.

### `random_control` — the missing denominator

A deliberately **zero-information** strategy: it opens positions on a deterministic pseudo-random
schedule with a randomly chosen side, using the same notional, geometry and holding period as the
strategies it benchmarks. It reads no price feature.

This is the most valuable addition here. Every apparent edge in this repository's research died on
contact with a matched control:

- the `+10/-20` first-passage structure showed `+5.8` for `flow_imbalance`, for `flow_reversal`
  **and** for a zero-information baseline — it was BTC's path distribution, not a signal;
- the breakout bracket's loss was only interpretable *because* the control lost too.

The paper lane had no control at all, so `trend_following`'s P&L was reported against nothing: a
positive number could not be separated from BTC drift, a negative one from the cost of trading.
**A strategy that does not beat `random_control` has established nothing.**

Determinism matters and is enforced: entries derive from `sha256(strategy_id | version | seed |
second)`, never a global RNG, so the benchmark is reproducible across restarts and replays. Its
`score` and `confidence` are hard zero — a control that reported confidence would be masquerading
as a forecast.

### `mean_reversion` — the untested species

Fades a displacement that is large relative to recent volatility (`|z| >= 2.0` against a causal
60-sample window that **excludes the current sample**), with one veto:

> a stretched price on a surging tape is information being priced in, and fading that is the
> losing half of this trade.

The veto threshold is set from measurement, not invention. Rolling 60-second aggTrade counts over
33,753 recorded seconds: p50 807, p75 1196, **p90 1699**, p95 2114, p99 2974. The ceiling is
1700 — the busiest tenth of the tape is treated as information and skipped.

**A drafting error worth recording:** the first version compared intensity against
`snapshot.agg_trade_count_baseline`, a field that does not exist. The `getattr(..., 0.0)` fallback
would have made the strategy permanently `NO_EDGE` — alive-looking in the registry, never trading,
and silent about it. Caught by checking the schema instead of trusting the attribute name.

---

## 4. Also fixed

`api_selftest.py` asserted `len(strategy_body["items"]) == 2` and broke when the registry grew to
four (and later five). Now compared against `StrategyRegistry()` itself, including the id set - a hardcoded count
tests the constant rather than the API and must be edited on every registry change.

## 5. Noted, not fixed

`backend/binance_paper/types.py` **shadows the standard library `types` module**. Harmless under
package imports, but running any file in that directory as a script puts the directory on
`sys.path[0]` and breaks Python's import machinery before the first line executes:

```
ImportError: cannot import name 'MappingProxyType' from 'types'
```

Every test in the package is therefore module-invoked (`python -m backend.binance_paper.…`), which
is why the new test is too. Renaming a module the package imports from is a wider change than this
work should carry, so it is recorded here rather than done quietly.

---

## 6. What this does NOT claim

Fixing the arithmetic makes these strategies *capable* of profit. It does not make them
profitable, and nothing here is evidence that they are.

The research suite's verdict stands: **0 of 39 scripts produced a positive out-of-sample result**,
and direction was dead at settlement and along the path. `mean_reversion` is a hypothesis; it is
expected to be read against `random_control`, and if it does not beat that control it has
established nothing.

Nothing is wired to real money. Real orders remain **DISABLED**; the lane is **PAPER / SHADOW
ONLY**.

## 7. Reproduce

```bash
python -m backend.binance_paper.test_strategy_economics
python -m backend.binance_paper.test_engine
python -m backend.binance_paper.api_selftest
python -m backend.binance_paper.selftest
python backend/run_ci_locally.py
```

## 8. Model-driven venue strategies

The Binance paper registry now has a fifth strategy, `model_consensus`. It consumes the final
post-filter 5m ensemble decision only when live calibration, bundle identity, agreement, meta trust
and a conservative post-cost EV gate all pass. Its target is floored at 18 bps against the 12 bps
assumed round trip. Dynamic exits are causal and still use the shared latency, depth, fee, slippage,
risk and accounting lifecycle.

Polymarket adds `CHAMPION_DYNAMIC_PAPER_V1` as the seventeenth tracker strategy. It can enter only
after the existing Champion authorizes `PAPER_BET`; it then measures whether executable bid exits
on net target, net stop or model invalidation outperform settlement. Both taker fees are charged.
The Champion calibration lockdown remains default-off, so this is a forward experiment rather than
a way to create more trades.

Full contracts, tests and non-claims are recorded in
`docs/active/MODEL_DRIVEN_PAPER_STRATEGIES_2026-07-31.md`.
