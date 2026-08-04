# Validation sweep — 2026-08-04

A full pass over everything built in this line of work: what was re-run, what passed, what was
found broken and fixed, and — importantly — **what was NOT validated and why**.

Head: `53328b5` + this sweep. Local CI: **PASS**. Protocol seals: **23/23**.

---

## 1. What was actually re-run

| gate | result |
|---|---|
| `run_ci_locally.py` (117 steps, every selftest) | **PASS** |
| `verify_prereg_hashes.py` | **23/23 intact** |
| `test_artifact_serviceability.py` | 0/25 serviceable, at floor — **PASS** (ratchet holds) |
| `audit/recorder_evidence_check.py` | ran; `binance_l2_recorder` still `NEVER_RAN` |
| `train_crossing_heads.py --status` | **`serviceable: true`** |
| `crossing_recorder_hf.py --selftest` | **58 checks** |
| `crossing_recorder_hf.py --health` | `LOCKED_BY_WRITER` (correct: a writer holds it) |
| `run_all_sequence.py --selftest` | 30 frontier scripts, **uncovered=0** |

The crossing head survived the target rename (`reverted_Ns` → `state_original_side_at_Ns`) and
a retrain, and still loads through the real verification path.

---

## 2. Defects found and fixed in this sweep

### 2.1 Two vacuous checks — both mine

A repo-wide scan for tautological assertions found two, both written by me earlier in this work:

```python
research/algodesk/data.py       check(merged.open_interest.isna().sum() == 0 or True, ...)
research/maker_execution_v1.py  check(hour_block_ci(...)[0] != 0.0 or True, ...)
```

`or True` makes an assertion unfalsifiable. Replaced with real properties:

- **algodesk**: every bar at or after the first OI print carries a value, *and* the joined
  values are the ones supplied — so an invented value fails too.
- **maker**: two blocks with a non-zero mean return a **finite** interval that **brackets** the
  mean.

Both mutation-tested afterwards: dropping the OI join is `CAUGHT`; making the CI always return
`nan` is `CAUGHT`. **Repo-wide tautology count is now 0.**

### 2.2 `--health` crashed on a locked database

DuckDB is single-writer. During a live run the health check raised `IOException` instead of
reporting anything — useless at exactly the moment it is consulted. It now returns
`LOCKED_BY_WRITER` and exits 0, because a held lock is the strongest available evidence that a
writer **is** running.

### 2.3 A background job silently reverted a fix

A mutation-testing job left running in the background held a stale copy of
`crossing_recorder_hf.py` and restored it on failure, **undoing a later correction**. It was
caught only because the selftest count dropped 58 → 57.

Mutation testing now always runs against a **temp copy** and never writes the real file. This is
a process defect worth remembering: long-running jobs and concurrent edits do not mix.

---

## 3. Mutation testing — the real validation

A passing selftest proves nothing if the checks cannot fail. Every load-bearing property added
in this work was mutation-tested by breaking the implementation and requiring the check to fail.

### Restart safety (`crossing_recorder_hf.restore`)

```
don't re-adopt the durable anchor   CAUGHT
recover leader as a blank slate     CAUGHT
restart the crossing index at 0     CAUGHT
don't count obligations             CAUGHT
re-adopt already-ended rounds       CAUGHT
```

### Supervision (`crossing_recorder_hf.supervise`)

```
no restart on failure               CAUGHT
backoff never grows                 CAUGHT   (SURVIVED before 2.1's sibling fix)
barren counter never resets         CAUGHT
Ctrl-C treated as a fault           CAUGHT
deadline never expires              CAUGHT
```

The backoff check was itself vacuous before this sweep: it compared `slept` against
`BACKOFF_MS[0]` and `[1]`, so flattening the schedule flattened the expectation with it.
Now `slept[1] > slept[0]`, plus an assertion that the declared schedule is non-decreasing and
not flat.

**15 of 15 mutations caught across restore, supervise, and the two replaced checks.**

---

## 4. Proposed solutions — which are implementable here

Assessed against what this repository can actually support today.

| proposal | verdict |
|---|---|
| Supervised recorder service (#4) | **Built.** Restart with bounded backoff, health from row progress |
| Round-equal-weighted AUC (#12) | **Built.** Result survived; one number corrected |
| Restart safety (#5) | **Built and mutation-proven.** Anchors, leaders, index, obligations all recover |
| Trade-driven fill model | **Built** (parallel session). Fill rate 99.5% → 39.8%, markout flipped sign |
| Preflight datastore identity (#3) | `datastore_identity.py --strict` exists; **the canonical choice is still unmade** |
| Supervision / row-progress health (#1/#2) | **Built** for the HF recorder; not generalised to other recorders |
| Bybit L2 depth heads | **Built** (parallel session), registered in the runner |
| Digital-option fair value | **Blocked** — needs Deribit IV, not collected |
| Cross-venue transmission graph | **Blocked** — needs Coinbase L2 / OKX, not collected |
| Delta-hedged Polymarket basis | **Blocked** — needs paired Polymarket books, 0 rows |
| Polymarket reaction-function residual | **Blocked** — same |
| Volume/information clocks | **Runnable now** on the 360-day archive; not built |
| Similar-state retrieval, conformal abstention | **Runnable now**; not built |
| Self-supervised L2 encoder | Runnable on Bybit L2, but premature — simple heads first |

The pattern is unchanged: **what is blocked is blocked on collection, not on modelling.**

---

## 5. What was NOT validated, and why

Stated explicitly so this document is not mistaken for more than it is.

- **The full research suite was not re-executed.** `run_all_sequence.py` runs 30 studies, several
  over multi-GB archives with a 900s per-script timeout — hours of compute. Its **selftest**
  passed (coverage complete, 0 uncovered), and every study's own selftest ran inside CI, but the
  scored results stand on their original runs, each documented with its own protocol hash.
- **The recorder has not run for weeks.** The service is now restart-safe and supervised, and
  that is a property of the code, not evidence of uptime. The 5s/15s study still needs ~3 weeks
  of actual collection.
- **No forward protocol matured.** B, C and D remain sealed and unscorable.
- **The frontend was never examined** in any part of this work.
- **0 of 25 legacy artifacts are serviceable.** Unchanged. The crossing head is a 26th, built to
  the contract; it does not repair the other 25.

---

## 6. Standing state

```
CI                     PASS (117 steps)
protocol seals         23/23
tautological checks    0
mutations caught       15/15
crossing head          serviceable, authority NONE
recorders              1 NEVER_RAN, round recorders dark since 2026-07-25
artifacts              0/25 serviceable
capital                real orders disabled, paper/shadow only, 0 promotable strategies
```

The binding constraint has not moved: **cost on the research side, collection on the
infrastructure side.** Every lane measured remains closed on cost, and every sealed forward
protocol remains waiting on a recorder that is not running.
