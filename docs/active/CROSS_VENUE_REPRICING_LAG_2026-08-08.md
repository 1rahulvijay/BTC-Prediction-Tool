# Cross-venue repricing lag — is the Polymarket quote slow? — `2026-08-08`

The last lane the fair-value study did not close. It asks a **different kind of question**:
not "do we forecast the settlement better than the market" (answered: no) but "is the
market's own price *late*". If BTC moves and the quote takes seconds to follow, the stale
quote is executable and the lag itself is the alpha — no forecasting skill required.

**Result: no multi-second lag. The lane is closed at the resolution this data can see.**

---

## The limitation, stated before the result

The recorder's median inter-snapshot gap is **1.95 seconds**.

```text
p05 1.81s   p25 1.84s   median 1.95s   p75 2.20s   p95 2.92s
```

Latency arbitrage normally lives at 50–500ms. **This test cannot see a lag shorter than ~2
seconds.** A null result here means *no multi-second lag*, not *no lag*. Reporting it as the
latter would be the overclaim this repository keeps finding in its own history.

---

## 1. Absorption — how much of the implied move does the quote take?

22,105 impulses (implied fair-value change ≥ 1c) across 548 rounds:

```text
k=0   same interval as the BTC move       2.5%   contemporaneous
k=1   1 interval later                   -1.9%   no further move
k=2   2 intervals later                  -1.2%   no further move
k=3   3 intervals later                   0.6%   no further move
                        cumulative by k=3  0.1%
```

The quote does not absorb the implied move at k=0 **and never catches up**. That shape is not
a lag. A lag looks like small-at-k=0 followed by materially positive at k>0; this is
small-at-k=0 followed by nothing.

## 2. Trading the leftover

```text
hold        n        gross    fee     NET     5th pct
t+1    22,105      -0.62c   1.39c   -2.01c    -2.06c
t+2    22,105      -0.67c   1.39c   -2.06c    -2.14c
t+3    22,105      -0.65c   1.39c   -2.04c    -2.14c
```

Loses at every horizon, before the fee, with a negative round-clustered lower bound.

## 3. The model-free check — the decisive one

Sections 1 and 2 both depend on a structural fair value that
`POLY_FAIR_VALUE_VS_ASK_2026-08-08.md` showed is **worse than the ask**. So low absorption is
ambiguous: either the quote is slow, or the fair value is wrong. Removing the model entirely:

```text
correlation between a BTC log-return and the quote mid change
k=0   n=86,599   r =  0.0253   contemporaneous response
k=1   n=86,599   r =  0.0016   no predictive power
k=2   n=86,599   r = -0.0058   no predictive power
k=3   n=86,599   r =  0.0014   no predictive power
```

**An already-observed BTC move carries no information about the next quote change.** There is
nothing to be early to. This holds without reference to any model, so it is not contaminated
by the fair value being wrong.

## 4. What the leftover actually is

```text
median quoted spread at the impulse   1.00c
median taker fee at that price        1.57c
round-trip cost                       2.57c
mean unabsorbed implied move at k=0   3.91c
```

3.91c > 2.57c looks like an opportunity and **is not one**. That number is the size of the
model's *disagreement* with the price, and section 2 shows acting on it loses at every
horizon. A quantity exceeding a cost is only an opportunity if it is information.

The study prints that warning inline. An earlier draft printed
`"the leftover exceeds the cost"` with no such note, which is exactly the defect shape this
repository keeps finding — a number that reads as a finding while the measurement beside it
says the opposite.

---

## Where this leaves the lanes

```text
taker on structural fair value    CLOSED   ask wins on Brier and log loss
cross-venue repricing lag         CLOSED   at >=2s; sub-second BLOCKED on BTC tick data
market residual alpha             OPEN     bar raised; must beat the ask
maker / toxicity                  OPEN     different economics, untested
Binance first-touch               OPEN     different contract and venue
```

## What would reopen it — and what is already on disk

I nearly wrote "this needs a recorder that does not exist". That would have been wrong, and
checking took two minutes:

```text
polymarket_l2.duckdb
  pm_l2_book_levels     25,809,455 rows
  pm_l2_raw_events         751,330 rows   272,274 of them 'book' events
  median inter-event gap      32.5ms      p05 1.7ms, p95 391ms
  covers                       449 btc-updown rounds, 2026-07-02 .. 07-04
  timestamps                   recv_ts_ns (nanosecond) + exchange_ts_ms
```

**The Polymarket side of a sub-second test already exists.** It is nanosecond-timestamped, it
covers the right markets, and at 32.5ms it is 60× faster than the round recorder used above.

The blocker is the **other** side:

```text
microstructure.duckdb
  crossvenue_snapshots     198,036 rows
  l2_snapshots             198,036 rows
  median inter-event gap     1,177ms       <- the BTC reference
  covers                   2026-06-21 .. 07-04  (overlaps the L2 window)
```

A lead/lag measurement is limited by the *slower* series. With BTC sampled every ~1.2s you
cannot resolve a 200ms lead however fast the Polymarket book is recorded.

So the precise statement is: **the sub-second question is blocked on a fast BTC tick series,
not on Polymarket capture.** That is a much smaller and more specific piece of work than
"build a recorder" — the aggTrade stream the paper engine already consumes is the natural
source, aligned to the existing L2 window.

An intermediate test at ~1.2s resolution is runnable today on the 2026-07-02..07-04 overlap.
It would tighten the bound from 2s to 1.2s. It would still not reach the range where this kind
of alpha usually lives, which is why it is recorded here as an option rather than run.

`research/cross_venue_repricing_lag.py` — read-only, standalone.
