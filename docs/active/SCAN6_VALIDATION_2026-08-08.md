# Scan 6 — validation and fixes — `2026-08-08`

A fresh scan against `0402209` raised 34 core defects plus 4 in the shadow lane. This records
what was checked against source, what was fixed, and what was refuted.

**The headline finding of this pass is uncomfortable and worth stating first: three of the
seven confirmed defects were introduced by fixes committed earlier the same day.** A fix that
records a criterion without acting on it, a fix that trades one unscaled limit for another,
and a fix whose own comparison can never be true. Each was written to remove exactly the
defect shape it then committed.

```text
CONFIRMED and FIXED     7   D1, D2, D3, D5, D6, D12, D31, D34
REFUTED as stated       1   D1's stated location (real defect, different line)
NOT YET INVESTIGATED   26
```

---

## D1 — replay handed a fraction-taking grader a dollar amount. **CONFIRMED, severe.**

Not at the call site the scan named — `neutral_band` arrives there correctly as a fraction.
It is inside `_graded_direction`:

```python
threshold = entry_price * float(neutral_band or 0.0)
```

`first_touch_at` builds `entry * (1 ± threshold)`. Measured at a $100,000 entry and an 8bps
band:

```text
correct (fraction 0.0008)   barriers [    99,920 ..     100,080]
this bug (threshold 80)     barriers [-7,900,000 ..   8,100,000]
```

Neither barrier is reachable, so **every replay row timed out to NEUTRAL** — while the status
still read `GRADED_FIRST_TOUCH`. Proven on a path that genuinely touches the upper barrier: it
grades `UP` under the fraction and `NEUTRAL` under the product.

The server turns replay output into threshold recommendations. Those recommendations were
computed against a constant.

This sits inside the function whose own docstring describes repairing the endpoint-vs-first-touch
substitution. The contract dispatch was fixed and a units bug was introduced in the same edit,
and the units bug silently undid the repair.

## D2 — replay's headline hit was still the endpoint sign. **CONFIRMED.**

`actual_dir` was graded through the contract; `direction_hit` two lines later was
`(raw_dir == "UP") == (actual_move > 0)`. Two truths in one row. Now `raw_dir == actual_dir`.

## D5 + D6 — the executed-training identity. **BOTH CONFIRMED, and they interact.**

D5: `artifact_compatibility` enforces 13 identity keys and not one `executed_*` field. The
manifest could record that the executed data differed from the research matrix and still pass.

D6: the flag D5 would enforce compares `executed_feature_matrix_sha256` — a sha256 over
in-memory NumPy bytes — against `matrix_hash`, a Parquet **file** digest. Measured: logically
identical data hashes differently. The flag was structurally `False` for every run.

**Fixing D5 alone would have rejected every honest retrain forever.** They had to be fixed
together:

- D6: the flag is now `None` (not comparable) with `executed_matrix_comparison_basis`
  recording why, and `executed_rows_match_matrix_rows` publishing the comparison that *can*
  be made.
- D5: what is enforced is the **recording**, not the impossible hash. Under strict identity a
  bundle that cannot say what it was fitted on is refused. Verified that `train()` does record
  it, so real retrains are unaffected — checked before keeping the enforcement, precisely
  because bricking every retrain was the failure mode on offer.

A real agreement check needs one canonical logical-row hash computed the same way on both
sides. That is not written, and the flag now says so instead of implying an answer.

## D34 — the control-relative gate recorded a criterion it never enforced. **CONFIRMED.**

Two bugs in the fix committed this morning:

```python
criteria = gate.get("criteria") if isinstance(gate.get("criteria"), dict) else gate
...
for key in ("passes", "eligible", "promotable", "ready"):
```

There is no `"criteria"` key, so the criterion landed **beside** `status` and `checks` rather
than inside `checks`. And `_promotion_gate` returns none of those four keys — its verdict
field is `status` — so the fail-close was a no-op. A strategy losing to a zero-information
control still read `FORWARD_GATE_PASSED_PAPER_ONLY`.

Now the criterion goes into `checks` and the verdict is **recomputed** through
`_status_from_checks`, extracted so the rule lives in one place rather than being
reimplemented beside itself. Measured: loser → `BLOCKED_FAILED_GATE`, winner unaffected,
missing control → `BLOCKED_FAILED_GATE` with `CONTROL_UNAVAILABLE`.

## D31 — paper risk limits were fractions of a constant. **CONFIRMED.**

Also from this morning. The absolute-dollar defaults were replaced with fractions of
`DEFAULT_STARTING_CASH_USD`, which is a constant, so at a configurable $10,000 bankroll:

```text
max_position_notional  $25.00   0.25% of the account
maximum_daily_loss     $12.50   0.12% of the account
```

The original defect was "absolute dollars that do not scale down". The fix produced "fractions
of 250 that do not scale up" — the same defect pointing the other way.
`StrategyRiskConfig.for_capital(starting_cash_usd)` now derives them, bound in
`persist_defaults` where the real bankroll is known, preserving each strategy's deliberate
non-capital settings.

## D12 — the cascade's own feedback was graded by the move sign. **CONFIRMED.**

`lean_correct = (raw_dir == "UP") == (actual_move_usd > 0)` feeds
`cascade_monitor.record_outcome`, which auto-enables the cascade and scales its strength. The
comment above it reasons carefully about why `hit` must not be used (dual-semantic on gated
rows) and then reaches for the move sign — the barrier on touching rows, the closing residual
on the ~47% that time out — with `actual_direction` sitting on the same row. Now uses it.

## D3 — the revision ledger grades by endpoint markout. **CONFIRMED, with a distinction.**

The scan treats all markout rows alike. They are not: a first-touch barrier contract is **not
defined** over a 1000ms offset, so the sub-horizon markouts are correct as endpoint
measurements and must stay that way. Only the `HORIZON_{h}M` row answers the model's own
question, and grading *that* by endpoint sign is wrong.

Grading it correctly needs the intrabar path, which this resolver does not have — it sees one
price. So every row now declares `outcome_contract` and `answers_training_contract: False`,
and the HORIZON row carries an explicit note that it must not be read as directional accuracy
for a first-touch model. Same treatment as the price-to-beat mirror: do not change a
measurement that is right for its own question; label it.

---

## Two test fixtures that agreed with the bug

`test_scan5_barrier_and_gates` supplied `{"passes": True}` as a promotion gate — **a shape
`_promotion_gate` has never returned**. The code looked for `passes`, the fixture provided
it, and the two agreed on a fiction that made the real `status` field invisible. It now uses
the real shape.

`test_executed_training_identity` asserted `executed_matches_matrix is False` and described it
as evidence about the arrays. It was a tautology: False was structural. It now pins
not-comparable plus the reason.

Both are the same lesson as the code defects: **a check that passes while the property it
guarantees is false.** A fixture that is not the thing it stands in for is one of those.

---

## Still open from this scan

26 claims not yet investigated, including D4 (release-scoped adaptation), D7 (HMM label
fail-open), D8/D9 (paper close/reversal lifecycle), D10 (ForwardEvidenceGate wiring), D11
(cascade causal design), D13/D14 (meta-model target and `expectedEdge`), D15–D30, D32, D33,
and the four shadow-lane items.

D4, D8, D9 and D13 look like the highest value on the scan's own reasoning and are the next
batch. None of them is fixed, and none should be described as fixed.

**On retraining:** the scan recommends fixing everything first. D1 alone justifies that
ordering — replay's threshold recommendations were computed against a collapsed grader, and
anything tuned from them is unusable. D5/D6/D34/D31 all touch how a retrain would be judged.
The retrain should follow this work, not precede it.
