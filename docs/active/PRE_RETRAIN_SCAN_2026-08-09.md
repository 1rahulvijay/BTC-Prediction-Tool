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

## Known, not fixed

`start.bat` only runs `--selftest` on the recorders; `start_recorders_once.ps1` starts them.
The evidence audit's `wired_recorders()` regex matches the selftest lines, so a recorder counts
as "wired into the launcher" on the strength of a selftest invocation alone — the gap the
evidence check's own footer names.

Minor: the settlement-head groups slice `train_ts[LOOKBACK:LOOKBACK+len(X)]` silently truncates
if `train_ts` is short, surfacing as an `IndexError` hours in rather than an assertion. The
invariant holds by construction today.
