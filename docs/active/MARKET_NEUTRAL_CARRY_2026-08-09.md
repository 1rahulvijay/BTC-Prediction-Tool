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
funding             OPEN/BOUNDED measured positive risk premium, small at this capital
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

## 2. Funding — now measured

```text
official settlements    3,500 from 2023-05-31 through 2026-08-09
mean funding            +0.6637 bps / 8h
mean annualized         +7.27% on notional before omitted costs
positive settlements    85.2%
funding sign flips       486
```

The historical mean clears the four-leg 24 bps execution estimate only when held long enough:

```text
holding period    mean net funding after 24 bps    profitable windows
5 days                         -14.04 bps                 6.6%
15 days                         +5.92 bps                50.7%
30 days                        +35.96 bps                77.6%
60 days                        +96.19 bps                90.4%
90 days                       +157.35 bps                92.0%
```

This is a measured risk premium, not a guaranteed trade. The regime varies materially:

```text
rolling 90-day annualized    -1.58% minimum, +5.47% median, +23.28% maximum
latest rolling 90-day        +4.76%
last 180 days                +1.76% annualized
last-180d 30-day windows     -11.52 bps mean net; 39.6% profitable
```

At the configured $500 Binance paper allocation, an unlevered two-leg hedge has about $250 of
matched notional. The 3.2-year mean is therefore about $18.17/year and the recent 180-day regime
about $4.41/year before basis change, borrow, margin, tax, slippage variance and operational
risk. The arithmetic works, but the dollar value is small.

## 3. Why this lane remains open but bounded

Every previous closure was economic: the fee curve, the spread crossed twice, the queue,
adverse selection, or an absence of information. Those are properties of the market and no
amount of data collection changes them.

Funding is the only tested lane whose sign and magnitude can clear its estimated execution
cost over long holding periods. It remains bounded because the premium changes regime, can turn
negative, requires capital on both legs, and is not large enough at the configured bankroll to
support an income claim.

## Standing caveats

- Spot borrow / margin cost is not modelled and is a real term for the short-perp side.
- Funding flipping negative while a hedge is on remains a measured risk: the series changed sign
  486 times, and recent funding is much weaker than the long-run mean.
- 24 bps assumes the shipped taker model on all four legs. Maker execution on any leg lowers
  it, and unlike the Polymarket book there is room to improve the quote on Binance.

`research/market_neutral_carry_lane.py` — read-only, standalone.

## Implementation update — 2026-08-09

`backend/funding_recorder.py` is now implemented and enabled by `start.bat`. On first launch it
backfills the configured historical window from Binance's public `fundingRate` endpoint, then
continues recording official settlements and mark/index basis into `data/funding.duckdb`.
It records funding in raw and basis-point units, settlement-time versus receive-time separately,
fixed-schedule gaps, sign flips, basis samples and independent heartbeats.

The initial backfill completed with 3,500 official settlements and
`research/market_neutral_carry_lane.py` was rerun. That closes the previous `UNMEASURED`
classification and yields the `OPEN/BOUNDED` result above. It does not promote carry into the
paper engine or prove live profitability. The recorder is public-data, read-only and has no
order path.
