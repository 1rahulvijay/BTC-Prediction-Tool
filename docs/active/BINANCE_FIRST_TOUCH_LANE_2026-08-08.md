# The Binance first-touch lane — `2026-08-08`

The last open lane after five Polymarket lanes closed. A different contract on a different
venue: not a binary settling at a fixed anchor but *"does price reach +X bps before −Y bps"*,
with a linear payoff and a flat bps cost instead of a probability-dependent fee.

**Result: every barrier pair at both horizons returns ≈ −12 bps, which is exactly the shipped
round-trip cost. The process is a martingale.**

518,400 one-minute bars (360 days), disjoint windows, ambiguous bars refused.

---

## The competitor is the martingale, not a coin flip

Under a driftless walk the first-touch probability is closed-form:

```text
P(hit +X before -Y) = Y / (X + Y)

EV = p·X - (1-p)·Y = [Y/(X+Y)]·X - [X/(X+Y)]·Y = 0     exactly, for every X and Y
```

Widening the target and tightening the stop does not create edge — it trades a lower hit rate
for a larger win by precisely the amount that keeps EV at zero. **After costs every pair is
worth −cost.** A grid search that reports "target 30 / stop 15 wins 33% of the time!" has found
`15/45 = 0.333` and nothing else.

## The measurement

```text
5m horizon, disjoint windows          cost 12.0 bps (shipped model)
target/stop        n  timeout  ambig  observed p  martingale   EV bps   5th pct
10/10        103,165     52%     514      0.4967      0.5000   -12.03    -12.07
15/15        103,564     71%     115      0.4948      0.5000   -12.04    -12.09
20/20        103,638     82%      41      0.4920      0.5000   -12.04    -12.10
10/20        103,549     66%     130      0.7581      0.6667   -12.16    -12.21
20/10        103,541     66%     138      0.2340      0.3333   -11.91    -11.95
20/40        103,670     89%       9      0.8315      0.6667   -12.04    -12.10
40/20        103,669     89%      10      0.1643      0.3333   -12.06    -12.12

15m horizon: same pattern, EV -11.84 to -12.36 bps in every cell.
```

Eighteen cells. Every EV within 0.4 bps of −12. Every lower bound negative.

## The trap in that table

`observed p` deviates from the martingale by **up to 17 points** — 0.8315 against 0.6667 at
20/40. That looks like a large, systematic edge and it is **not**.

`Y/(X+Y)` is the **unbounded-time** formula. These windows expire, and the `timeout` column
shows how often: **82% at 20/20 and 89% at 20/40**. So `observed p` is conditioned on the small
decided minority, which is dominated by whichever barrier is *nearer* — near targets look better
than the martingale and far ones look worse, by exactly the amount the conditioning implies.

`EV bps` is the column that settles it, because it **includes the timeouts at their realised
exit**. That is why every row sits at −cost despite the p-column swinging by 17 points.

Reading the p-column as edge would be the same error as reading a 66% win rate as skill, and
the study now prints the warning inline.

## Relation to prior art

Section 10.5 test 106 ran a frozen 4×4 grid over 8,639 disjoint **60m** windows and found no
cell cleared costs, with barriers near-symmetric (48.4% vs 48.7%). This is additive on three
points: the horizons the app actually serves (5m/15m), the null stated as the martingale rather
than 50%, and the shipped cost model rather than an assumed number. It reaches the same
conclusion on 12× the windows.

## What is not tested

**Conditional entry.** Every window above is entered unconditionally. The martingale result
says the *unconditional* process has no barrier edge; it does not say no conditioning signal
exists. That is the same question the direction head already answered negatively across 13
model families and 17 microstructure features, which is why it is recorded as untested rather
than promising.

**Maker execution on Binance**, where the cost is a rebate rather than 12 bps. The measured EV
is −12.0 bps against a 12.0 bps cost, so the gross edge is ≈ 0.0 bps — a maker rebate would
move the result to approximately break-even, not to profit.

---

## The sweep is complete

```text
taker on structural fair value   CLOSED   ask wins on Brier and log loss
cross-venue repricing lag        CLOSED   at >=2s; sub-second blocked on BTC tick data
state selectivity                CLOSED   0 of 15 cells survive
early exit (taker round trip)    CLOSED   20 of 20 rules worse than holding
maker at the touch               CLOSED   6.46% fill, -2.15c markout, deepening
Binance first-touch              CLOSED   -12 bps = exactly cost, at every barrier pair
```

Six lanes, six closed. Five were closed by execution economics. This one is closed by something
more fundamental: **the gross edge is zero, not small.** The barrier geometry has no
information in it to begin with, which is what a martingale means.

`research/binance_first_touch_lane.py` — read-only, standalone.
