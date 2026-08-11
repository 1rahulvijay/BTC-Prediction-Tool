# Repo scan findings — round 2 (recorder health, serving caches, boot)

Written 2026-08-10 against HEAD `804a108`, tree still carrying the parallel session's 40
uncommitted files. Companion to `REPO_SCAN_FINDINGS_2026-08-10.md`; no overlap with it — that
pass covered the terminal-outcome fence, gate coverage, and the serving-path fallbacks in
`model.py`. This pass covers recorder health, `server.py` caches, DB boot, and the frontend.

Same rule as round 1: everything below was reproduced by running something. Two hypotheses I
formed while scanning **did not survive checking** and are recorded as such rather than dropped.

Nothing was fixed. The only repo file this scan wrote is this document.

> Date correction (2026-08-11): this scan ran on **2026-08-11**, not 2026-08-10. Finding 6 also
> misstated the stall as "~12 hours"; re-measured it is **20.8 hours**, and `crossing_recorder_hf`
> is genuinely live (newest row within minutes). See round 3, "Corrections to rounds 1 and 2".

> Resolution note (2026-08-11): findings 1-5 were fixed in the subsequent core hardening pass.
> Binance L2 health uses `received_ts_ms`; declaration drift exits nonzero; launcher parsing
> excludes audit/selftest decoys; all-time accuracy exposes stale failures; and the DuckDB anchor
> uses `_connect()` retry semantics. Finding 6 remains an operational observation: the recorder
> fleet must advance after launch before its data is considered current.

Probe scripts (session scratchpad, will not survive — move them into the repo if you work any of
this):

    probe_recorder_coverage.py     finding 3
    probe_launcher_wiring.py       finding 3
    probe_binance_l2_schema.py     findings 1, 2

---

## 1. `binance_l2_recorder` health is permanently broken — it declares a column that has never existed

`recorder_health.RECORDER_CLOCKS` (`recorder_health.py:67`) declares:

    "binance_l2_recorder.py": ("binance_l2.duckdb", "l2_diffs", "ts_ms", "MILLIS")

There is no `ts_ms` column in that table. Read the live store directly (`data/binance_l2.duckdb`,
1.65 GB):

    l2_diffs   rows=519,706
        session_id, ordinal,
        received_ts_ms, event_ts_ms, transaction_ts_ms,     <-- the three real clocks
        first_update_id, final_update_id, previous_update_id,
        bids_json, asks_json, applied, disposition,
        payload_sha256, book_top_sha256

So the probe can never compute an age, and the audit reports it every time:

    binance_l2_recorder.py    True    -    -    SCHEMA_DRIFT
      column ts_ms absent from l2_diffs

**This is the recorder `recorder_evidence_check.py`'s own docstring was written about.** That
docstring (lines 3-10) records it as "804 lines of correct, CI-gated, selftested code" that "has
never recorded a single row". It now holds 519,706 rows — and the health surface still cannot say
whether it is advancing or dead, for a different reason than before.

The fix is one word: `ts_ms` → `received_ts_ms`. That is the recorder's own receive clock and is
the same semantic as `recv_ts` / `recv_ts_ns` used by the other declarations. Pick deliberately
between `received_ts_ms` (when we saw it) and `event_ts_ms` (when the venue stamped it) — the
module's unit-trap docstring argues the choice should be explicit, and row-progress health wants
the receive clock.

`l2_gaps` is empty (0 rows) across 519,706 diffs and 16 sessions. Not investigated; may be correct
(no gaps detected) or may indicate gap detection never firing. Flagging only — **I did not verify
this either way.**

## 2. No gate can notice finding 1 — the health selftests validate themselves

Three separate reasons a permanently-drifted recorder stays green:

**The status is a declared one.** `recorder_health.py:316-319` asserts every probe returns a
status from `("ADVANCING", "STALLED", "NEVER_RAN", "LOCKED_BY_WRITER", "UNREADABLE",
"SCHEMA_DRIFT", "UNIT_MISMATCH")`. `SCHEMA_DRIFT` is on that list, so a recorder that returns it
forever satisfies the assertion forever.

**The coverage check is a tautology.** `recorder_health.py:315` asserts
`len(live) == len(RECORDER_CLOCKS)` — "every declared recorder is probed". It compares the
declaration to itself. It cannot detect a recorder that is missing from the declaration, and it
cannot detect a declaration that no longer matches the data.

**The real audit is never run, and exits 0 anyway.** Measured:

    $ python -m backend.audit.recorder_evidence_check
    ... binance_l2_recorder.py ... SCHEMA_DRIFT ...
    exit code: 0

and CI invokes only the selftest, never the audit:

    invariants.yml:590   python backend/audit/recorder_evidence_check.py --selftest
    invariants.yml:927   python backend/audit/recorder_evidence_check.py --selftest || exit /b 1
    invariants.yml:1037  python backend/recorder_health.py --selftest || exit /b 1

This is the same shape as round 1's finding 1: a check that verifies its own declaration rather
than the world it describes. The cross-check at `recorder_health.py:309-312` is the honest part of
the module — it compares `RECORDER_CLOCKS` against `EXPECTED_STORE` so the two registries cannot
drift from *each other* — but neither is ever compared against the actual table schema.

Cheapest real fix: have the selftest (or the audit's exit code) fail on `SCHEMA_DRIFT` and
`UNIT_MISMATCH`. Both mean "the declaration no longer describes the data", which is exactly the
condition worth failing on, and distinct from `STALLED`/`NEVER_RAN`, which are legitimate
operational states a gate should not fail on.

## 3. `wired_recorders()` counts the audit tooling as recorders

`recorder_evidence_check.py:68`:

    re.findall(r"backend\\[\w\\]*?([\w]*recorder[\w]*\.py)", text)

The token `recorder` appears in the names of the audit modules themselves. Run against a launcher
text containing them, the function returns `recorder_health.py` and `recorder_evidence_check.py`
as wired recorders, alongside `open_position_action_recorder.py`, which is a selftest invocation
rather than a daemon.

Today this is inert: `audit()` reads `LAUNCHER = backend/start_recorders_once.ps1`, which contains
only the ten real recorders, and `EXPECTED_STORE.get(script, (None, None))` degrades quietly for an
unknown name. It becomes a live problem the moment the audit is pointed at `start.bat` (which does
reference all three) or the launcher gains a selftest line — the audit would report the health of
its own source file.

Match on the launch shape rather than the filename token, and exclude lines carrying `--selftest`.

## 4. `_ptb_alltime_accuracy` marks itself fresh before it succeeds

`server.py:1495-1506`:

```python
now = time.time()
if now - _PTB_ALLTIME_CACHE["ts"] < 60.0:
    return _PTB_ALLTIME_CACHE["val"]
_PTB_ALLTIME_CACHE["ts"] = now                 # stamped BEFORE the fetch
try:
    _PTB_ALLTIME_CACHE["val"] = database.fetch_price_to_beat_accuracy() or {}
except Exception:
    pass  # keep last good value; never break serving
return _PTB_ALLTIME_CACHE["val"]
```

The timestamp advances before the work is attempted, and the failure path both swallows the
exception and keeps the previous value. So if DuckDB is unavailable — locked, wrong `DB_PATH`,
corrupted — the function retries once per 60s, fails, re-stamps itself fresh, and returns stale
all-time accuracy indefinitely. Nothing in the payload marks it stale and nothing is logged.

On the very first call the kept value is the initial `{}`, so a boot-time failure serves empty
accuracy as though it were measured.

Compare `_forward_readiness_snapshot` (`server.py:5860-5873`), which is written correctly: it
stamps *after* computing, and its failure path returns an explicit
`{"available": False, "error": ...}` payload. That is the pattern to copy —
`_paper_rule_status_cached` (`:1528`) also does the honest thing by setting `val = None`.

`_ptb_alltime_accuracy` is the only one of the four caches with this shape. It is the same family
as the already-recorded finding that health freshness measured the file rather than the evidence.

**Verified by reading, not executed** — importing `server.py` starts real work, so I did not run
it. The control flow is unambiguous at those line numbers.

## 5. `init_db` opens the anchor connection without the retry it documents

`database.py:102-109`:

```python
# DELIBERATELY fail-fast ... _connect() already retries transient locks for ~5s
global _ANCHOR_CONN
if _ANCHOR_CONN is None:
    _ANCHOR_CONN = duckdb.connect(DB_PATH)      # raw connect - no retry
conn = _connect()                                # retrying connect
```

`_connect()` (`:66-87`) exists because "OneDrive sync / IDE indexers can briefly hold the file
handle; without a retry those writes are silently lost" — it retries six times with backoff, and
sets `memory_limit='512MB'` and `threads=2` to stop one heavy query competing with the live event
loop.

The anchor connection is opened on line 108 with a raw `duckdb.connect()`. It gets neither. The
comment two lines above tells the reader boot is protected by that retry; the first connect boot
performs is the one that is not.

Consequence is narrow but real: a transient lock at boot — precisely the OneDrive case the retry
was written for, and **this project does have a OneDrive-resident copy** — raises out of `init_db`
instead of being absorbed, and the app fails to start rather than retrying for ~5s.

Low effort: `_ANCHOR_CONN = _connect()`. The caps are irrelevant for a connection never used for
queries, but the retry is the whole point of the line above it.

## 6. Operational, not a code defect: the recorder fleet stopped ~12 hours before this scan

From the same audit run, last write per recorder:

    crossing_recorder_hf.py            ADVANCING   2026-08-10T19:39:39    32,543 rows
    btc_tick_recorder.py               STALLED     2026-08-10T08:41:43
    cross_window_recorder.py           STALLED     2026-08-10T08:41:51
    deribit_option_chain_recorder.py   STALLED     2026-08-10T08:39:34
    funding_recorder.py                STALLED     2026-08-10T08:41:15
    l2_recorder.py                     STALLED     2026-08-10T08:41:28    37,688,939 rows
    live_btc_updown_recorder.py        STALLED     2026-08-10T08:41:45
    microstructure_recorder.py         STALLED     2026-08-10T08:41:44
    multi_venue_recorder.py            STALLED     2026-08-10T08:41:49    31,287,431 rows

Eight recorders stopped within 17 seconds of each other around 08:39-08:41, which reads as one
launcher process ending rather than eight independent failures. `crossing_recorder_hf` is the only
one still advancing.

This is machine state, not a repo defect, and it may be entirely intentional. It is recorded
because every hour it continues is evidence not collected, and because a retrain drawing on these
stores will silently see a window that ends at 08:41 today.

---

## Checked, no defect

- **Recorder coverage.** Compared every `*recorder*.py` on disk against `RECORDER_CLOCKS`. Six are
  undeclared, but all six are analysis or audit tooling, or selftest-only
  (`open_position_action_recorder.py` appears in `start.bat:492` **only** as `--selftest`, not as a
  daemon). I initially wrote this up as "a launched recorder with no health monitoring"; that was
  wrong, and checking `start.bat` line by line is what corrected it. No live recorder is missing a
  declaration.
- **`open_position_action_recorder` store mismatch.** It names both `open_position_actions.duckdb`
  and `actions.duckdb`. The second is a temp path inside its own selftest
  (`open_position_action_recorder.py:995`). Not a two-store split.
- **pytest gate.** `python -m pytest -q` in the current dirty tree: **155 passed**, 13 warnings,
  14s. No failing test is hiding behind the parallel session's changes.
- **Mutable default arguments and bare `except:`.** Zero of either in the core app (both appear
  only in `feature_finding/probe_*.py`).
- **Frontend numeric defaults.** The `|| 0` fallbacks in `src/main.js` (lines 1357, 1399, 1620,
  1704, 1909, 2127, 2206, 2307, 4205-4230) all default *downward* — a missing confidence renders
  0%, not a plausible mid-range value. That is the opposite of the `model.py:2489` defect in round
  1, and it is the safe direction. `renderBestLongShort` guards the empty case at
  `main.js:4198` before indexing `[0]`.
- **Frontend build wiring.** Standard Vite: `index.html:1070` loads `/src/main.js`, `dist/` is
  build output and is newer than `src/`. No duplicate or stale second frontend.
- **The other three `server.py` TTL caches.** `_forward_readiness_snapshot`,
  `_evidence_health_snapshot`, and `_ROW_HEALTH_CACHE` all stamp after computing or fail
  explicitly. Only finding 4 has the inverted order.

---

## Suggested order

1. **Finding 1** — one-word fix, restores health on a 519k-row store. Do it with finding 2 or it
   will just break again silently.
2. **Finding 2** — fail on `SCHEMA_DRIFT` / `UNIT_MISMATCH`, and run the audit itself in CI rather
   than only its selftest. This is the finding that makes the others detectable.
3. **Finding 4** — move the timestamp below the fetch, mark the payload stale on failure.
4. **Finding 5** — `_ANCHOR_CONN = _connect()`.
5. **Finding 3** — tighten the regex when the audit is next touched.
6. **Finding 6** — operator decision, not a code change.

Findings 1, 2 and 6 all bear on the same question: whether the data a retrain will draw on is
still arriving. Findings 4 and 5 are serving and boot robustness. None of them change what a model
learns.
