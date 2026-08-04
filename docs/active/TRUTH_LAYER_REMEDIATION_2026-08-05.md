# Truth-layer remediation — P0-6, P1-1, P1-3, P1-4, P1-6, P1-7, P1-9, P1-12

**`2026-08-05`.** Commits `f2a29b9`, `752603a`, `b9d768f`, `50e1cfa`.

The audit's verdict was that the execution layer had become safe while the **truth-measurement
layer** had not: the system had no single answer to *"what did the model predict, at what
timestamp, and what future event decided whether it was right?"* These eight items are that
question, answered one defect at a time.

Every item below was **verified against the source before being changed** and
**mutation-tested after**. Two audit claims were wrong and are corrected here rather than
implemented as written.

---

## The single pattern

Seven of the eight are the same shape:

> **A number was paired with a different moment, a different model, or a different dataset than
> the one it claimed.**

| item | the claim | what was actually used |
|---|---|---|
| P0-6 | "state at the decision instant" | live global, re-read after inference |
| P1-1 | "the move that produced this direction" | the main loop's price, seconds later |
| P1-3 | "per-model accuracy" | endpoint sign at loop time, vs first-touch training |
| P1-4 | "the ensemble's training set" | one seat trained on a different one |
| P1-6 | "conformal interval" | residuals on the model's own training rows |
| P1-7 | "holdout accuracy" | undefined outcomes counted as NEUTRAL |
| P1-9 | "this model's outcomes" | anything after a file's mtime |
| P1-12 | "an independent venue" | Binance, copied |

None of these raise. All of them produce plausible numbers. That is why they survived.

---

## P0-6 — the snapshot stopped at inference

`decision_snapshot` froze what **inference** reads. Expectancy, the precision instrumentation,
the quality filters and the revision ledger each re-read `data_state` afterwards, while
WebSocket callbacks kept mutating it.

The ledger row was the sharpest case: stamped `snapshot_ts=now_ms_pred` while its price and
order flow were read *after* prediction and filtering. The one row whose purpose is to record
what was true at the decision instant recorded something else.

**The price could not be recovered from the snapshot.** `snap["klines"]` is the model's
**closed-bar** window; the loop priced from the live **forming** bar. Pointing the gates at the
last closed bar would have silently *repriced* every decision rather than frozen it. So
`decision_price` is captured at freeze time and participates in the identity hash — two
decisions at different live prices inside one forming bar are different market states.

**Found while wiring it:** `apply_live_quality_filters` reads three keys that were not in
`SNAPSHOT_KEYS`. `.get()` returns a default rather than raising, and each default fails in a
direction that looks like normal operation:

```text
order_flow_updated_ms   missing -> staleness measured against 0 -> EVERY decision reads
                                   as a dead feed and is filtered
poor_regimes            missing -> the regime-quality veto silently stops vetoing
spread_expansion_ratio  missing -> a calm book during a blow-out
```

The absent-key discipline only protects inference if the key list keeps up with its consumers,
so a test now re-derives the list from the function's own source.

## P1-1 — one row, three moments

Direction came from the resolving bar; every magnitude field came from `current_price`.

```text
actual_move_usd   = current_price - predicted_price
target_error_usd  = current_price - target_price
actual_price      = current_price
_actual_strict    = "UP" if current_price >= predicted_price else "DOWN"
```

On the test path the defect reports **+11.50 against a DOWN call**; it now reports **−1.50**.

The last line was not in the audit's list and matters most: `_actual_strict` drives the
**learned regime weights**, which decide how much each seat is trusted per regime. They were
fitted against a loop-time *endpoint* sign while the models train on *first touch* — the same
mismatch the verifier was rebuilt to remove, surviving one variable over.

**A defect introduced by the P1-3 fix and caught here:** `target_contract.grade` returned
`path[-1]` — the last bar of the window, not the bar that touched. A path can pierce the lower
barrier in bar 1 and rally for four more. `first_touch_at` now returns `(outcome,
resolving_index)`.

## P1-3 — two panels, two random variables

```python
actual_dir = "UP" if current_price >= p["ref_price"] else "DOWN"
```

Four defects in one line: loop-time price; endpoint sign against first-touch-trained models; no
lateness bound; and no NEUTRAL, so a flat bar always scored UP and any DOWN vote always missed.
These numbers select model seats.

Fixed by **extraction, not a second implementation**. `target_contract.grade` is now the only
grader and both verifiers call it — two copies is how they diverged.

## P1-4 — one seat, a different dataset

`base_histgb.fit(X_train_h, y_train_h)` — no `sample_weight`, while six other seats had it. That
seat ignored recency, ignored class balance, and trained on exactly the rows every other seat
was told to ignore, while the OOF stacker combined all seven as if they agreed on what the
training set was. Every `fit` call in the block was audited; histgb was the only one.

Class weights were also counted over all rows including the zero-weight ones. Ambiguity is not
uniform across classes — a bar violent enough to touch both barriers is not a NEUTRAL-looking
bar — so outcomes the model never learns from set the frequencies for the ones it does.

**A comment corrected by measurement.** Zeroing a sample weight is *not* equivalent to removing
the row: HistGB computes feature bin edges from every row handed to it regardless of weight.
Held at `max_bins=255`, so it is binning and not loss.

```text
weighted vs rows-removed    100% identical hard decisions, ~0.004 mean probability drift
unweighted vs rows-removed   58% agreement,                ~0.47  mean drift
```

## P1-6 — the narrowest band where it was least earned

In-sample residuals were stored as the conformal band whenever the held-out slice was thin.

```text
in-sample IQR  0.407
held-out  IQR  1.534
```

Roughly **3.8× too narrow**, firing precisely where overfitting is most likely. The caveat
lived only in a log line — `_conf_src` never reached the artifact.

Ladder is now regime-held-out → global-held-out → **no band**. The stored dict carries `source`
and `n`; the served prediction carries `expectedMoveRangeSource`. The empirical regime prior
still supplies a range and is kept, but labelled `regime_prior_only` rather than emitted under
the same key as a coverage claim.

## P1-7 — undefined outcomes scored as NEUTRAL

`build_sequences` gives an AMBIGUOUS row a NEUTRAL one-hot so a mask-ignoring `argmax` stays in
range. Training excludes them by weight; the promotion gate did not, and `server.py` had
`Yvalid` in hand ninety lines above the call.

A model abstaining on a chaotic bar was credited a hit for a label meaning *unknowable* —
moving the Brier and ECE the gate promotes on. A too-short mask is now refused rather than
aligned by guessing; an all-ambiguous holdout FAILS rather than passing on an empty set.

## P1-9 — a calibrator selected by filesystem timestamp

```text
challenger trained Monday   -> artifact mtime = Monday
incumbent predicts Mon-Fri
challenger promoted Friday  -> mtime STILL Monday; the file was never rewritten
```

Every incumbent prediction from Monday to Friday satisfied `timestamp >= mtime`.

Selection is now `model_version = <serving bundle>`, rebound every refresh. The mtime rule is
kept only for rows predating the column, and the mode (`bundle:<id>` vs `mtime_fallback`) is
recorded so a fallback fit is never mistaken for an exact one.

Made concrete on one table — incumbent rows all correct, challenger rows all wrong:

```text
fitted as incumbent    1.0
fitted as challenger   0.0
old mtime rule         0.5     <- the contamination, as a number
```

## P1-12 — a venue that was never observed

```python
"coinbase": (binance + prem) if binance is not None else None
```

`prem` defaults to `0.0` and Coinbase is geo-blocked from this box, so the usual value is
**Binance exactly**. That number was folded into the median consensus, assigned a deviation
from consensus, and graded as an independent venue.

Every consequence flatters the signal: the median counted Binance twice and was pulled toward
it (102.0 → 101.0 with four real venues); Coinbase agreed perfectly with itself and that was
reported as cross-venue confirmation; fragmentation was understated, because a duplicated venue
never disagrees.

`handle_coinbase_ticker` now retains the observed price and its receive time; the venue is
absent unless that print is fresh. **Absent beats invented.**

---

## Two audit claims that were wrong

**"The predictions table already stores `model_bundle_id`."** It does not — that column is on
`ab_results`. The identity *is* on `predictions_{h}m`, under `model_version`. The fix was
available without a migration, but not at the name given.

**"The backtester leaks ambiguous rows as NEUTRAL."** It does not use `build_sequences` at all
— it derives endpoint labels from closes against a threshold, so it has no ambiguity concept.
Its real problem is grading a different contract from the one the models train on, which is
**P0-2**. Left alone rather than "fixed" into something that was never broken as described.

---

## What does NOT take effect until a retrain

**P1-4** and **P1-6** are training code. The live champion bundle was fitted under the old
weighting, and if its conformal residuals were written under the in-sample fallback they remain
too narrow — they now report `unknown` rather than claiming `held-out`.

Deliberately not retrained here: the oracle release freeze already needs a new release id, and
retraining before that is resolved would move the champion identity underneath an unresolved
freeze.

## Validation

```text
local CI       160 OK / 134 checks
               2 FAIL, both pre-existing and unrelated: #73 oracle release freeze, and
               #134 the Windows aggregate that fails because #73 does
mutations      21/21 caught across the four commits
```

New suites, all registered in **both** jobs of `invariants.yml`:

```text
test_resolution_observation_consistency.py   31
test_per_model_grading_contract.py           16
test_sample_weight_coverage.py               11
test_venue_independence.py                   12
test_conformal_source_honesty.py             13
test_calibration_bundle_identity.py          11
                                             --
                                             94
```

Registration mattered on its own: `run_ci_locally` executes what `invariants.yml` declares, so
a test file that is not declared there is not a regression test — it is a script that passed
once. The P1-1 and P1-3 suites existed for two commits before anything ran them.

## Three test errors worth recording

They are the same failure as the defects themselves — a check that passes while the property is
false — and only mutation testing found them.

1. **A tautology.** `check(... or Recorder().record(...)[0:0] == [])` — an empty slice always
   equals an empty list, so the check could never fail.
2. **A truncated scan.** The snapshot key-coverage check read a fixed 6000-byte window of
   `server.py`, cutting off before the line that reads `poor_regimes` — it passed while missing
   exactly the key it was written to catch. Now bounded by the next top-level `def`.
3. **A fixture that could not distinguish.** The P1-1 first-touch test touched on bar 0, where
   "first bar", "index 0" and "the touching bar" all coincide, so an index mutant survived. A
   second fixture touches on bar 2.

A fourth, in the same family: the venue median demonstration first used three venues, where the
duplicate happens to land on the same median. *Sometimes harmless is not harmless.*

## Still open

**P0-2** is now the largest: the model is trained on first touch and consumed as endpoint
direction by Polymarket UP/DOWN and the Binance EV calculation. Unlike everything above, it
needs a second trained head, not a grading fix.

Also open from the audit: **P1-2** (first-touch grading from 1m OHLC for mid-bar decisions),
**P1-5** (OOF stacker fitted on different base models from those served), **P1-8** (backtest
cache keyed by architecture, not artifact), **P1-10** (overlapping calibration systems),
**P1-11** (feature retirement not bound to artifact identity).

One latent path back, flagged rather than fixed: `server.py` has two
`v.get("actual_price", current_price)` fallbacks. They cannot fire today because the key is
always written, but the default is precisely the value P1-1 removed.
