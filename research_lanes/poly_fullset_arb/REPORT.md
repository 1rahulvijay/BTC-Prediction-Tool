# POLY_FULLSET_ARB_V1 and HEDGED_POLY_MM_V1

**Full-set arb: REAL BUT NEGLIGIBLE.** It exists in 0.076% of snapshots and totals **$43.82**
across ten days at top-of-book size. The taker fee — 2.79c for both legs — is what eats it.

**Hedged maker: INCONCLUSIVE, and the number that looks best is not a strategy result.** The
bid side sits 1.3c below parity, but capturing it requires *both* legs to fill, which is
precisely the thing this data cannot measure.

Run 2026-08-13 · `research_lanes/poly_fullset_arb/run.py` · 149,064 snapshots, 921 rounds, 10 days

---

## POLY_FULLSET_ARB_V1

UP and DOWN are the two outcomes of one binary market — exactly one pays $1. Buying both for
under $1 all-in is a mechanical inconsistency requiring no forecast.

```
cost = ask_UP + ask_DOWN + fee(ask_UP) + fee(ask_DOWN)
edge = 1 - cost
```

### Result

| | |
|---|---:|
| snapshots with both asks | 149,064 |
| **median `ask_UP + ask_DOWN`** | **1.0100** |
| minimum observed sum | 0.6900 |
| % where **gross** sum < 1.00 | **0.390%** |
| median fee, both legs | **0.0279** |
| % where **net** cost < 1.00 | **0.076%** |
| executable opportunities | **114** |
| mean edge per share | +0.0182 |
| median executable size | 12.5 |
| **total $ across every hit, 10 days** | **+$43.82** |

### Reading it

The market normally prices the pair at **1.0100** — one cent above parity, which is just the
spread. Parity violations at the gross level appear in 0.39% of snapshots.

**The fee is the whole story.** At 2.79c for both legs, a dislocation must exceed 2.8c before
it is worth anything. That takes 0.390% down to 0.076% — a factor of five.

$43.82 over ten days is roughly **$4.40/day** of theoretical maximum, and that is generous:
it assumes both legs fill instantly at top-of-book size, ignores gas and settlement costs, and
counts every hit including any stale-quote artifacts. The minimum observed sum of 0.6900 is
implausible as a live executable price and is more likely a crossed or stale book — which means
the true total is lower than $43.82, not higher.

**Verdict: real, mechanically sound, and too small to fund anything.** Worth running as a
zero-cost background scanner precisely because it needs no model; not worth engineering effort
beyond that.

---

## HEDGED_POLY_MM_V1 — upper bound only

`POLYMARKET_RESIDUAL_V1` was killed by the taker fee. Makers pay zero platform fee, so the
maker side needs its own test.

### What this measures, and what it cannot

There is no fill data here, only quotes. So this computes an **upper bound**: assume the quote
fills whenever posted, at the quoted price, with **no adverse selection** and no queue risk.
Real market making is strictly worse — you are filled preferentially when the market is about
to move against you.

An upper bound is decisive in one direction only. If it loses, the lane is closed and no fill
model rescues it. If it wins, the result is **inconclusive**, and the next step is measuring
toxicity rather than trading.

### Result

Spread is **exactly 1.0c at p25, median and p75** — the book is tick-wide essentially always.

| quoting policy | EV/share | 95% CI | verdict |
|---|---:|---|---|
| bid UP only | −0.0113 | [−0.0412, +0.0168] | negative, not established |
| bid DOWN only | +0.0240 | [−0.0037, +0.0550] | marginal, not established |
| **two-sided, both filled** | **+0.0127** | **[+0.0107, +0.0160]** | positive |

### The two-sided number is not what it looks like

Its confidence interval is far tighter than the others, and that is the tell. `two_sided` PnL
is `1 − (bid_UP + bid_DOWN)` — it **does not depend on the outcome at all**. Holding both legs
is a complete set worth exactly $1 at settlement regardless of which way BTC goes. So the
bootstrap is resampling the quote distribution, not outcome uncertainty, and the tight interval
reflects that the bid-side parity gap is stable, not that a strategy is proven.

What it actually says: **the bid side sits about 1.3c below parity.** That is a real,
consistently-present structural gap.

**Capturing it requires both legs to fill, and that is the entire problem.** A resting bid on
the side the market is moving toward does not fill; the side moving against you does. One-sided
fills carry full directional risk — which is exactly what the two one-sided rows show, both with
intervals spanning zero.

### One more caution on `bid DOWN only`

Its +2.4c is largely the realized base rate. UP settled 48.7% of the time in this sample, so
DOWN won 51.3%, and buying DOWN cheaply inherits that. Over 921 rounds a 51.3/48.7 split is
well within noise of a coin flip, and the CI crossing zero says so. **Do not read this as a
DOWN-side edge.**

---

## What would make the maker lane conclusive

1. **Fill data.** Post quotes in shadow and record what actually fills, when, and at what queue
   position. Everything above collapses to this one measurement.
2. **Markout after fill.** Mid at +1s, +5s, +30s versus fill price. That is adverse selection,
   measured directly.
3. **Rebate as a variable.** The documented 20% crypto maker-rebate share is performance-based
   and can change. It is modelled at **zero** in every number above, deliberately, so no result
   depends on it.
4. **Both-leg fill rate.** The single number that decides this lane. If two-sided fills are
   rare, the 1.3c gap is unreachable.

## Attacks applied

| attack | status |
|---|---|
| round-clustered bootstrap | yes — 921 rounds, 800 draws |
| real fee schedule | yes — `0.07·p·(1−p)` per leg for takers, zero for makers |
| executable prices | yes — ask/bid, not mid |
| executable size | yes for arb — top-of-book, `min(up_size, down_size)` |
| rebate excluded from headline | yes — modelled at zero |
| adverse selection | **no — this is why the maker lane is an upper bound** |
| queue position | **no** |
| second era | **no** — 10 days only |

## Caveat on all of it

**Ten calendar days.** 921 rounds is a fair count, but from ten days (06-16, 06-29→07-04,
08-09→08-13). The full-set arb count in particular is a rate estimate from a short window, and
parity-violation frequency is exactly the kind of thing that varies with market conditions.
