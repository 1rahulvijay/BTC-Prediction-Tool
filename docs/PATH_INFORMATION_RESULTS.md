# Path Information Test — results

Reproduce: `python research/path_information_test.py --rows 200000`

Tests whether a signal carries information about the **path** of a 15-minute window even when it
carries none about the **settlement**. All 31 earlier scripts measured only `close(t+15) > close(t)`
and reported ~AUC 0.50. That measured the endpoint, never the excursion.

All four tests are **diagnostics**, run out-of-sample, against a **matched-random control** with
the same entry count and the same holding period. MFE/MAE appear only as labels — no feature is
derived from the future.

---

## Test 1 — directional path information: NONE

| signal | n | settle acc | P(MFE≥5) | P(MFE≥10) | P(MFE≥20) | P(MFE≥40) |
|---|---:|---:|---|---|---|---|
| flow_imbalance | 2394 | 49.2% | 58.6 / 58.4 | 34.3 / 35.7 | 11.7 / 13.9 | 2.0 / 2.7 |
| flow_reversal | 289 | 50.9% | 62.3 / 58.3 | 38.1 / 35.7 | 10.0 / 14.0 | 1.7 / 2.7 |
| momentum_baseline | 17924 | 47.8% | 56.6 / 56.6 | 34.7 / 34.7 | 14.0 / 14.0 | 3.1 / 3.1 |

*(signal% / matched-random%)*

**Not one cell beats its control.** `momentum_baseline` is identical to random to the decimal.
Direction is dead along the path, not merely at the endpoint — which closes the hypothesis that
the 0.50 AUC was hiding a tradeable excursion. Signed order flow and VPIN, tested here for the
first time, do not change that.

## Test 2 — excursion timing: UNIFORM, for every signal

Median argmax 6–9 min with q25–q75 spanning 2–12 min in every case. The best moment to exit is
distributed across the whole window.

**No clock-based exit is learnable.** A fixed "exit at minute k" rule cannot work, which rules
out the simplest form of dynamic profit-taking.

## Test 3 — two-sided magnitude: **REAL, and significant**

Sign discarded. Does a *move* happen at all?

| signal | n | P(\|mv\|≥5) | P(\|mv\|≥10) | P(\|mv\|≥20) | P(\|mv\|≥40) |
|---|---:|---|---|---|---|
| **rv_term_inversion** | 2272 | 96.4 / 94.8 ★ | 82.1 / 77.1 ★ | 46.7 / 39.2 ★ | **16.3 / 10.6 ★** |
| **shock** | 2598 | 96.3 / 94.8 | 80.4 / 77.1 ★ | 43.1 / 39.2 ★ | 13.4 / 10.6 ★ |
| flow_reversal | 955 | 97.3 / 94.8 ★ | 81.2 / 77.1 | 40.5 / 39.2 | 9.7 / 10.6 |
| compression_release | 2211 | 89.2 / 94.8 | 68.0 / 77.1 | 27.0 / 39.2 | 5.6 / 10.6 |
| vpin_spike | 2713 | 95.5 / 94.8 | 73.6 / 77.1 | 31.9 / 39.2 | 6.2 / 10.6 |
| momentum_baseline | 59924 | 94.8 / 94.8 | 77.1 / 77.1 | 39.2 / 39.2 | 10.6 / 10.6 |

★ = beats the matched-random control at Bonferroni α = 0.00089 (56 comparisons)

**`rv_term_inversion` (`rv_15m / rv_60m > 1.5`) is significant at all four thresholds**, and the
lift grows with size: **1.54× at 40 bps** (16.3% vs 10.6%). `shock` is significant at three of
four. This is the only positive result anywhere in this repository's research that survives
correction for multiple testing.

`compression_release` is significantly *worse* than random at every level — coiled ranges were
followed by **smaller** moves, the opposite of the usual claim.

## Test 4 — first passage: structural, not signal

| signal | +k/−j | n | win% | breakeven% | edge |
|---|---|---:|---:|---:|---:|
| flow_imbalance | +10/−20 | 4791 | 72.5% | 66.7% | +5.8 |
| flow_reversal | +10/−20 | 618 | 72.3% | 66.7% | +5.7 |
| **momentum_baseline** | +10/−20 | 35721 | 71.7% | 66.7% | **+5.0** |

The `+10/−20` structure shows a positive edge for **every** signal *including the zero-information
baseline*. That makes it a property of BTC's short-horizon path distribution — risking 20 bps to
make 10 wins more often than 2:1 — not a property of any signal.

And it does not survive costs: the target is **10 bps** against a **9 bps** round trip. The
`+20/−10` and `+30/−10` structures, which would clear costs, are all sharply negative.

---

## What this establishes

1. **Direction is dead** — at settlement *and* along the path. Two independent measurements now
   agree, and the second used features the first never touched.
2. **Dynamic exit timing is not learnable** from a clock. Excursions are uniformly distributed.
3. **Magnitude is predictable.** Volatility term-structure inversion genuinely forecasts large
   moves, with a lift that increases with move size.
4. **The `+10/−20` "edge" is an artifact** of path structure and dies on costs. It would have
   looked like a discovery without the baseline comparison.

## The honest problem with finding 3

Predicting **|move| without direction** is only monetizable with an instrument that pays on
magnitude. Checking the venues actually available:

- **Polymarket binaries — no.** A contract still settles on *direction*. Knowing the move will be
  large does not change P(up), and buying both YES and NO costs ~$1 for a $1 payoff: a
  guaranteed loss.
- **Binance directional futures — not directly.** Long or short still needs a side.
- **Binance breakout bracket — yes, in principle.** Resting stop-entries both sides; whichever
  triggers rides the move. This is a synthetic long-gamma position and is the natural expression
  of this signal. Untested.
- **Deribit options — yes, cleanly.** A straddle is the textbook instrument. Requires the options
  chain, which is not collected.

Note also that vol clustering is one of the most robust and widely known effects in finance. That
this signal works is a validation that the measurement is sound; it is not a proprietary
discovery, and the edge — if any — will be in **execution and instrument choice**, not in the
prediction.

## Next test, and its gate

`BREAKOUT_BRACKET_V1` — resting stop-entries on both sides, triggered only when
`rv_term_inversion` fires, exits by trailing stop.

It must clear the bar the `+10/−20` result failed: **positive after the full 9 bps round trip**,
beating a matched-random control with the same trigger count *and* the same holding time, with a
day-block lower confidence bound above zero.

Given a 40 bps threshold at 16.3% hit rate, the arithmetic is tight but not obviously impossible.
That is the first thing this suite has produced worth a real test.


---

# BREAKOUT_BRACKET_V1 — result: does not monetise it

Reproduce: `python research/breakout_bracket_test.py`

The magnitude finding needed an instrument. On Binance the natural expression is a breakout
bracket — resting stop-entries both sides, whichever triggers rides the move. Synthetic long
gamma, no direction required.

| entry | trail | fills | no-fill% | whip% | net bps | **control bps** | lift | day LCB |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 10b | 10b | 1866 | 17.9% | 1.0% | −11.40 | −10.38 | −1.02 | −11.99 |
| 10b | 40b | 1866 | 17.9% | 1.0% | −9.48 | −8.17 | −1.32 | −10.73 |
| 20b | 40b | 1061 | 53.3% | 0.0% | −9.02 | −8.77 | −0.25 | −11.10 |
| 30b | 40b | 616 | 72.9% | 0.0% | −9.29 | −10.32 | **+1.03** | −12.42 |

Entry and exit both taker. Both stops touched inside one bar is charged as a full whipsaw.

**All nine configurations lose, and the control loses just as much.** That is the decisive
column: the bracket bleeds whether or not the magnitude signal fired, so the loss is structural,
not a signal failure.

The one cell with positive lift (30b/40b, +1.03) is lift over a *losing* control — −9.29 bps
net, day LCB −12.42. Lift over a loss is not profit.

## Why it fails, structurally

A bracket enters only **after** price has already travelled `entry` bps. It pays the cost of
being late on every trade and collects only the remainder:

- entry 10 bps → 17.9% never fill, and those that do give back the 10 bps plus 9 bps of cost
- entry 30 bps → whipsaws vanish, but **72.9% never fill** and the remaining move is smaller

Widening the entry to cut whipsaws also cuts what is left to capture. There is no setting where
both work, which is why this is not a tuning problem.

## What this does and does not close

**Does not** invalidate the magnitude finding. `rv_term_inversion` still predicts large moves,
still survives Bonferroni, still shows lift growing with size. Two independent tests now agree.

**Does** close the Binance directional route to harvesting it. Knowing a move is coming is worth
nothing if the only available instrument requires you to be late to it.

## The instrument that fits

A **Deribit straddle** never picks a side, so it does not pay the lateness cost that kills the
bracket. That is the textbook expression of "large move, direction unknown", and it is the one
instrument on the shortlist that structurally matches the signal.

It needs the per-strike options chain, which is **not collected** — the client polls five
aggregate scalars every 30 s and persists nothing.

**Concrete next step:** persist the Deribit chain (strike, bid, ask, mark IV, expiry). That is a
recorder change, needs no credentials, and it is now justified by a measured signal rather than
by a hypothesis — which is a materially better reason than the one behind the original V4
proposal.

---

# COMPLETE-SET ARBITRAGE — result: exists, economically negligible

Reproduce: `python research/complete_set_arbitrage_test.py`

UP + DOWN must equal $1 at settlement. No forecast, no direction, no model — the arithmetic is
fixed by the settlement rule. This was the one idea that could not be wrong in an interesting way.

**Sample:** 2026-07-02 → 2026-07-04 (2.17 days), 419 paired conditions, 158,906 simultaneously
quoted observations (median quote skew **0 ms**), valid + synchronized books only.

## The market is efficiently quoted

```
UP_ask + DOWN_ask : median 1.0100
UP_bid + DOWN_bid : median 0.9900
```

A **1-cent spread centred exactly on $1.** That penny is the market maker's income. A taker
cannot reach it — crossing costs you the spread by construction.

## The opportunities that do exist

| assumed cost | buy-side opps | % of quotes | median size | total $ edge |
|---|---:|---:|---:|---:|
| 0.0c | 246 | 0.155% | 10.2 | **$78** |
| 1.0c | 99 | 0.062% | 7.7 | $30 |
| 3.0c | 19 | 0.012% | 8.0 | $8 |

Sell side is similar: $121 at 0c, $40 at 3c.

**Total theoretical edge across the entire 2.17-day window: ~$200.** Annualised at 100% capture,
~$34k — and that assumes you win *every* opportunity, with both legs filling simultaneously,
against everyone else watching the same book.

A typical opportunity is **10 shares × 1 cent = $0.10.**

## Verdict

Complete-set arbitrage is **real but not a business.** It appears in 0.15% of quotes at sizes
around ten shares. You cannot deploy $100k into $0.10 opportunities, and the two-leg execution
risk is asymmetric: miss one leg and a riskless spread becomes an outright directional position —
the precise risk the structure exists to avoid.

## The finding underneath the finding

The median tells the real story. **The market quotes 1.0100 / 0.9900** — someone is already
making this market, tightly, around the theoretical value. The penny spread is not an
inefficiency; it is the compensation for providing that liquidity.

Which means the only way to earn it is to **be the market maker rather than the taker** — and
that loops directly back to the liquidity-provision lane, which needs queue position, which needs
sequenced L2. Every structural path now converges on the same prerequisite.
