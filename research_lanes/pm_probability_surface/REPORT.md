# PM_PROBABILITY_SURFACE_V1

Date: 2026-08-14 · Runner: `run.py` · Raw: `results.json`
Full batch context: `../BATCH_4_REPORT.md`

## The proposed form is not runnable

The lane was specified over a strike ladder — `BTC > 118k` at 64%, `BTC > 119k` at 67%, fit
`p(K,T)`, trade the outliers. **That ladder does not exist.** Polymarket BTC up/down rounds are
single-strike: the anchor is the price at window open. There is nothing to fit a surface to.

## What was tested instead

A two-point structure that does exist: a 5m round and a 15m round expiring at the **same second**
with different anchors.

```
buy UP   on the LOWER  strike   pays 1 if S_T >  K_lo
buy DOWN on the HIGHER strike   pays 1 if S_T <= K_hi
```

If both legs settled off the same `S_T`, the payoff is ≥1 in every state and 2 inside the band,
so any all-in cost below 1.0 would be riskless.

## The finding that matters: the legs do not share an observable

```
5m  rounds settle on  chainlink_btc_usd_twap_30s
15m rounds settle on  chainlink_btc_usd_twap_60s
```

- **217 of 246** settled pairs have a *different* expiry price
- the state that is impossible under a shared reference — higher strike settles UP while the
  lower strike settles DOWN — **is actually observed**

**This is not an arbitrage.** It is a spread trade carrying basis risk between a 30-second and a
60-second TWAP. Any implementation that assumes a shared underlying will report a riskless profit
that does not exist.

## Verdict

**Not established.** 23,872 simultaneous quotes / 244 pairs / 9 UTC days.

| entry basis | mean | LCB95 | UCB95 | bets |
|---|---|---|---|---|
| all observations | +0.7683 | −8.9330 | +9.7773 | — |
| **first quote per pair (causal)** | **+1.3850** | **−7.2232** | +11.4346 | 244 |
| first quote under cost 1.0 (causal) | +12.2199 | −3.1747 | +25.4180 | 20 |

Median all-in cost is **1.4054** against the 1.0 riskless threshold; only **2.94%** of quotes
fall below 1.0.

## The second artifact: look-ahead entry selection

The first implementation selected each pair's **cheapest** quote via `idxmin()` — the natural way
to write it, and not executable, because nothing at decision time knows which quote will turn out
cheapest.

```
cheapest-quote selection   +19.85c per bet    (LCB +12.14c, "POSITIVE")
causal first-quote rule     +1.39c per bet    (LCB  −7.22c, not established)
                          ---------------
look-ahead inflation       +18.47c per bet     -- 13x the causal effect
```

It flipped the verdict. The biased variant is retained in the runner as an explicit
`hindsight_bias_check` so the comparison stays visible rather than becoming a correction someone
can silently undo.

## Reusable rules

1. Every entry rule must be nameable as a causal decision. `idxmin`, `idxmax` and `groupby.min`
   over an outcome-spanning window are look-ahead until proven otherwise.
2. Verify that the legs of a spread share an observable before calling anything an arbitrage.
   Read `required_reference_source`; do not infer it from two markets expiring together.
