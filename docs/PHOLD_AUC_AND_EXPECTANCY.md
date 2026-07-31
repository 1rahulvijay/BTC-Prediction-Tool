# P(hold) — AUC and net-of-cost expectancy on the live deployed head

Reproduce: `python research/phold_auc_and_expectancy.py`

Prompted by a fair challenge: *the heads had higher AUC, so why are real orders disabled?*
The premise is correct. This measures what it buys.

---

## 1. The ranking is real, and it got better

| | |
|---|---:|
| population (head-health protocol, one row per round) | **6,725** |
| **live AUC** | **0.7762 ± 0.0148** |
| June held-out test AUC (contract v11 / VWAP v1) | 0.746 |
| ECE | **0.0678** (gate 0.05) |
| leader actually held | 89.34% |
| mean predicted `p_hold` | **96.06%** |

**The head ranks, and it ranks better live than it did in the June backtest.** Any suspicion
that the v11→v14 contract change destroyed the ranking is wrong — that is settled here and is
worth knowing on its own.

But look at the last two rows. It predicts **96.06%** on an event that happens **89.34%** of the
time: overconfident by **6.7 points**, which is what `ECE 0.0678` means and why
`head_permissions` demoted it to `CALIBRATION_ONLY` — *"ranks, does not price."*

AUC is invariant to calibration. It only asks whether the winners are ordered above the losers.
Sizing a bet asks a different question: is 96% *right*? It isn't.

---

## 2. What the ranking is worth at the price you would have paid

Buy the leading side at the recorded ask, hold to settlement. `n = 3,282` rounds over 21 days
carrying a `LATE_LEADER_30S_V1` quote. Per $1 of notional, net of the recorded fee.

| p_hold bucket | n | held % | mean ask | net/$1 | day LCB | verdict |
|---|---:|---:|---:|---:|---:|---|
| **ALL (baseline)** | 3282 | 89.2% | 0.8810 | +0.0047 | **−0.0028** | NEGATIVE |
| 0.00–0.90 | 577 | 73.8% | 0.7502 | −0.0238 | −0.0445 | fails G4 |
| 0.90–0.95 | 212 | 87.3% | 0.8363 | +0.0279 | −0.0004 | fails G4 |
| 0.95–0.97 | 181 | 88.4% | 0.8652 | +0.0116 | −0.0391 | fails G4 |
| **0.97–0.99** | 240 | 90.8% | 0.8640 | **+0.0371** | **+0.0069** | **PASS** |
| 0.99–1.01 | 2072 | 93.5% | 0.9253 | +0.0059 | −0.0005 | fails G4 |

**The mean ask is 0.8810 and the leader holds 89.2%.** The price already contains the ranking,
to within a fraction of a cent. Always trading the leader has a *negative* lower bound.

### The one survivor, and why it isn't one

`p_hold 0.97–0.99` passed the gate declared before results were seen. That pass is recorded as
stated. Three checks follow — the multiplicity the protocol flagged in prose but never
quantified, plus two robustness diagnostics:

| check | result | |
|---|---:|---|
| Bonferroni 1% LCB (5 buckets examined) | **−0.0053** | crosses zero |
| top 3 of 21 days as % of profit | **56%** | concentrated |
| expectancy monotone in `p_hold` | **NO** | |

```
0.00-0.90: -0.0238   0.90-0.95: +0.0279   0.95-0.97: +0.0116
0.97-0.99: +0.0371   0.99-1.01: +0.0059
```

The last line is the most damning. If the ranking translated into money, expectancy would **rise
with the score**. It zigzags, and the largest bucket by far — `0.99+`, n=2072, the bulk of all
trading — pays **+0.0059**, essentially nothing. The money is not where the model is most
confident. A single spiking bucket between flat neighbours, concentrated in 3 of 21 days, failing
Bonferroni, is the shape of noise.

---

## 3. A measurement error caught mid-run

The first execution printed `nan` for every day-block lower bound and marked every bucket
`fails G4` — a clean-looking negative that was actually a broken measurement.

`ts` is `BIGINT` **milliseconds**; the code divided by `86_400_000_000_000` (nanoseconds per
day), collapsing all 21 days into a single block, so the bootstrap silently refused to run.

Fixed to `// 86_400_000`, and the script now prints `NOT MEASURED (too few days)` rather than
`fails G4` when the bound is not finite. **An unmeasured bucket must never be reported as a
failed one** — that manufactures negative results as surely as the reverse manufactures positive
ones. Had this gone unnoticed, this document would have claimed a cleaner negative than the data
supports.

---

## 4. The answer to the question

**Why are real orders disabled when the head has high AUC?**

1. **The high-AUC head is not a direction model.** Raw direction cleared 0 of 7 horizons at
   AUC 0.50–0.52 and was never saved. Every trade needs a side.
2. **AUC 0.776 on a 89%-base-rate event is ranking, not pricing.** The head is overconfident by
   6.7 points — larger than any edge it could be claiming.
3. **The price already contains the ranking.** Mean ask 0.8810 against a 89.2% hold rate. Always
   trading the leader has a negative lower bound; no `p_hold` bucket survives correction.
4. **Independent of all of that**: no real order adapter exists, 17 production prerequisites
   fail, `trading_authority` requires an acceptance phrase, control token and per-venue grant,
   and the standing constraint forbids funded credentials in testing.

Points 1–3 are about the model. Point 4 means real orders could not execute even if 1–3 were
solved.

## 5. What this does not answer

Whether a head **retrained under the current v14 contract** would price better. The served
artifact was trained under v11 / VWAP v1 and cannot be scored on v14 features — 69 pruned vs 63,
hash `7977e0559560` vs `864622d65e85`. That needs the retrain, not a measurement.

The evidence here suggests the retrain would improve calibration rather than create an edge: the
ranking is already strong and already priced in. But that is a hypothesis, and this document
does not settle it.

## 6. Standing constraints

Nothing here is wired. Real orders remain **DISABLED**; **PAPER / SHADOW ONLY**.
