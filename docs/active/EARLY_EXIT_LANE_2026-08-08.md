# The early exit lane — selling before settlement — `2026-08-08`

The last untested lane, and the one nothing had argued against. Every prior study scores a
position at settlement, so a round that trades to 0.67 and then settles DOWN is a total loss
in that accounting and could have been a gain with an exit rule.

**The difference is real and large. It is also unreachable.** 1,088 entries across 545 settled
rounds, one per side per round, entering 25% into the path.

---

## 1. The ceiling — what a perfect oracle exit would capture

Not a strategy. `exit at the maximum` is a measurement of the maximum. Reported because a
ceiling below cost would be decisive on its own.

```text
MFE of the bid over the entry ask
  median +23.00c   p75 +41.00c   p90 +55.00c   p99 +72.00c

ORACLE exit      mean  +23.26c/share
HOLD to settle   mean   -2.00c/share
oracle premium         +25.25c/share
```

So there **is** a very large budget — 25 cents per share of difference between holding and a
perfect exit. That is the entire prize any real rule competes for, and it is far above the
~2.5c round-trip cost. On this evidence alone the lane looks extremely promising.

## 1b. Why the budget is unreachable

```text
median spread at entry              1.00c
the exit bid starts                -1.00c below the ask you paid
so a nominal +2c target needs       3.00c of favourable move
while a nominal -3c stop needs only 2.00c against you
```

**Entering at the ask and exiting at the bid means the position opens one cent underwater.** A
symmetric-looking ±2/3c band is asymmetric *against* the position by the full spread, before
any forecasting. Measured at the tightest grid cell, **55% of first crossings are stops**.

This is structural. No choice of thresholds removes it, because it is the cost of crossing the
spread twice.

## 2. What is reachable — a frozen grid, walked causally

Exit at the first snapshot where the bid crosses the threshold; if it never does, settle. No
peeking.

```text
take profit  stop        n   exit rate   mean net    5th pct   vs hold
2%           3%      1,088       100%     -4.00c     -4.12c    -2.00c
3%           5%      1,088       100%     -3.97c     -4.08c    -1.97c
5%           none    1,088        81%     -3.63c     -4.58c    -1.64c
8%           5%      1,088        99%     -3.87c     -4.17c    -1.88c
12%          none    1,088        68%     -3.86c     -5.03c    -1.86c
```

**Every one of the twenty cells is worse than holding**, and holding is itself −2.00c. The best
by lower bound (tp 3%, stop 5%) is −3.97c mean with a 5th percentile of −4.08c.

The pattern is uniform: adding an exit adds a second set of costs to an entry that was already
unprofitable. The exit rate near 100% at the tight cells is the spread asymmetry doing its
work — positions get stopped before they can travel.

---

## What this closes, and the one thing it opens

**Closes:** early exit as a **taker round trip**. Two spread crossings and two fees against an
entry with no edge is worse than one crossing and one fee.

**Opens — and this is the useful part:** the whole result is driven by *paying* the spread on
entry. A **maker** entry fills at the bid rather than the ask, which turns the 1c handicap into
a 1c head start and removes the platform fee on that leg. The same grid, entered as a maker,
starts 2c better per round trip than what is measured above — larger than the entire margin by
which these rules lose to holding.

That does not make the maker lane profitable. It makes it the **only remaining lane where the
cost structure is not already decisive**, and it is now the highest-value thing left to test.
The blocker there is adverse selection: a resting bid fills precisely when the fair value has
moved against it, which this data cannot see and which the 32.5ms `pm_l2` store can.

---

## Ordering note

The oracle premium of +25c is exactly the kind of number that justifies a large modelling
effort — predict the exit point, capture a fraction of 25c, done. Measuring the *reachable*
part first cost one script and closed it. The ceiling was never the constraint; the spread was.

`research/early_exit_lane.py` — read-only, standalone.
