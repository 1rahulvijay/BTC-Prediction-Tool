# Scan-5 closeout and the open-defect sweep — `2026-08-08`

Closes every remaining scan-5 claim and re-checks the `OPEN_DEFECTS.md` items that had
never been read against code. Seven fixed, three doc entries found stale, one claim
corrected, and two left open with the reason stated.

```text
fixed with a registered test        5.13  5.14  5.15  5.29  5.31  5.2  P0-15  P0-9
stated mechanism was WRONG          5.2   (shifted, not shortened)
OPEN_DEFECTS entries found STALE    P0-4  P0-10  P0-14 (the blocker half)
still open, reason recorded         5.18  P0-16/17/18  P0-8B/8C  P0-27
```

---

## The A/B test was an experiment with uncontrolled conditions

`ab_testing.py` runs a challenger beside the incumbent and a promotion gate reads the
result. Three separate defects meant that result described something other than the
comparison it claimed.

### 5.13 — the challenger was conditioned on the incumbent

```python
primary_pred    = self.primary.predict(h, seq, data_state, acc_cache, cascade_data)
challenger_pred = self.challenger.predict(h, seq, data_state, acc_cache, cascade_data)
```

The **same** `cascade_data` object, and the server fills it as `cascade_data[h] = p`
*after* the full policy chain runs. The model's hierarchical cascade reads
`cascade_data[5]["direction"]` to bias its 15m probabilities — so the challenger's 15m
forecast was partly the incumbent's post-policy 5m call. A challenger cannot be evaluated
on a forecast the incumbent partly made.

`acc_cache` is the quieter half and arguably the worse one. It is the **primary
verifier's** live record, and the cascade only fires when the lower horizon has
demonstrated directional skill. The challenger was borrowing the incumbent's track record
to decide whether to trust its own call.

Each variant now sits its own skill test, from its own resolved A/B rows. A challenger
with no directional outcomes yet has no `lean_accuracy`, the gate reads absence as
unknown, and **its cascade stays inert** — the true answer, not a defect. That asymmetry
is recorded (`challenger_evidence: none_cascade_inert`) rather than papered over: a
promotion decision must know whether it compared cascade-active against cascade-inert.

The challenger's mirror expires on the server's own cycle boundary, detected by object
identity with the reference retained so a recycled `id` cannot fool it. Without that, a
cycle where the challenger's 5m call failed would leave the previous cycle's 5m
conditioning its 15m — a staleness the primary can never suffer.

### 5.14 — the evidence clock restarted with the process

`simulated_live_days` is measured from `started_at`, set to `time.time()` at
construction. `restore_from_db` restored counts and not the clock, so a
`min_live_days: 30` gate **could never be reached by any process restarted more often
than monthly.** That is the 5.21 shape again: a bar that cannot be cleared is a closed
gate, not a strict one.

The restore is now identity-scoped, and that qualifier is the whole fix:

```text
evidence belongs to      a MODEL
it was keyed by          a LABEL
```

Two of the three construction sites use fixed strings (`"baseline_v9"`,
`"challenger_cat_v1"`), so a replaced challenger reusing a label inherited its
predecessor's hit count — and restoring the clock naively would have handed it the
predecessor's *age* as well, turning a durability fix into a fail-open. The clock is
therefore restored **only from a bundle-scoped record**; an unidentifiable one earns no
calendar credit. Legacy rows written before bundle stamping are returned unscoped and
labelled `legacy_rows_unstamped`, because scoping them out would delete real evidence and
reporting them as scoped would be a lie.

### 5.15 — in-flight attribution was memory-only

`self.pending` maps `pred_id -> {label: direction}` and is what attributes an outcome to
the variant that predicted it. Every prediction open at shutdown resolved in DuckDB while
the in-memory counters the `min_verified` gate reads never saw it. Unresolved rows are now
reopened at restore, both sides.

### What the verdict now carries

`get_comparison()` returns an `evidence_integrity` block naming every parity the test does
**not** have — comparison basis, cascade isolation, whether the challenger had a record of
its own, each side's evidence scope, and where the clock came from. A promotion decision
that reads the verdict without them is claiming an experiment that was never run.

*17 checks, 5/5 mutation.* `backend/test_ab_isolation_and_durability.py`

---

## 5.31 — a win rate that answers a different question than its label

The price-to-beat mirror publishes "model X%". It grades `end >= anchor` on the anchor
feed. The model forecasts `first_touch_triple_barrier_v1`. Those are different questions —
"touches +band before −band" is correlated with but not identical to "ends above the
anchor" — and **nothing on the row said so.** Under the shipped configuration
`contract_match` is `False` for *every* round in the strip.

The grading is not what is wrong. Endpoint sign against the anchor is the venue's own rule
and the tradeable question. What was wrong is publishing it unlabelled.

The second half is the interval. `_ptb_preds` holds whatever the heavy loop last produced,
and a round is graded against it with no bound on the offset:

```text
offset  0s → horizon_overlap 1.00     usable
offset  3s → horizon_overlap 0.99     usable
offset 120s → horizon_overlap 0.60    NOT usable
```

Normally the offset is one cycle. A retrain, a throttled machine or a stall widens it
silently. Every round now carries `pred_contract`, `grading_contract`, `horizon_overlap`
and `grade_usable`, persisted through the restart that rehydrates this history, and
`accuracy()` reports the interval-covered subset beside the blended number. Rows written
before the column existed default to **not** usable: a round never measured against the
rule cannot be reported as having passed it.

---

## 5.29 — a cashflow stamped with one moment and priced at another

The exchange charges funding on the notional at `funding_time_ms`. The engine holds one
mark, the one observed at `received_at_ms`, and funding settles every 8h from a
"last settled" REST field — so the two are routinely **hours** apart.

Be plain about the size: a 3% mark error moves a ~0.01% funding rate by 3% *of that*. The
dollar impact is negligible. It is a provenance defect, and the rule it breaks is P1-1 —
one row must not silently describe two moments.

The charge is still applied, on the best mark available. Skipping a real cashflow would
flatter paper P&L, and that is the worse error. `mark_basis` and `mark_lag_ms` now say
which case each row is, so the error is measurable instead of invisible.
`OBSERVATION_TIME_MARK_ESTIMATED` is the expected label, not the exception.

The migration matters as much as the fix: the funding INSERT is **positional**, so a
database created before these columns would have rejected every funding event — and the
caller swallows exceptions, so the engine would have quietly stopped charging funding
altogether. Schema v4 → v5, with an explicit `PRAGMA table_info` check.

---

## 5.2 — CONFIRMED-ADJUSTED. Shifted, not shortened

The claim was that the live first-touch interval is *shorter* than the declared horizon.
Measured on a 5m horizon over 1-minute bars selected as
`entry_ts < open_ms <= verify_ts`:

```text
entry +0s   observed [ 60s.. 360s]  declared [ 0s.. 300s]  shift 60s
entry +20s  observed [ 60s.. 360s]  declared [20s.. 320s]  shift 40s
entry +40s  observed [ 60s.. 360s]  declared [40s.. 340s]  shift 20s
entry +59s  observed [ 60s.. 360s]  declared [59s.. 359s]  shift  1s
```

Always five bars. The length is exactly right; the window is **shifted forward by up to
60 seconds.** Both ends cost something and they do not cancel:

- **head** — a barrier touched between the entry and the first bar's open is invisible.
- **tail** — a touch after the horizon ended is attributed to a position that had already
  closed. This is the half that matters, because it makes the label easier to hit than the
  tradeable reality.

**Tightening the selection would be a regression.** Requiring bars fully inside the
horizon drops to four bars and grades a five-minute contract over four minutes — a larger
error than the one being fixed, and the same trap that reverted the P0-4 attempts twice.
The structural remedy is bar-aligned entry timestamps, which is a change to *when
predictions are issued*, not to the grader. Until then every graded row carries
`observed_start_ms`, `observed_end_ms` and `window_shift_ms`, so no consumer has to assume
the observed interval was the declared one. On an irregular bar list the cadence is not
guessed and the shift is reported as unknown.

That makes **five** claims across all scans whose stated remedy or mechanism was wrong:
5.1, 5.21, 2.20's `groups` half, 4.16's, and now 5.2's.

---

## The open-defect sweep

### Three entries in `OPEN_DEFECTS.md` were stale

| # | listed as | actually |
|---|---|---|
| P0-4 | "VERIFIED OPEN, fix attempted and REVERTED" | **FIXED.** `as_of_close` returns the recorded close via `kline_schema`; selection deliberately unchanged |
| P0-10 | CLAIMED, never checked | **FIXED.** `causal_neutral_band` evaluates the training threshold series, replacing the instantaneous recomputation |
| P0-14 | "blocker is deeper: no `target_contract` column" | **that blocker is gone.** `target_contract`, `release_id`, `resolution_basis` all exist and `log_prediction` requires them |

A stale defect register is its own defect: it directs work at problems that are solved and
implies coverage that does not exist.

### P0-15 — CONFIRMED and fixed

`_recent_conf` is a module-level deque rebuilt empty at import. The percentile gate returns
`None` below 20 samples, so after every restart the learned confidence bar ran unbounded
until the window refilled — a condition the code already documents at the threshold
ceiling ("right after a restart (<20 samples) the percentile cap above is None while the
learned policy is allowed up to 0.76").

It is now rehydrated at boot, under two qualifiers that are the difference between
restoring evidence and manufacturing it:

- **namespace** — the effective confidence is `calibrated_confidence` where one exists and
  the raw score otherwise. Loading raw scores into a window that a calibrated bar is
  compared against is **defect 5.21 in a new place**.
- **release** — a percentile is a claim about one model's distribution. Mixing releases is
  **defect 5.10 with a different denominator**.

An unknown release restores nothing. The gate already handles an empty window; a window of
unattributable rows is worse than none.

### P0-9 — CONFIRMED-ADJUSTED, and smaller than stated

Direction and magnitude **cannot** contradict each other in the served object:
`target_price` is derived from `direction`, and `exp_move` is made unsigned by `abs(frac)`
on all three paths (regressor, conformal, empirical prior). The "incoherence" the audit
describes does not reach the output.

What that `abs()` discards is real, though: the magnitude head's own **sign**. A regressor
saying −0.4% while the classifier says UP produces a target above spot and leaves no
trace. The two heads are separately trained on the same rows, so their disagreeing is
information about the row. `magnitudeSignAgrees` now records it — three-valued, `None`
when the head did not run, because an unmeasured agreement must not be published as
agreement. Recorded, not acted on: acting on an unmeasured signal is how the fail-open
gates in this scan were built.

### Still open, with reasons

| # | why it is not fixed here |
|---|---|
| 5.18 | promotion supplies a hard regime label where production blends a posterior. Real, and it is the same replay work as 4.5 / 2.9 — not a local edit |
| P0-16 / P0-17 / P0-18 | one job: immutable `releases/<id>/`, atomic pointer swap, manifest-declared loading. Already tracked as architecture (2.14 / 3.14) |
| P0-8B / P0-8C | a contract-correct RandomForest backtest still evaluates a RandomForest. Needs `BacktestSpec.model_kind`, and the surrogate path must print that it does not evaluate the served ensemble |
| P0-27 | unauthenticated read routes. Acceptable on localhost, and a deployment decision rather than a code defect today |
| P0-19 / P0-20 / P0-24 | read but not resolved. P0-24 is the real one: `ALTER TABLE … except: pass` appears 13 times, so a migration that fails for a genuine reason is indistinguishable from one that was already applied. The 5.29 fix uses `PRAGMA table_info` instead, which is the pattern the rest should adopt |

---

## Tests

```text
backend/test_ab_isolation_and_durability.py            17 checks   5/5 mutation
backend/test_grade_provenance_and_funding_moment.py    17 checks   5/5 mutation
backend/test_observed_window_and_restored_state.py     16 checks   7/7 mutation
```

All three registered in both CI jobs. Two mutants survived the first pass on the third
file and both were the same error — the test measuring itself rather than the code. One
asserted on the whole function body where a `WHERE` clause repeated the expression the
`SELECT` was supposed to carry; the other reimplemented the arithmetic it was checking. The
second now compiles the shipped expression out of the AST and evaluates that.
