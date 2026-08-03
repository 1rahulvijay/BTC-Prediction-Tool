# Exit timing on open positions — result

**Protocol** `PREREG_EXIT_TIMING_V1.md` sha256 `09ee0bf3…`, frozen before any result ·
**Script** `research/exit_timing_v1.py` · Scored **once**

```
601 test positions, random entries (seed 71), max hold 240 bars
cost 14 bps, identical in every arm    training rows 331,200
"exit now beats holding" base rate in training: 50.2%
```

## Result

| arm | net bps | day-block 95% CI |
|---|---:|---|
| CANDIDATE (learned) | −13.31 | [−14.62, −12.25] |
| **HOLD_TO_HORIZON** | **−7.25** | [−13.92, −0.63] |
| RANDOM_EXIT | −13.00 | [−18.70, −8.00] |
| TRAILING_STOP | −13.20 | [−17.30, −9.20] |
| ORACLE_BEST_EXIT | **+45.26** | [+39.41, +51.04] — *ceiling, requires hindsight* |

```
candidate - hold          -6.06 bps   CI [-12.91, +0.27]
candidate - random exit   -0.31 bps   CI [ -5.46,  +5.00]
ceiling                  +52.51 bps above holding; candidate captures -11.5% of it
```

**VERDICT: `EXIT_TIMING_ADDS_NOTHING`.** The pre-declared `EXIT_CEILING_UNREACHABLE` condition
also holds: the oracle is large and no non-hindsight arm captures a distinguishable share of it.

## The ceiling is real and nothing reaches it

`ORACLE_BEST_EXIT` sits **+52.51 bps above holding**. That confirms the earlier finding —
exit-timing ceilings on these positions are genuinely large, not an artifact.

And every arm that cannot see the future clusters at roughly −13 bps: the learned policy
(−13.31), a random exit (−13.00), a trailing stop (−13.20). They are indistinguishable from each
other and all of them are **worse than simply holding** (−7.25).

The candidate captures **−11.5%** of the ceiling. Not a small positive fraction — a negative one.
Acting on the model destroyed value relative to doing nothing.

## The training labels were already a coin flip

"Exit now beats holding" had a **50.2%** base rate across 331,200 training rows. The signal the
model was asked to learn was, in the training data itself, indistinguishable from a fair coin.
That it then performed like a random exit out of sample is consistent rather than surprising.

## Why the ceiling is unreachable

The oracle picks the maximum of each position's path. Over 240 bars of a near-martingale, the
running maximum of a random walk is large by construction — the expected maximum grows with
√horizon whether or not the path is predictable. Most of that +52.51 bps is the *statistics of
maxima*, not recoverable information.

This is the general shape of every hindsight ceiling: it measures how much a path wandered, not
how much of the wandering was foreseeable. Sizing an opportunity from a ceiling and assuming a
model can capture a share of it is the error this arm was built to expose.

## Power, stated plainly

601 test positions is modest, and it shows: `HOLD_TO_HORIZON` has a wide interval
[−13.92, −0.63], and `candidate − hold` at [−12.91, +0.27] only just includes zero.

So the strict claim is "does not beat holding", not "is definitively worse". What is **not**
ambiguous is `candidate − random exit` at −0.31 with CI [−5.46, +5.00]: the learned policy and a
coin land in the same place, and the pre-declared kill rule fires on exactly that.

## What was controlled

- **Entries are random by protocol.** Direction was measured at AUC 0.498 in
  `CONDITIONAL_DIRECTION_V1`, so model-chosen entries would have confounded exit skill with
  entry skill that is already known to be absent. Absolute levels are therefore negative by the
  cost and are not the result.
- **Cost is identical in every arm** — each enters and exits exactly once — so the comparison is
  purely about *where* the exit lands.
- **`RANDOM_EXIT` was matched on count** and pre-declared as the deciding control. Shortening
  average hold time changes the risk profile and can move the mean without any timing skill;
  this is what separates the two.
- **The oracle is asserted to dominate** every other arm in the selftest, so a ceiling that was
  accidentally beatable would be caught as a bug rather than reported as a finding.
- Selftest 20 checks, including that MFE is non-decreasing, MAE non-increasing, and that a
  monotonically rising position never labels "exit now beats holding".

## Where this leaves the research programme

```
Regime labeler          separable, 84% volatility        -> not a router basis
Strategy Router         NOT BUILT
Tradability head        ADDS (+2.67 pts over rv_60m)     -> real, not monetisable alone
Conditional direction   NOT PREDICTABLE (AUC 0.498)      -> gate has nothing to gate
Exit timing             ADDS NOTHING; ceiling unreachable-> last large ceiling closed
```

This was the final lane with a large measured ceiling. Every route tested on this archive is now
closed:

- entry direction — unpredictable
- movement — predictable, and worthless without direction
- regime — mostly volatility
- exit timing — ceiling is the statistics of maxima, not information

One genuine measurement survives the programme: the **tradability head**, +2.67 points over the
volatility incumbent, out of sample, against the right baseline. It has no standalone use.

What remains untested is not another model over this data. It is **different information** —
sequenced L2 and book depth, absent from this archive — and **forward evidence**, which is the
Phase 6 collection work already underway. Both were the conclusion before this sequence of tests
began, and four negative results have not changed it.
