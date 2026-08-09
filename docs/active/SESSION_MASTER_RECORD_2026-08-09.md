# Master record — audit and lane sweep — `2026-08-08` → `2026-08-09`

One document for the whole session. Twenty-six commits, six audit scans, nine research studies,
one new recorder. Everything below is measured, and every claim has a script that reproduces it.

**Post-audit remediation:** see
`CORRECTNESS_EVIDENCE_REMEDIATION_2026-08-09.md` for the later regime-snapshot repair,
grouped settlement uncertainty, non-promotable replay contract, exact-policy Binance paper gate,
market-specific Polymarket TWAP truth quarantine, and recorder-health visibility.

**The two things worth knowing first:**

1. **The app has served no prediction since 2026-07-04.** The saved bundle is refused for
   having no identity manifest. The refusal is correct; only a retrain fixes it honestly.
2. **Seven alpha lanes were measured and all seven closed.** Five closed on *execution
   economics*, two on the *absence of information*. On this venue at this size, the constraint
   has not once been the model.

---

## Part 1 — the state of the application

```text
main ensemble          UNAVAILABLE   no_identity_manifest:provenance_unprovable
last served prediction 2026-07-04 10:44
last round recorded    2026-08-08 08:10   (the fast ticker kept running)
check_feature_contract FAILING        12/12 artifacts UNKNOWN, VWAP semantics changed
real-money authority   NONE           and nothing here changes that
local CI               181 steps, 1 documented failure (check_feature_contract)
                       (last measured run; the workflow file lists 186 `- name:` entries,
                        which is not the same number the runner executes)
```

Every model-side table stops at the same instant while `price_to_beat` ran 35 more days. Of the
9 rounds recorded since, **zero** carry a directional lean.

**Two ways out, and they are not equivalent.** Retraining is the only honest one — regenerating
a manifest for the existing bundle would attest provenance for feature semantics already known
to be stale, which is signing a false claim.

---

## Part 2 — what was fixed

Six scans, ~35 confirmed defects. Every fix carries a mutation-tested regression test
registered in CI. The scans that produced them:

```text
scan 5 investigation    24 claims read against source, 19 confirmed
scan 5 fixes            barrier-as-price, four fail-open gates, horizon scoping
core app scan           grading band, verdict blockers
lean_hit / expectancy   contract truth in the betting columns
scan 6                  34 claims, 7 confirmed, 1 refuted as stated
open-defect sweep       P1-C verified, three register entries found stale
```

### The defects that mattered most

**`lean_hit` was measuring a mixture of two contracts.** The column the schema names as *the*
betting metric compared the lean against the sign of `actual_move_usd` — the barrier on
touching rows, the closing residual on timeouts. NEUTRAL is 46.6% of 5m rows, so on a
zero-skill model the old rule reported **0.501** where the contract says **0.268**.

**"Historical EV" was accuracy wearing a dollar sign.** `expectancy_usd` averaged
`|actual_move_usd|`, which under first touch is `entry × threshold` — identical on every
touching row. The statistic collapsed to `barrier × (2·accuracy − 1)`. A 70%-accurate model
reported **+$32** while actually losing **$5.40** per trade.

**Replay handed a fraction-taking grader a dollar amount.** `threshold = entry_price × band`
made the barriers `[−7,900,000 .. 8,100,000]`, so **every replay row timed out to NEUTRAL**
while the status still read `GRADED_FIRST_TOUCH`. The server turns replay output into threshold
recommendations.

**A label documented as "not a live gate" was the only gate.** `_confluence` returns A/B/C
unconditionally, `grade_unproven` fires unless `grade_validated` is set, and nothing in
production sets it — so `trade_verdict` could **never** be `TRADE`, which is `model_consensus`'s
entry condition.

**Seat votes had two graders.** `update_outcome`'s docstring names the endpoint-vs-first-touch
defect and then commits it on the next table, competing with `model_verifier`'s contract grader
on the same column.

**A declared zero band was widened to 8bps.** `float(x or 0.0008)` — and `BTC_LABEL_COST_FLOOR=0`
makes zero reachable, so a zero-cost study would train at 0.0 and be graded at 8bps.

### Three fixes that were themselves defective

Caught by scan 6, all committed the same morning as the fix they broke:

- **The control-relative promotion gate** wrote its criterion into the wrong dict and
  fail-closed on keys the gate does not have — recorded, never enforced. *The exact defect the
  function was written to remove.*
- **Paper risk limits** traded "absolute dollars that don't scale down" for "fractions of a
  constant that don't scale up" — a 0.25% position cap at a $10k bankroll.
- **The executed-identity flag** compared a NumPy tensor digest against a Parquet file digest.
  Structurally always `False`, so enforcing it — the obvious next step — would have rejected
  every honest retrain forever.

### Six stated remedies whose direction was wrong

Refused rather than implemented, each with the measurement that refuted it. The most striking:
`BTC_DIR_MARGIN`'s own comment says *"set it to neutralize the tilt"*. Measured, the tilt is a
whole-distribution shift (+5.1pp), so widening the dead zone makes the skew **worse**:

```text
margin 0.000 -> UP share 69.5%      margin 0.050 -> 74.8%
margin 0.015 -> UP share 71.1%      margin 0.100 -> 76.8%
```

---

## Part 3 — the lane sweep

Nine studies. Each is standalone, read-only, registered in `run_all_sequence.py`, and exits
non-zero only on a data problem — never on an unfavourable finding.

```text
lane                        verdict   what closed it                        ledger
served directional lean     CLOSED    coin flip on the venue's question       §15
taker on fair value         CLOSED    ask beats Φ(z): Brier .167 vs .181      §16
cross-venue repricing lag   CLOSED    corr(past move, next quote) = 0.0016    §17
state selectivity           CLOSED    0 of 15 cells survive a bootstrap       §18
early exit (taker)          CLOSED    20 of 20 rules worse than holding       §19
maker at the touch          CLOSED    6.46% fill, −2.15c markout, deepening   §20
Binance first-touch         CLOSED    −12 bps = exactly cost, every pair      §21
conditional entry           CLOSED    best of 285 cells is a reliable loss    §22
fast BTC tick recorder      BUILT     74ms cadence; unblocks sub-second       §23
```

### The pattern

**Five lanes closed on execution economics, not on prediction quality:**

```text
the taker fee curve         1.75c/share at 50c, peaking exactly where these markets trade
the spread crossed twice    entering at the ask and exiting at the bid opens 1c underwater
the queue                   87.5% of books at a 1c spread on a 1c tick - no price improvement,
                            median 186 shares ahead against ~18 available sells per round
adverse selection           half-spread +0.77c against −2.93c, deepening with horizon
```

**Two closed on the absence of information:** the barrier geometry is a martingale
(`EV = 0` exactly, before costs, for every pair), and no observable state in the matrix changes
that.

### Traps found inside the studies themselves

Each of these would have produced a confident, wrong finding:

- **The DOWN column** in the profitability surface looks systematically better. It is the
  sample's base rate — a blanket state-blind DOWN buyer books +2.22c, and the imbalance is 1.2
  standard errors from even. Eleven of fifteen "positive" cells are worse than not selecting.
- **The +25c early-exit budget** looked like a green light. Measuring the *reachable* part
  closed the lane in one script; the ceiling was never the constraint, the spread was.
- **The first-touch p-column** deviates from the martingale by up to 17 points. It is a
  time-cap artifact — 82–89% of windows expire, so `observed p` is conditioned on the decided
  minority.
- **The conditional-entry verdict** printed *"a CANDIDATE for a pre-registered forward test"*
  for a cell losing 11.74 bps at p=0.043. A p<0.05 on a loss says the loss is *reliable*.
- **The unabsorbed fair value** (3.91c) exceeds the round-trip cost (2.57c) and is not an
  opportunity — it is the size of the model's disagreement with the price, and trading it loses.

---

## Part 4 — the recurring defect class

One shape accounts for most of what was found, in code and in studies alike:

> **A check that passes while the property it guarantees is false.**

It appeared as: a criterion recorded but never enforced; a margin applied at one site and
bypassed at the next; a comment guaranteeing a property the code does not have; a flag that is
structurally constant; a detector firing on a property it does not measure; a fixture agreeing
with the bug so both are invisible; and a verdict branching on significance without checking
profitability.

**The methodological rules that kept catching it**, worth carrying forward:

```text
verify from source        every claim read before acting; 1 of 34 in scan 6 was wrong as stated
mutation-test the test    a test that survives reverting the fix guards nothing
extract code by AST       the fix's own comment always quotes the line it replaced
fixtures must be real     {"passes": True} was a gate shape the code has never returned
name the competitor       the ask, the martingale, the base rate - never 50%
price the search          max-statistic permutation, because searching guarantees winners
cluster the bootstrap     by round or day; snapshots inside a round share one outcome
absent ≠ zero             unmeasurable EV is not negative EV
```

---

## Part 5 — what is genuinely open

**Blocking anything else:** the retrain. `check_feature_contract` reports 12/12 artifacts
UNKNOWN and the VWAP formula changed from cumulative to trailing time-anchored, so any
v1-trained model is fed a materially different column.

**Untested, in rough order of cost:**

```text
sub-second cross-venue    recorder BUILT; needs simultaneous capture with l2_recorder.py on
                          one host - pm_l2's window (07-02..07-04) does not overlap
posting deeper than touch better price, worse fill odds; unlikely at a 6.46% touch rate
two-sided maker quoting   with inventory management; only one leg was simulated
feature families absent   depth imbalance, options surface, liquidation cascades - the 19
                          columns tested are what exists, not what is possible
horizons beyond 5m/15m    everything measured is at the horizons the app serves
```

**Architecture tracked and not started:** atomic release-directory pointer swapping (2.14/3.14),
`DecisionEnvelope` threading (3.15), one action vocabulary and one risk engine (3.11/3.12),
and forecast-vs-agreement populations (4.9). Regime expert accuracy is now isolated by horizon,
and recurring server work uses monotonic deadlines instead of call-count timebases.

**Scan 6 residue (closed in the 2026-08-09 follow-up):** D4 (release-scoped adaptation), D7
(HMM fail-open), D8/D9 (paper close/reversal), D10 (ForwardEvidenceGate wiring) and D13
(meta target) are now fixed. The meta label is explicitly counterfactual endpoint economics,
not realized fill P/L. Remaining uninvestigated claims retain `CLAIMED` status.

---

## Part 6 — the honest summary

Nothing in this repository is evidence of profitability, and the sweep strengthened that rather
than weakening it. Seven independent questions, seven negative answers, each recorded with the
script that produced it so none gets re-proposed.

That is not a failed session. A measured negative closes a lane permanently and cheaply; an
unmeasured hope consumes engineering indefinitely. The taker lane, the lag lane, the
selectivity architecture, early exit, the maker lane and the barrier geometry are all now
answered questions rather than open plans.

**The one thing the evidence points at, and it is a small job:** run the two recorders together
for a week. That makes the sub-second question answerable, and it is the only lane where the
data — not the theory — is what has been missing.

---

### Index

```text
docs/active/SCAN5_INVESTIGATION_2026-08-08.md          scan 5, 24 claims read
docs/active/SCAN6_VALIDATION_2026-08-08.md             scan 6, 7 confirmed + 1 refuted
docs/active/CORE_APP_SCAN_2026-08-08B.md               band, verdict, lean_hit, expectancy
docs/active/DIRECTIONAL_TILT_2026-08-08.md             the tilt, and the refuted remedy
docs/active/OPEN_DEFECTS.md                            the serving outage, P0 ledger
docs/active/LIVE_ROUND_EDGE_AUDIT_2026-08-08.md        §15
docs/active/POLY_FAIR_VALUE_VS_ASK_2026-08-08.md       §16
docs/active/CROSS_VENUE_REPRICING_LAG_2026-08-08.md    §17
docs/active/PROFITABILITY_SURFACE_2026-08-08.md        §18
docs/active/EARLY_EXIT_LANE_2026-08-08.md              §19
docs/active/MAKER_LANE_2026-08-08.md                   §20
docs/active/BINANCE_FIRST_TOUCH_LANE_2026-08-08.md     §21
docs/active/CONDITIONAL_ENTRY_2026-08-08.md            §22
docs/active/BTC_TICK_RECORDER_2026-08-09.md            §23
docs/active/CORRECTNESS_AND_MONEY_PATH_FIXES_2026-08-09.md  pre-1000d correctness closeout
docs/RESEARCH_LEDGER.md                                §15–§23
```
