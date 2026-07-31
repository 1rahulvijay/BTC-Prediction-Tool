# Options-Surface Tests — results

Reproduce: `python research/options_surface_tests.py`

Five research lanes were proposed once the Deribit per-strike chain recorder landed. **Two are
answerable from data on disk; three are blocked.** The script prints a refusal and no number for
the blocked ones rather than producing five results of mixed provenance.

| lane | status |
|---|---|
| 1. implied vs realized movement | ANSWERABLE (hurdle form) — **undecided** |
| 2. static no-arbitrage on the surface | ANSWERABLE — **surface is coherent** |
| 3. skew / term-structure regime transitions | BLOCKED — 6.4 minutes of surface history |
| 4. options surface → Polymarket residuals | BLOCKED — archives 27 days apart |
| 5. liquidity-provision economics | BLOCKED — no queue or depth recorded |

Data as measured: chain snapshot `2026-07-31 08:01–08:07` (3 batches, 1,816 two-sided quotes,
13 expiries); BTC bars through `2026-07-27 23:59` (518,398); Polymarket books
`2026-07-02 → 2026-07-04`.

---

## Test 1 — realized movement vs quoted option cost

No pricing model is used anywhere. Cost is the **quoted ask**, exit is the **quoted bid**, and
the expiry payoff is `|S_T − K|` by definition.

### 1a — hold to expiry

| expiry | T days | IV % | ask bps | indep n | mean\|mv\| all | → vol % | mean\|mv\| 30d | → vol % | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-08-01 | 1.0 | 31.7 | 140.0 | 362 | 165.5 | 39.7 | 121.4 | 29.2 | IV **above** realized |
| 2026-08-02 | 2.0 | 25.9 | 160.0 | 180 | 230.5 | 39.1 | 189.7 | 32.2 | IV **below** realized |
| 2026-08-03 | 3.0 | 26.2 | 190.0 | 120 | 283.2 | 39.2 | thin | thin | vs full archive only |
| 2026-08-07 | 7.0 | 32.5 | 370.0 | 51 | 420.1 | 38.0 | thin | thin | vs full archive only |
| 2026-08-21 → 2027-06-25 | 21–329 | — | — | 17 → 1 | **INSUFFICIENT** | | | | |

Windows are **non-overlapping**; 30 are required before a tenor is scored, 10 for the trailing
regime check. Mean, not median — an option payoff is linear in the move and the tail is fat.
`→ vol %` inverts the mean absolute move (`E|X| = σ√(2/π)`) into an annualised vol so it sits in
the same units as the quoted IV.

**Verdict: undecided, and it must stay that way.** One implied-vol observation against a realized
distribution ending 3.3 days earlier cannot establish a premium in either direction. The
regime-matched comparison at 1 day reads IV 31.7% against 29.2% realized — a **+2.5 vol point**
premium, the ordinary sign and an ordinary size.

### 1b — intraday hold (upper bound)

Round trip on the front straddle is **20.0 bps** (ask 140.0 − bid 120.0).

| hold | n | mean \|mv\| signal | mean \|mv\| base | lift | round trip | bound − cost | clears |
|---|---:|---:|---:|---:|---:|---:|---|
| **15m** | 16,560 | 20.6 | 15.1 | **+5.6** | 20.0 | **−1.0** | **NO — dead** |
| 60m | 16,560 | 37.3 | 30.1 | +7.2 | 20.0 | +14.8 | yes |
| 240m | 16,543 | 72.4 | 61.1 | +11.3 | 20.0 | +47.9 | yes |
| 1440m | 16,522 | 154.9 | 161.2 | **−6.3** | 20.0 | +124.9 | yes |

A straddle's delta lies in `[−1, +1]`, so its value cannot gain more than the underlying's
absolute move through the delta/gamma channel. **Failing the bound kills a horizon** — no exit
rule, sizing or strike choice recovers a move smaller than the spread. **Clearing it proves only
"not dead"**: theta and the decaying losing leg are not charged, and vega is excluded entirely.

The 15-minute horizon — where `rv_term_inversion` is strongest (+5.6 bps, a 37% lift) — is
precisely the one the bound kills. By one day the lift has **inverted to −6.3**. It is a
15-minute signal and the cheapest instrument that could express it is priced for a day.

---

## Test 2 — static no-arbitrage on the surface

Same species as the complete-set arbitrage test: arithmetic fixed by the payoff, no forecast, no
free parameter. All three constraints checked on **bid/ask**, never mid.

| structure | checked | edge > 0 | edge > 2 bps | best bps |
|---|---:|---:|---:|---:|
| butterfly-call | 461 | 0 | 0 | −1.0 |
| butterfly-put | 318 | 0 | 0 | −4.0 |
| vertical-call | 728 | 0 | 0 | −1.0 |
| vertical-put | 572 | 0 | 0 | −2.0 |
| **TOTAL** | **2,079** | **0** | **0** | **−1.0** |

Put-call parity residual `|C − P − (U − K)|`: median **3.3 bps**, p90 **12.7 bps** across 536
matched pairs — consistent with ordinary discounting and forward basis. Reported as a
surface-quality diagnostic only; converting it to an arbitrage claim needs the futures book,
which this archive does not contain.

**Verdict: the surface is internally coherent.** No vertical or butterfly opens for a credit at
executable prices. Same answer the complete-set and cross-market coherence tests gave for
Polymarket.

### The guard that made this trustworthy

`underlying_price` is **not constant inside a batch** — 20 of 37 (batch, expiry) groups carry 2
distinct values and 4 carry 3, because the chain is polled over ~300 ms while the index moves.
Comparing two strikes quoted against different underlyings manufactures fake violations exactly
the way the 60-second bar-reference error produced a fake 16.42% coherence violation.

Every leg pair must therefore share an **identical** `underlying_price` (G1), and the script also
reports the result **without** the guard so a timing artifact would be visible rather than
reported as a discovery. Here both pass: 0 violations with the guard and 0 without.

---

## Tests 3–5 — blocked, with the exact gap

**3. Skew / term-structure regime transitions.** The surface archive spans **6.4 minutes** across
3 batches. Needs the recorder running for weeks; it is built and its provenance is clean
(3/3 HTTP 200, 0 dropped, `response_sha256` recorded) — it is simply not scheduled.

The snapshot does establish a term-structure **baseline**, in contango from the front with a dip
at 2–3 days:

| tenor | 1d | 2d | 3d | 7d | 14d | 28d | 91d | 329d |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ATM IV % | 31.7 | 25.9 | 26.2 | 32.5 | 33.2 | 33.9 | 37.7 | 42.8 |
| ATM spread bps | 10.0 | 6.0 | 10.0 | 10.0 | 10.0 | 15.0 | 15.0 | 35.0 |

**4. Options surface → Polymarket residuals.** The archives do not overlap by **27 days**.
Polymarket books end `2026-07-04 09:23` UTC; the chain begins `2026-07-31 08:01` UTC. Not one
shared minute. This is the strongest of the five on paper — the option surface is the deepest,
best-informed BTC vol market and Polymarket binaries are a thin retail venue pricing the same
distribution — and it is unanswerable until the clocks overlap. It also requires Polymarket
settlement joins on **actual ask, depth and executable VWAP**; mid-price residuals would
reproduce the taker-cost error that closed the previous five lanes.

**5. Liquidity-provision economics.** Nothing recorded carries **queue position**.
`orderbook.1` holds 485,105 top-of-book observations — a price, not a place in a queue — and its
last write was 38 hours before this run. Needs `BINANCE_SEQUENCED_L2_RECORDER_V1`: depth diffs
with sequence numbers, replayed into a book where a simulated resting order holds a position.
Adverse selection is then measurable as the drift **after** a fill, which is the number that
decides the lane.

---

## Two errors caught inside this script, before they became claims

**1. The round trip was double-counted.** The first pass charged `per-leg spread × 2 legs × 2
directions = 40 bps`. Buying at the ask and later selling at the bid **is** the round trip: it is
`straddle_ask − straddle_bid = 20 bps`, charged once. The negative was being reported at twice
its true strength.

**2. "Buy favoured" on 11 of 11 expiries** — rising monotonically with tenor to **+1230 bps** at
329 days. Uniform, monotone results across independent tests are a signature of systematic bias,
not of eleven discoveries. Two compounding faults:

- *No independence.* A 329-day move measured on 1-minute-spaced overlapping windows has roughly
  **one** independent observation. The day-block bootstrap could not fix it — blocking on entry
  date does nothing when every window overlaps every other. Fixed by non-overlapping windows and
  a 30-window floor, which correctly reduces 11 evaluable tenors to 4.
- *No regime match.* Comparing today's IV against a 360-day realized average imports regimes
  calmer and wilder than the one being quoted. The 1-day tenor reads "IV **below** realized" at
  39.7% full-archive and "IV **above** realized" at 29.2% trailing-30-day. Which window you pick
  determines the sign — which is exactly why the lane is reported as undecided.

---

## What this establishes

1. **The option surface is coherent** — a sixth independent confirmation that quoted books on
   these venues are priced, and the spread belongs to whoever quotes it.
2. **The magnitude signal still has no reachable instrument.** The 15-minute horizon where it
   works is killed by an upper bound that no refinement of the strategy can escape.
3. **The implied-vs-realized lane is genuinely open** — the first lane in this repository whose
   honest verdict is "the data on disk cannot close it" rather than "closed, negative". It needs
   a surface time series, not a better model.
4. **Every blocked lane is blocked on collection, not on ideas.** That is the finding that
   matters more than any single test here.

## Standing constraint

Nothing here is wired into the application, and none of it may be without passing the normal
promotion gates. Real orders remain disabled (`backend/trading_authority.py`).
