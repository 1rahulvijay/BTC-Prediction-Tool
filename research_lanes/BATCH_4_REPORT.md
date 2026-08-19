# Batch 4 — Trade-Economics Lanes

Date: 2026-08-14
Lanes run: `DIRECT_PNL_DISTRIBUTION_V1`, `PM_PROBABILITY_SURFACE_V1`
Runners: `research_lanes/direct_pnl_distribution/run.py`, `research_lanes/pm_probability_surface/run.py`
Raw results: `results.json` beside each runner

## Headline

Neither lane established a positive lower bound on net EV. That makes 23 lanes with no
qualifying edge.

Both lanes initially *appeared* to produce one. Both appearances were artifacts, and the two
artifacts had different causes. Documenting how they were produced and removed is the more
durable output of this batch than the verdicts themselves — a future lane that repeats either
mistake will look like a discovery.

---

## Why the ideas had to be reframed before they could run

The proposals assumed data and instrument structure this venue does not have.

**`DIRECT_PNL_DISTRIBUTION_V1`** was specified as a model that forecasts a PnL distribution per
action. It was run instead as a *measurement* of the distribution that actually occurred. The
ordering matters: a forecaster of net PnL is only worth building if some action's realized net
PnL has a positive lower bound somewhere in the state space. If every action loses at every
level of selectivity, a better estimator of the loss is not progress.

**`PM_PROBABILITY_SURFACE_V1`** was specified over a strike ladder — `BTC > 118k` at 64%,
`BTC > 119k` at 67%, fit `p(K,T)`, trade the outliers. **That ladder does not exist.** Polymarket
BTC up/down rounds are single-strike: the anchor is the price at window open. There is nothing
to fit a surface to.

What does exist is a two-point structure: a 5m round and a 15m round that expire at the same
second with different anchors. That is what was tested.

---

## Data actually available

The round counts are far more encouraging than the independence units.

```
pm_round_settlements     3,336 settled rounds
pm_round_snapshots     178,481 quoted observations
  ... of which joinable to a settlement WITH executable quotes:
                       177,911 observations across only 1,053 rounds
```

Snapshot coverage is **11 UTC dates in two disjoint clusters**:

```
2026-06-15                    4 snapshots      (single stray round)
2026-06-29 .. 2026-07-04  106,850 snapshots
      << five-week hole: 2026-07-05 .. 2026-08-08 >>
2026-08-09 .. 2026-08-13   71,627 snapshots
```

So every bound in this batch rests on **9–10 independent day-blocks**, not on 3,336 rounds.
This is the previously-noted lost Polymarket capture, now measured rather than recalled.

---

## Lane 1 — `DIRECT_PNL_DISTRIBUTION_V1`

Actions priced at the **ask** with the venue's own taker fee `0.07·p·(1−p)`, settled against the
official recorded outcome. `WAIT` is exactly 0.0 and is the benchmark every other action must beat.

### Unconditional (cents per $1 contract)

| action | n | n_days | mean | LCB95 | p_profit | q05 | q50 | q95 | ES 5% |
|---|---|---|---|---|---|---|---|---|---|
| BUY_UP | 177,911 | 10 | −3.6049 | −5.7118 | 0.490 | −68.55 | −0.96 | 61.37 | −79.48 |
| BUY_DOWN | 177,911 | 10 | +0.0556 | −1.9623 | 0.510 | −65.61 | +0.28 | 64.43 | −75.74 |
| WAIT | 177,911 | 10 | 0.0 | 0.0 | — | 0.0 | 0.0 | 0.0 | 0.0 |

Buying UP is decisively negative. Buying DOWN is indistinguishable from zero.

### Selectivity — where the artifact appeared

Selecting the top 1–2% of snapshots by the live `p_hold` signal produced this:

| selection | n | mean | **LCB95** | p_profit | q05 |
|---|---|---|---|---|---|
| BUY_UP top 1% | 1,781 | +0.6706 | **+0.3357** | 0.9994 | +0.09 |
| BUY_DOWN top 2% | 3,772 | +1.3065 | **+0.7524** | 0.9931 | +0.09 |
| BUY_DOWN top 1% | 2,048 | +0.9335 | **+0.5872** | 1.0000 | +0.09 |

Three positive lower bounds — the first this project has produced. All three are false.

### The tail-risk audit that removed them

Three failures compound:

**1. The bet count was inflated ~22×.** The top-1% selection contains 2,048 snapshots drawn from
**94 distinct rounds**. Those are not 2,048 bets; they are 94 bets observed ~22 times each. The
day-block bootstrap corrects for day-level dependence, not for the same open position being
re-counted within a day.

**2. The payoff is violently asymmetric.** Median entry is **0.997**, so the trade risks 99.7c to
make 0.3c — a **332:1** loss-to-gain ratio.

**3. A bootstrap cannot resample an event that never occurred.** With zero losses in the sample,
every resampled day is profitable and the lower bound is positive *by construction*. The interval
describes the sample, not the risk.

Collapsing to one bet per round exposes losses the snapshot view had hidden, and EV is then
evaluated at the 95% upper bound on the loss rate (rule of three where zero losses are observed):

| selection | bets (rounds) | losses | median entry | gain | loss | ratio | p_loss 95%UB | **EV at bound** |
|---|---|---|---|---|---|---|---|---|
| BUY_UP top 2% | 158 | 5 | 0.990 | 1.0c | 99.0c | 99:1 | 0.0589 | **−4.894c** |
| BUY_UP top 1% | 87 | 1 | 0.996 | 0.4c | 99.6c | 249:1 | 0.0339 | **−2.989c** |
| BUY_DOWN top 2% | 167 | 6 | 0.980 | 2.0c | 98.0c | 49:1 | 0.0642 | **−4.416c** |
| BUY_DOWN top 1% | 94 | 0 | 0.990 | 1.0c | 99.0c | 99:1 | 0.0319 | **−2.191c** |

**Selections surviving: 0.**

Note the second row: 5 losses in 158 rounds is a 3.2% loss rate against a break-even requirement
below 1%. It is negative on *observed* data, not merely at the conservative bound.

Capacity confirms it independently: median top-of-book at those quotes is 200 shares at a 0.3c
gross edge — **$0.70 per round at maximum**, roughly 10 rounds/day, taking the entire book and
assuming perfect fills.

---

## Lane 2 — `PM_PROBABILITY_SURFACE_V1`

Structure: long UP on the lower strike, long DOWN on the higher strike, same expiry second.
If both legs settled off the same `S_T`, the payoff is ≥1 in every state and 2 inside the band,
so any all-in cost below 1.0 would be riskless.

### The finding that matters: the legs do not share a settlement reference

```
5m  rounds settle on  chainlink_btc_usd_twap_30s
15m rounds settle on  chainlink_btc_usd_twap_60s
```

- **217 of 246** settled pairs have a *different* expiry price
- the state that is impossible under a shared reference — higher strike settles UP while lower
  strike settles DOWN — **is actually observed** (1 pair, 154 observations)

This is not an arbitrage. It is a spread trade carrying basis risk between a 30-second and a
60-second TWAP. **Any implementation of this lane that assumes a shared underlying will report a
riskless profit that does not exist.**

### Result — 23,872 simultaneous quotes / 244 pairs / 9 UTC days

| entry basis | mean | LCB95 | UCB95 | days | bets |
|---|---|---|---|---|---|
| all observations | +0.7683 | −8.9330 | +9.7773 | 9 | — |
| **first quote per pair (causal)** | **+1.3850** | **−7.2232** | +11.4346 | 9 | 244 |
| first quote under cost 1.0 (causal) | +12.2199 | −3.1747 | +25.4180 | 7 | 20 |

Median all-in cost is **1.4054** against a 1.0 riskless threshold; only **2.94%** of quotes are
below 1.0. Nothing is established.

### The look-ahead bias, quantified

The first implementation selected each pair's **cheapest** quote via `idxmin()` — the natural way
to write it, and not executable, because nothing at decision time knows which quote will turn out
cheapest.

```
cheapest-quote selection   +19.85c per bet     (LCB +12.14c, "POSITIVE")
causal first-quote rule     +1.39c per bet     (LCB  −7.22c, not established)
                          ---------------
look-ahead inflation       +18.47c per bet
```

The bias was **13× the causal effect** and flipped the verdict. It is retained in the runner as
an explicit `hindsight_bias_check` so the comparison stays visible rather than becoming a
correction someone can silently undo.

---

## Transferable methodology from this batch

1. **Collapse to the bet, then bootstrap.** Repeated snapshots of one open position are one bet.
   Report `n_days` and the distinct-bet count next to every interval.
2. **A bootstrap cannot bound a short-volatility payoff whose EV lives in an unobserved tail.**
   When entry is near 1.0, use the rule of three on the loss rate and evaluate EV at that bound.
3. **Every entry rule must be nameable as a causal decision.** `idxmin`, `idxmax`, `groupby.min`
   over an outcome-spanning window are look-ahead until proven otherwise.
4. **Verify that legs of a spread share an observable** before calling anything an arbitrage.
   Read `required_reference_source`; do not infer it from the fact that two markets expire together.
