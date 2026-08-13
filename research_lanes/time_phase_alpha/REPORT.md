# TIME_PHASE_ALPHA_V1

**Verdict: NO EFFECT.** The quarter-hour clock structure reported for crypto perpetuals does
not appear in 5-minute forward move size here. Intervals overlap.

Run 2026-08-13 · `research_lanes/run_matrix_lanes.py`

---

## Question

A 2026 paper reports periodic bursts around one-, five- and quarter-hour marks in Binance
perpetuals. Its stronger effect is at longer horizons, so this is an independent test at the
app's own horizon rather than a replication:

> Does minute-of-quarter carry structure in 5-minute forward |move|?

## Method

518,400 one-minute bars, 360 UTC days. For each bar, minute-of-quarter (0–14) and forward
|5m move| in bps. Compare the most extreme bucket against all others, both bounded by
day-block bootstrap.

**The max of 15 buckets is a biased statistic** — some bucket is always highest. So the
comparison is not "is bucket 14 higher" but "does its lower bound clear the rest's upper
bound." That distinction is the whole test.

## Result

| | mean \|move\| bps | bound |
|---|---:|---|
| hottest bucket (minute **14** of quarter) | 9.33 | LCB **8.87** |
| all other minutes | 8.70 | UCB **9.10** |
| **separated?** | | **No** — 8.87 < 9.10 |

The intervals overlap. The 0.63 bps difference is not distinguishable from the noise of picking
the maximum of fifteen buckets.

Minute 14 being the hottest is *directionally* consistent with the paper's claim — activity
concentrating just before the quarter mark — and that is exactly why the bound matters. Without
it, "minute 14 shows +7% higher volatility" is a sentence this data does not support.

## Full bucket profile

Mean forward |5m move| in bps by minute-of-quarter, for reference. The spread across all
fifteen buckets is under 1 bps against a mean of 8.7:

`{0..14}` values recorded in `research_lanes/matrix_lanes_results.json` under
`time_phase.by_minute_of_quarter`.

## Interpretation

Even had it separated, the size disqualifies it economically. A 0.63 bps effect against a
12 bps round trip is 5% of one cost unit. There is no execution of this that pays.

## What was not tested

- **Sub-minute phase.** Second-within-minute and the seconds bracketing the 5m/15m boundary.
  The matrix is 1-minute bars, so this lane structurally cannot see it. The paper's mechanism
  is algorithmic scheduling, which would plausibly live at second resolution. Testing it needs
  the tick recorder, not the matrix.
- **Volume, spread, OFI phase.** Only |move| was tested. Phase structure could exist in
  liquidity without existing in realized move.
- **Longer horizons**, where the source paper reports its stronger effect.

So this is a negative result about **5-minute move size at 1-minute resolution**, not about
clock effects in general.

## Attacks applied

| attack | status |
|---|---|
| day-block bootstrap | yes — 360 independent days |
| selection-bias correction | yes — max-of-15 bounded rather than reported bare |
| beat a baseline | n/a — no effect to beat one with |
| economic screen | yes — 0.63 bps vs 12 bps cost |
