# Conditional entry — can any state break the martingale? — `2026-08-08`

`binance_first_touch_lane.py` showed the **unconditional** first-touch process is a martingale:
EV at exactly −cost in all eighteen cells. That says the barrier geometry carries no
information on its own. It does not say no conditioning state exists — and that was the last
untested question in the sweep.

**Result: no. The best cell out of 285 loses 11.74 bps, and a shuffle of pure noise produced a
better one.**

---

## Two ways to get a fake answer, both guarded

**Leakage.** The research matrix carries `future_close_5m`, `future_high_5m`, `future_low_5m`,
`future_abs_move_5m`, `future_direction_5m`, `ret_5m`, `tradable_move_label` and
`fail_fast_label` — **outcomes sitting in the same table as the features**. Conditioning on any
of them produces spectacular alpha and means nothing.

The feature list is an explicit **allow-list** of 19 causal columns, not a deny-list of
known-bad ones. A deny-list silently admits every future column added later. The allow-list is
also asserted against leak markers before the file is read, so a careless addition refuses
rather than runs.

**Multiple testing.** 19 features × 5 buckets × 3 barrier pairs = **285 cells**. About fourteen
clear p<0.05 by chance alone and the best will look convincing. The null is therefore a
**max-statistic permutation**: shuffle the outcomes against the buckets, re-run the *entire*
search, take its best cell, repeat 300 times. That prices the search itself, which a per-cell
p-value does not.

## The result

```text
BEST CELL FOUND BY THE SEARCH
  feature funding_velocity   quintile 5/5   barriers 15/30   n=5,194
  mean EV -11.74 bps        (unconditional is ~-12 bps)

MAX-STATISTIC PERMUTATION NULL - 300 shuffles of the SAME search
  shuffled best: median -11.84   95th pct -11.75   max -11.53 bps
  p(shuffled best >= real best) = 0.043
```

The best conditioning state in the entire search is **0.26 bps less bad** than trading blind,
and still a 11.74 bps loss. **In 300 shuffles of pure noise the best cell found (−11.53) was
better than the best cell in real data.**

## A defect in this study's own verdict logic, caught and fixed

The first version branched on the p-value alone. At p = 0.043 it printed:

> *"the real best exceeds the shuffled distribution; a CANDIDATE for a pre-registered forward
> test"*

for a cell that **loses 11.74 bps**. A p-value below 0.05 on a loss is not a candidate for
anything — it says the loss is *reliable*. That is a check passing while the property it
guarantees is false, which is the defect this entire audit series has been about, committed by
the tool written to avoid it.

The verdict now requires **both** significance and profitability, and when a significant loss
occurs it says so explicitly rather than promoting it.

---

## The sweep, complete

```text
lane                             verdict   what closed it
taker on structural fair value   CLOSED    ask beats Phi(z) on Brier and log loss
cross-venue repricing lag        CLOSED    corr(past BTC move, next quote) = 0.0016
state selectivity                CLOSED    0 of 15 cells survive a round bootstrap
early exit (taker round trip)    CLOSED    20 of 20 rules worse than holding
maker at the touch               CLOSED    6.46% fill, -2.15c markout, deepening
Binance first-touch              CLOSED    -12 bps = exactly cost, every pair
conditional entry                CLOSED    best of 285 cells is a reliable loss
```

**Seven questions, seven closed.** Five by execution economics — the fee curve, the spread
crossed twice, the queue, adverse selection. Two by the absence of information itself: the
barrier geometry is a martingale, and no observable state in the matrix changes that.

## What remains genuinely untested

- **Sub-second cross-venue.** Blocked on a fast BTC tick series, not on Polymarket capture —
  `pm_l2` is at 32.5ms, the BTC reference at 1,177ms. The smallest remaining piece of work.
- **Posting deeper than the touch**, and two-sided maker quoting with inventory.
- **Feature families not in the matrix**, e.g. order-book imbalance at depth, options surface,
  liquidation cascades. The 19 columns tested are what exists, not what is possible.
- **Longer horizons.** Everything here is 5m/15m, the horizons the app serves.

`research/conditional_first_touch_entry.py` — read-only, standalone.
