# Market-neutral carry — the lane that does not predict direction — `2026-08-09`

Seven lanes have closed. Every one of them asked the market to move a particular way, so all
seven are exposed to the same finding: the barrier geometry is a martingale and no observable
state changes it.

Carry is not. Long spot against short perpetual is direction-neutral by construction, so it
**cannot inherit** that result. That is why it was worth testing separately rather than folding
into the same conclusion.

**Two P&L terms, two different verdicts.** Reporting one verdict for both would answer a
question that was never asked.

```text
basis convergence   CLOSED     by arithmetic, before any model
funding             UNMEASURED the cashflow was never recorded
```

---

## 1. Basis convergence — closed by arithmetic

`perp_spot_basis_bps` is a real series: 49,883 distinct values over 518,381 finite
observations, monthly means drifting −3.93 → −5.41 bps.

```text
p01 -6.83   p05 -6.12   p25 -5.23   median -4.65
p75 -4.07   p95 -3.23   p99 -2.52   mean -4.645

whole range   p05 -> p95   2.89 bps
              p01 -> p99   4.31 bps
hedged round trip          24.0 bps   (FOUR legs, see below)
```

**A perfect oracle entering at p05 and exiting at p95 every single time captures 2.89 bps and
pays 24.0.** Shortfall 21.1 bps, a factor of 8.3. No entry rule, no state selection and no
model changes that — the spread is smaller than the cost of touching it.

**The cost is four legs, not two.** Buy spot *and* sell perp to open, then unwind both. Costing
a hedged position as a single round trip is the easiest way to make this lane look viable, and
it halves the true cost.

And the basis does not mean-revert to a fixed point — it drifts:

```text
block 677   n=38,861   mean -4.049   std 1.271
block 679   n=43,200   mean -4.636   std 1.269
block 682   n=43,200   mean -4.553   std 0.813
```

A convergence trade needs a level to converge *to*. This one moves.

## 2. Funding — not in the data, and it is the dominant term

```text
funding_velocity              90.0% zeros, 36,986 distinct - a derivative, not a rate
binance_paper_funding_events  0 rows
```

The 8-hourly funding cashflow is the dominant P&L term in a real carry book, and this
repository has never recorded it. **This study therefore does not conclude on carry as a
whole.**

What can be stated is the bar. Published Binance BTCUSDT funding is typically ~0.01% per 8h —
an order of magnitude from documentation, **not a measurement here**:

```text
0.5 bps/8h -> 1.5 bps/day -> 16.0 days of holding to clear the 24 bps entry+exit
1.0 bps/8h -> 3.0 bps/day ->  8.0 days
2.0 bps/8h -> 6.0 bps/day ->  4.0 days
```

Those holding periods are not absurd. At the typical rate a hedge held a month would clear its
entry cost several times over — which is precisely why this must be **measured rather than
assumed**, and why quoting the published rate as a result would be inventing one.

## 3. Why this lane is different from the other seven

Every previous closure was economic: the fee curve, the spread crossed twice, the queue,
adverse selection, or an absence of information. Those are properties of the market and no
amount of data collection changes them.

**This one is blocked on data collection**, which is cheap and mechanical:

```text
record every 8h    the realised funding cashflow per unit of notional
                   the basis at entry and at each settlement
                   how often funding flips sign while a hedge is on
then               carry P&L = sum(funding) +/- basis change - 24 bps, measured
```

The recorder is a REST poll on a schedule, not a model. It is the same shape as the BTC tick
recorder built for the sub-second lane, and smaller.

## Standing caveats

- Spot borrow / margin cost is not modelled and is a real term for the short-perp side.
- Funding flipping negative while a hedge is on is the main risk and is exactly what the
  proposed recording would quantify.
- 24 bps assumes the shipped taker model on all four legs. Maker execution on any leg lowers
  it, and unlike the Polymarket book there is room to improve the quote on Binance.

`research/market_neutral_carry_lane.py` — read-only, standalone.
