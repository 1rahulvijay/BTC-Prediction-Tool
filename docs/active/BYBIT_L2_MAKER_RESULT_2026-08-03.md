# Bybit 200-level L2 maker execution — result

> **SUPERSEDED 2026-08-04 by `BYBIT_L2_MAKER_V2_TRADE_DRIVEN`.** The fill rule below counted
> depth decay as execution, so cancellations ahead in the queue were scored as fills. The 99.5%
> fill rate was the tell. Rerun against the real trade tape (845,890 prints):
>
> | | V1 (this doc) | V2 (trade-driven) |
> |---|---:|---:|
> | fill rate | 99.5% | **39.8%** |
> | gross markout 60s | +0.080 bps | **-0.562 bps** |
> | net per FILLED order | -0.920 bps | **-1.562 bps** |
>
> Roughly 60% of the fills here were cancellations. More importantly the gross markout
> **flipped sign**: cancellation-fills are the benign case where nobody crossed against you, so
> V1 reported adverse selection as favourable. Filtered to real executions it is monotone in
> horizon (1s -0.218 -> 60s -0.562) - textbook adverse selection.
>
> The CONCLUSION is unchanged and now honest: a passive fill does not cover the 1.0 bps fee. It
> is short by 1.56 bps per fill, not 0.92. Numbers below are retained as the historical record.



**Protocol** `PREREG_BYBIT_L2_MAKER_V1.md` sha256 `fc49c09d…`, frozen before any result ·
**Script** `research/bybit_l2_maker_v1.py` · Scored **once**

## The data source, and it is the best free one available

```
host        quote-saver.bycsi.com/orderbook/linear/BTCUSDT/
coverage    1,293 daily files, 2023-01-18 -> 2026-08-02   (current to yesterday)
format      JSON lines, snapshot + deltas, 200 levels, update IDs
day used    2026-08-02, 106 MB compressed
```

Acceptance evidence, run before the protocol was frozen:

```
records inspected            400,000
snapshots                    1, at the start
update-id discontinuities    0
```

Bybit's own `public.bybit.com` bucket has **no** orderbook path — only `trading`, `spot`,
`premium_index`, `spot_index`, and its `trading/BTCUSDT/` archive holds just 51 files from 2020.
The order book lives on a different host entirely. Worth recording, because the obvious place to
look does not have it.

## Result

```
1,435 orders, 1,428 filled (99.5%)
median spread 0.016 bps    median depth at touch 4.287 BTC

gross IMMEDIATE (ceiling)     +0.008 bps
gross OPERATIONAL (filled)    +0.080 bps
adverse selection             -0.072 bps        <- NEGATIVE
net per SUBMITTED order       -0.916 bps        hour-block 95% CI [-1.085, -0.769]
net per FILLED order          -0.920 bps

markout after fill:  1s +0.039   5s +0.037   15s +0.063   30s +0.102   60s +0.080
```

**VERDICT: `MAKER_SAVES_BUT_NOT_ENOUGH`.** The interval lies entirely below zero.

## The fee is the whole answer

```
gross value of a passive fill   +0.080 bps
maker fee                       -1.000 bps
                                ----------
net                             -0.920 bps
```

A passive fill on Bybit BTCUSDT perp is worth **eight hundredths of a basis point**. The maker
fee is more than twelve times that. This conclusion does not depend on any fill-model subtlety,
because the fee dominates by an order of magnitude.

## A fill-model limitation that must be stated

Adverse selection here is **−0.072 bps** — fills were mildly *favourable* — against **+1.526 bps**
on Binance top-of-book. That is a large reversal, and it should not be read as "Bybit fills are
better".

The two tests use different fill rules, because they had different data:

```
Binance   a trade printed at or through the posted price, consuming visible size
Bybit     the level's remaining size fell below the queue that stood ahead at posting
```

**Queue depletion is not the same as a fill.** A level shrinks when orders ahead are *cancelled*
as well as when they *trade*, and a cancellation ahead of you moves you up the queue without
filling you. This model counts both.

The **99.5% fill rate is the tell.** A passive order at a 60-second horizon does not fill 99.5%
of the time in reality. That number says the fill condition is too permissive, and permissive
fills are selected *non-adversely*, which is exactly the direction the adverse-selection figure
moved.

So: the Binance figure is the more trustworthy estimate of adverse selection, and this one is
optimistic. The fix is to join Bybit's tick trades — which exist at `public.bybit.com/trading/`
— and require trade-driven depletion specifically. That is a different protocol with a different
hash, and the economic conclusion above does not wait on it.

## A bug I introduced and caught

The first run printed `markout: nan` at every horizon, `gross OPERATIONAL +0.000`, and
`net per FILLED` of exactly **−1.000** — precisely the maker fee.

Cause: `done` was set to `True` on fill, and the resolution loop began with
`if order["done"]: continue`, so the orders that filled were exactly the ones whose markouts
were never computed. Every gross figure was zero by construction.

Exactly −1.000 was the signal that something was wrong: a real market does not return a value
that is precisely the fee. Fixed by separating "no longer eligible to fill" from "finished
accruing markouts", with a selftest that asserts a filled order's net is *markout minus fee*
rather than the fee alone.

The 11-check selftest now covers this, and it is why the published numbers are the second run
and not the first.

## Real depth did change what could be measured

The two earlier maker tests had top-of-book only: queue position beyond level 1 was invisible
and orders larger than the visible best size had to be excluded. Here the queue ahead is a
measured quantity, and median depth at the touch is reported (**4.287 BTC**) — an order of
magnitude larger than the 0.01 BTC test order, which is why fill modelling matters at all.

## Replay integrity

The book is rebuilt from the opening snapshot and deltas applied in `u` order, with invariants
enforced rather than assumed:

```
best bid < best ask                      violation -> ReplayInvalid, no verdict issued
update ID must not go backwards          violation -> ReplayInvalid
size "0" removes the level                asserted in the selftest
a later snapshot resets the book          asserted, and exempt from the sequence check
```

A repaired book would be a fabricated book, so a violated invariant stops the run and produces
`REPLAY_INVALID` rather than a number. The full day replayed with no violation.

## Limits

- **One day**, hour-blocked. Real depth removes the top-of-book limitation; it does not turn a
  single day into a forward claim.
- **BTCUSDT only**, and it is the tightest-spread instrument. The archive covers 1,293 days and
  many symbols, so this is a sampling choice, not a data limit.
- **Fill model counts cancellations** — see above. Optimistic, and the direction of the bias is
  known.
- Order size 0.01 BTC against 4.287 BTC of depth: this measures the *cost of a passive fill*,
  not capacity.

## Where this leaves passive execution

```
gross edge available          +0.97 to +1.97 bps   (MULTIHORIZON, measured)
taker round trip                     14.0  bps
maker round trip                      2.0  bps     (fees only)
value of a passive fill, Binance     -1.33  bps    (top-of-book, adverse)
value of a passive fill, Bybit L2    +0.08  bps    (real depth, optimistic fills)
```

Even taking the **most favourable** measurement in this repository — a passive fill worth
+0.08 bps with real depth and an optimistic fill rule — the 1.0 bps maker fee per side leaves it
negative, and the 2.0 bps round trip still exceeds the 1.97 bps best measured gross edge.

Passive execution stays closed on perpetual venues. The evidence for that is now stronger, not
weaker, because it survives the best data available.
