# The maker lane — earn the spread, or get picked off? — `2026-08-08`

The early-exit study closed the taker round trip and pointed here: filling at the bid instead
of paying the ask is a ~2c swing, larger than the margin by which every taker rule lost. This
was the one remaining lane whose cost structure was not already decisive.

**It is decisive now. A resting bid fills 6.46% of the time, and the fills it gets are toxic.**

Measured on the nanosecond `pm_l2` store — 838 BTC round assets, 321,279 valid book snapshots,
16,303 aggressive sells. The 1.95s round recorder cannot see any of this.

---

## The structure, before any simulation

```text
87.5% of valid books sit at a ONE CENT spread  = the minimum tick
median queue ahead of you                      = 186 shares
median aggressive sell                         = 6.66 shares
-> ~28 consecutive sells needed to reach your order
total aggressive sells                         = 16,303 over 898 assets ≈ 18 per asset
```

Two consequences, neither of which depends on a forecast:

1. **A maker cannot price-improve.** At a 1-cent spread on a 1-cent tick there is no room
   between the quotes. You join the **back** of the queue at the touch or you cross.
2. **~18 sells available against ~28 required.** The queue, not the prediction, is the
   binding constraint.

## 1. Fill rate

```text
orders posted (one per 25 book events)   13,263
orders filled within 30s                    857
FILL RATE                                 6.46%

median queue ahead on FILLED orders          18
median queue ahead on ALL posts             186
```

**Fills happen where the queue was ten times thinner than usual.** That is a selection effect
on the state, not something a strategy chooses. Any backtest that assumes a fill whenever a
trade prints at your price grants priority nobody has, and would have reported a fill rate
around 100% here instead of 6%.

## 2. What the fills were worth

Maker platform fee is zero, so the markout **is** the P&L.

```text
horizon      n   mean markout    5th pct
+1s        732        -2.15c     -2.46c
+5s        856        -2.38c     -2.68c
+30s       857        -2.74c     -3.28c
```

Negative at every horizon with a negative lower bound at every horizon. Decomposed:

```text
paid for supplying liquidity (half-spread)   +0.77c
cost of who took it (adverse selection)      -2.93c
net                                          -2.15c
```

**You are paid 0.77c for an option that costs 2.93c.**

The markout **deepens** with horizon — −2.15c → −2.38c → −2.74c. That is the signature of real
information in the taking flow. Bid-ask bounce would decay toward zero; this grows, which means
the people hitting the bid know something that keeps being true.

## 3. Could the rebate close it?

The crypto maker rebate pool is funded from taker fees, and the taker fee peaks at 1.75c/share.

```text
a FULL 1.75c rebate on every fill   leaves  -0.40c
a 20% share                         leaves  ~-1.80c
```

Neither closes a 2.15c gap. The rebate is a real economic improvement and it is not the right
order of magnitude for this problem.

---

## What this does not test

- **Posting deeper in the book.** A bid one or more ticks below the touch gets a better price
  and worse fill odds. Given a 6.46% fill rate at the touch, deeper is unlikely to help, but
  it is untested and the honest statement is untested rather than hopeless.
- **Two-sided quoting with inventory management.** This posts bids only. A genuine market
  maker quotes both sides and earns on the round trip; the adverse-selection measurement above
  applies to each leg, but the joint behaviour is not simulated.
- **Only ~2 days.** 2026-07-02 to 07-04 is the full extent of the L2 capture.
- **Rebate eligibility.** Assumed unavailable rather than modelled; see above for why it does
  not change the conclusion.

---

## The lane sweep is complete

```text
taker on structural fair value   CLOSED   ask wins on Brier and log loss
cross-venue repricing lag        CLOSED   at >=2s; sub-second blocked on BTC tick data
state selectivity                CLOSED   0 of 15 cells survive; DOWN column is base rate
early exit (taker round trip)    CLOSED   20 of 20 rules worse than holding
maker at the touch               CLOSED   6.46% fill, -2.15c markout, deepening
Binance first-touch              OPEN     different contract, different venue, untested here
```

Five lanes measured, five closed, each with the negative recorded so it is not re-proposed.
Every one of them was closed by **execution economics** rather than by forecasting skill: the
fee curve, the spread crossed twice, the queue, and adverse selection. That is a consistent
finding and it is worth stating plainly — on this venue, at this size, the constraint has not
once been the model.

`research/maker_lane.py` — read-only, standalone.
