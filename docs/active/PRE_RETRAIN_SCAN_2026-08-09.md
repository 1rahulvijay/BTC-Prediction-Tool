# Pre-retrain scan — 2026-08-09

Scan performed before launching the 1,000-day retrain, against `8b449a1`.
Two defects found and fixed; several risks checked and cleared.

---

## THE OPERATIONAL BLOCKER — read this before starting

`check_feature_contract.verdict_for` refuses **every** artifact when `code_dirty is not False`,
and `artifact_identity._source_commit_state` computes it as:

```python
dirty = bool(subprocess.run(["git", "status", "--porcelain"], ...).stdout.strip())
```

Any uncommitted change — including untracked files — makes every artifact produced by the run
unservable. **Starting the retrain with a dirty tree burns 12–30 hours and leaves the app
exactly as dark as it is now.**

Two consequences:

1. **Commit before starting.** Verify with:
   ```bash
   python -c "import sys; sys.path.insert(0,'backend'); from artifact_identity import _source_commit_state; print(_source_commit_state()[1])"
   ```
   It must print `False`.

2. **Do not edit tracked files during the run.** `SEMANTIC_CODE_PATHS()` is `model.py`,
   `features.py`, `model_contract.py`, `target_contract.py`, `regime.py`, `calibration.py`,
   `decision_gate.py` — editing any changes `code_hash` and invalidates the bundle. A parallel
   session co-edits this repo, so this needs coordinating.

Good news: `data/` is gitignored, so the ten recorders writing logs and DuckDBs do **not**
dirty the tree. Confirmed with `git check-ignore -v`.

---

## Defect 1 — the matrix skip gate never checked the manifest described the parquet

Fixed in `8b449a1`. Found live on disk:

```
research_matrix_1m.parquet    Aug 9   360d, 518,400 rows   hash e14f8b79...
research_matrix_1m.manifest   Jul 28  requested_days=60    hash 281657b2...   MISMATCH
```

Two files that must move together were 12 days apart. Evaluating the real skip predicate
against that state, `--days 60` fired `skip=True`.

This matters because `current_training_identity` reads `training_data_hash` **from the
manifest**, falling back to hashing the parquet only when the manifest has no hash at all — so
the fallback is unreachable in exactly the case that needs it. A stale hash wins silently and
would be stamped onto a freshly trained bundle as its provenance.

`sources_older` cannot catch this: it compares *source* mtimes to the *matrix* mtime and says
nothing about whether the manifest was regenerated alongside the matrix.

Added `_manifest_describes_matrix()` to the skip condition. Hashing 147MB costs 0.11s.
`build_research_matrix.py` had **no test and appeared nowhere in CI**, despite being the single
script that produces the training data for every head; it now has both.

---

## Defect 2 — the training-window check was verified fail-OPEN

Fixed in this change. Save and load resolved the window through **different resolvers**:

```
save  (model.py)  resolve_history_days()           env x3 -> matrix manifest -> 60, never None
load  (model.py)  configured_model_training_days()  ONE env var, else None
```

and `artifact_compatibility` skips any expected key that is None:

```python
expected_value = expected.get(key)
if expected_value is None:
    continue
```

So a bundle stamped `requested_days=1000`, loaded into a process where
`BTC_MODEL_TRAINING_DAYS` happened to be unset, did not fail the window check — **the check
silently ceased to exist.** Reached whenever the server starts by anything other than
`start.bat`, since only the launcher sets that variable. `artifact_identity.py` already called
that launcher line *"alignment that only holds when someone uses the right launcher — a
convention, not a control"*; the load site still depended on the convention.

Isolating the single variable, everything else matching by construction:

| load resolver | `requested_days` | window check |
|---|---|---|
| OLD `configured_model_training_days()` | `None` | **never fired** |
| NEW `resolve_history_days_verbose()` | 60 (source: matrix manifest) | `requested_days mismatch: artifact=1000 current=60` |
| matching window | 1000 | no complaint |

### Why the naive fix was wrong, and what was done instead

Simply swapping resolvers could resolve to the last-resort 60 against a stamped 1000 and
**create** a rejection outage. It is safe here because `resolve_history_days` falls back to the
**matrix manifest**, not to a literal — and the manifest records what the training data
actually is. After the retrain both paths give 1000:

* via `start.bat`: `BTC_MODEL_TRAINING_DAYS=1000` → `env:BTC_MODEL_TRAINING_DAYS` → 1000
* direct `python server.py`: env unset → matrix manifest (rebuilt to 1000) → 1000

**This does change behaviour**: the bundle and the matrix manifest must now agree on the
window. That is the invariant that should hold, and a disagreement is a real finding rather
than a false alarm. Today's stale 60-day manifest would reject a 1000d bundle — but there is
no loadable bundle today anyway (0/11 have manifests).

A malformed override now raises at load, caught by the existing handler at `model.py:3830` as a
clean refusal rather than a crash — `resolve_history_days_verbose` refuses to guess a window.

### The skip is kept, but made visible

`artifact_compatibility` cannot fail closed on every None — several keys are legitimately
absent (`row_count`, `actual_span_days` in the current manifest), and refusing on all of them
would reject every honest bundle on disk. So the skip stays and becomes **reportable**:

* `COMPARED_IDENTITY_KEYS` is now module scope, and the comparison loop walks it, so the
  reporter cannot drift from the list actually compared.
* `unverifiable_identity_keys(expected)` names the fields whose expected side is None.
* The model load path logs them: *"Identity fields NOT verifiable — these were not checked,
  not passed."*

A skipped check that leaves no trace is indistinguishable from a passed one. That is what let
this sit open.

### Also fixed

`verify_artifact_identity.py` printed the label `BTC_HISTORICAL_DAYS` while reading
`BTC_MODEL_TRAINING_DAYS` — the wrong variable named in the one tool an operator opens to debug
a window mismatch. It now prints the resolved window, **its source**, and the raw override
separately:

```
training window                60d (source: manifest:research_matrix_1m.manifest.json)
BTC_MODEL_TRAINING_DAYS        (unset)
```

### Locked against regression

Asserted on **AST call nodes**, not source text: both files now carry comments that name
`configured_model_training_days` while explaining why it must not be used, so a substring
search would be satisfied by the very documentation of the fix — a trap this repo has sprung
several times. All four mutations are caught:

```
CAUGHT  revert LOAD to the narrow resolver (the original hole)
CAUGHT  drop the unverifiable-keys report
CAUGHT  make unverifiable_identity_keys blind to explicit None
CAUGHT  let artifact_compatibility FAIL on a None expectation
```

---

## Checked and cleared

| risk | finding |
|---|---|
| Disk | 429 GB free, `data/` 65 GB. Recorders ~1–2 GB/day. Not a constraint. |
| Retrain inputs | Preflight passes: derived sources span 1300d ≥ 1000d requested. No bulk download; the run will not stall. |
| DuckDB writer contention | All four retrain builders (`backfill_trade_features`, `build_persistence_dataset`, `build_crossvenue_flow`, `build_research_matrix`) are Parquet/CSV and open **no** DuckDB. No conflict with the ten recorders. |
| DuckDB health probes | Read-only opens *are* blocked on Windows while a writer holds the file (verified empirically). Already handled: `recorder_health._locked_store_progress` falls back to DB/WAL size+mtime and labels the method `locked_writer_db_wal_progress`. |
| Launcher vs registry | Exactly 10/10 match between `start_recorders_once.ps1` and `RECORDER_CLOCKS`. No orphans either direction. |
| Completion marker | Fail-closed on `HEAD_RETRAIN_COMPLETE` — not written if any head fails. |
| Matrix rebuild for this run | `manifest_days=60 != 1000` → skip does not fire → rebuild happens → stale manifest self-heals. |

---

## Verification after the run

The cleanest success signal. All 12 artifacts currently read UNKNOWN:

```bash
python backend/check_feature_contract.py
```

Expect **0 STALE, 0 UNKNOWN**. Then:

```bash
python backend/verify_artifact_identity.py
```

Expect the training window to read 1000d and artifacts to load.

---

## External audit pass — validated item by item

An external audit against `e58de906` raised 17 items. Verified against source; results below.

### Confirmed and fixed

**P0 — the launcher undid the repaired promotion thresholds.** `model_promotion.py` documents
0.48 precision as *"BELOW a coin flip"* and 0.80 Brier as worse than a uniform guess, and
repaired its defaults to 0.50 / `UNIFORM_3CLASS_BRIER`. `start.bat:98-99` then set **exactly
those two condemned values** as environment overrides, which `env_float` prefers — so the
repair never ran on the normal launcher. The selftest asserted only `> 0`, so 0.48 sailed
through CI.

Severity is asymmetric and the audit did not note this: Brier has a second, baseline-relative
gate (`brier_not_better_than_class_prior`), so a bad-Brier model is still caught. **Directional
precision has no baseline-relative equivalent** — that floor is the only thing between a
sub-coin-flip model and promotion.

Fixed as a *control, not a convention*: `env_float_no_weaker` lets an override tighten a gate
but clamps any attempt to weaken it past the safety bound, and logs when it fires. Fixing only
`start.bat` would leave the hole open for any other shell, service manager or CI job. Both were
fixed anyway. Regression test covers all four directions.

Also corrected: the comment credited a `_baseline_gate_failures` helper that **does not exist
in the module** — the logic is real and inline, the name was not.

**P0/P1 — `independence_validated` was an overclaim, and it gates money.** `entry["independence
_validated"] = horizon_groups is not None`: supplying *any* groups set it True. The server now
passes fixed-width time blocks, whose adjacent boundaries still share a lookback. Critically,
`model_consensus.py:225` **gates on this flag** — so the change would have flipped it True and
*unlocked a strategy that is currently abstaining*, on a property the grouping does not have.

Fixed by making the caller declare its grouping semantics. Only `DISJOINT_UNITS` establishes
independence; `TIME_BLOCKS` records `dependence_blocking="time_blocks"` with
`independence_validated=False` plus a note. An undeclared grouping claims nothing. The server
declares `TIME_BLOCKS`, so `model_consensus` continues to abstain — no new money authority.

**P1 — the funding recorder manufactured zeros.** Correct, and it was my defect: `_f`/`_i`
coerce any unparseable field to 0, and every critical field went through them. A malformed
response would have stored `fundingTime=0` (an epoch-1970 settlement), `fundingRate=0` (a
real-looking 0 bps observation) and `markPrice=0`, all indistinguishable downstream from
measurements. Critical fields now validate or the payload is quarantined as a gap row.

**But the audit's remedy, applied literally, would have destroyed 13% of the data.** Requiring
a positive `markPrice` quarantines 460 of 3,500 settlements, because **Binance genuinely
returns `markPrice: ""` for every BTCUSDT settlement before 2023-10-31** while `fundingRate` is
perfectly good. Verified against the live API. The rule therefore splits by *role*, not type:
`fundingTime` and `fundingRate` are the evidence and must validate; the mark is context and may
be absent — recorded as **NULL**, the one value a study cannot mistake for a measured zero. The
460 already-stored zeros were migrated to NULL; all 3,500 settlements intact, mean unchanged.

**P1 — funding cadence hardcoded against a configurable symbol.** Correct. Now measured from
the settlements themselves (modal spacing), falling back to the constant only while too few
rows exist, and labelled `observed` vs `declared_default`. The audit's stated risk was
fabricated gaps; the actual failure of an 8h constant on a 4h symbol is the **opposite** — a
genuine 8h hole reads as normal and is *missed*. The test asserts on that behaviour.

**P1 — missing recorder table reported `NEVER_RAN`.** Correct, and internally inconsistent: a
missing *column* correctly set `SCHEMA_DRIFT` while a missing *table* — the more severe fault —
fell through to the initial `NEVER_RAN`. Visible in live output: `cross_window_recorder` shows
"table absent" against a database present on disk, sending an operator hunting a process that
did in fact run. Fixed, with a negative selftest that probes a real drifted database.

**P1 — locked DuckDB reported `rows=0`.** Correct. `rows` now starts as `None` with a
`rows_known` flag; a count of zero is a measurement, and every path that returns before
counting (absent store, writer lock, schema drift) now says unknown instead.

### Refuted

**P0 — "Polymarket exact-round discovery uses the wrong Gamma route."** Not correct. Tested
both routes live against all four current slugs (5m/15m, current and next):

```
btc-updown-5m-1786287600   /markets -> list[1]   /events -> list[1]   same conditionId
btc-updown-15m-1786287600  /markets -> list[1]   /events -> list[1]   same conditionId
```

`/markets?slug=<exact>` is **not** empty. The audit conflated the broad *listing*
(`/markets?closed=false&limit=…`), which did surface only far-future rounds, with the *exact
slug* lookup — and the exact-slug fetch is the fix for that, as the code comment at
`live_btc_updown_recorder.py:318` states. The recorder's User-Agent was also checked against
Gamma and returns HTTP 200. No change made.

### Confirmed open, deliberately not fixed in this pass

**P1 — A/B bootstrap resamples individual predictions** (`ab_testing.py:428`,
`rng.integers(0, n_min, n_min)`). Real: consecutive BTC predictions share overlapping horizons
and feature history, so 1,000 predictions are not 1,000 experiments and the lower bound is
optimistic.

**P1 — A/B durable evidence can diverge from memory** (`ab_testing.py:337`): `resolve()` catches
a DB failure at DEBUG and still calls `record_outcome()` in memory, so a restart can lose
resolved rows the running process counted.

Both are real and both gate **challenger promotion**, which requires 30+ days of A/B — not the
retrain. Fixing them means changing the bootstrap unit to day/week clusters and adding a
dead-letter path, which is a larger change than belongs immediately before a long run. Fix
before the paper competition, not before the retrain.

**P2 — rolling endpoint head carries Polymarket rule metadata**, and **P2 — promotion baseline
uses the holdout's own class prior** (an oracle baseline; conservative, it can only reject).
Both accepted as stated, neither retrain-blocking.

---

## Known, not fixed

`start.bat` only runs `--selftest` on the recorders; `start_recorders_once.ps1` starts them.
The evidence audit's `wired_recorders()` regex matches the selftest lines, so a recorder counts
as "wired into the launcher" on the strength of a selftest invocation alone — the gap the
evidence check's own footer names.

Minor: the settlement-head groups slice `train_ts[LOOKBACK:LOOKBACK+len(X)]` silently truncates
if `train_ts` is short, surfacing as an `IndexError` hours in rather than an assertion. The
invariant holds by construction today.
