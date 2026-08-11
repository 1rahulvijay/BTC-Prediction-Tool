# Repo scan findings — round 3 (canonical datastore, fence coverage, gate coupling)

Written **2026-08-11 05:30 UTC** against HEAD `804a108`. The working tree now carries **51**
uncommitted files from the parallel session, up from 40 when round 1 was written.

Third pass. Rounds 1 and 2 are in `REPO_SCAN_FINDINGS_2026-08-10.md` and
`REPO_SCAN_FINDINGS_2026-08-10_ROUND2.md`.

**Read section 0 first.** Every finding from rounds 1 and 2 has been implemented in the working
tree since those documents were written, so most of what they say is now historical. This round
verifies those fixes rather than re-reporting them, and corrects two errors of my own.

---

## 0. Status of rounds 1 and 2 — all implemented, one fix verified as genuinely correct

Checked each against the current tree:

| finding | status in tree |
| --- | --- |
| R1-1 terminal-outcome fence blind spots | **fixed** — `TERMINAL_TABLES` rule added, `ast.JoinedStr` handled, flagged writers converted |
| R1-3/5 39 ungated selftests | **fixed** — `binance_paper` tests now registered (21 references in `invariants.yml`) |
| R2-1 `binance_l2` clock drift | **fixed** — `ts_ms` → `received_ts_ms` (`recorder_health.py:69`) |
| R2-2 drift not fatal | **fixed** — selftest returns 1 on `SCHEMA_DRIFT`/`UNIT_MISMATCH`; see finding 3 |
| R2-3 `wired_recorders()` regex | **fixed** — skips `--selftest` and `backend\audit\`, with a decoy test |
| R2-4 `_ptb_alltime_accuracy` | **fixed** — failure path now marks the payload stale |
| R2-5 `_ANCHOR_CONN` | **fixed** — `_ANCHOR_CONN = _connect()` (`database.py:108`) |

The fence fix is the one worth confirming rather than trusting, because it is the one my round-1
probe measured at 3/6. Re-running that probe still prints 3/6 — **and that number is now
misleading**. The new rule keys on the table name, so any REPLACE into a declared terminal table
is caught regardless of statement form; my probe used synthetic table names outside that set, so
it now measures only the residual heuristic. The shipped test carries its own probe of the three
hard forms and passes:

    PASS  terminal-table rule catches bound, literal and no-column REPLACE forms
    TERMINAL OUTCOMES NOT REPLACEABLE: PASS (7 checks, 0 known unfixed)

and the writers round 1 named — `shadow_signals`, `round_settlement_truth`,
`settlement_checkpoint`, `pm_round_settlements` — no longer contain a REPLACE statement at all. The
fix is real. Retire `probe_fence_gap.py`; it answers a question that no longer maps onto the code.

---

## 1. BLOCKER — the canonical datastore is missing `head_identity_json`, and two weeks of snapshot evidence

> **CORRECTED by round 4 — read `REPO_SCAN_FINDINGS_2026-08-11_ROUND4.md` §0 before acting on
> this section.** The `head_identity_json` finding below stands. The surrounding story about
> "two weeks of stranded evidence" does **not**: the two stores hold *zero* overlapping rounds and
> are consecutive segments of one timeline (handover 2026-07-05), and the canonical store is the
> **richer** one for certification — 6,725 officially settled rounds against 693. I built that
> framing from row counts without checking whether the stores describe the same rounds.

`REMAINING_AUDIT_ITEMS.md` flagged this as needing a check before anyone relies on it. Checked.
It is true, and the split is wider than a missing column.

`database._resolve_db_path()` resolves through `audit.datastore_identity`:

    CANONICAL_RELATIVE_PATH = data/btc_duckdbs/analytics.duckdb
    exists                  = True
    BTC_DB_PATH  = None          (no override)
    BTC_DATA_DIR = None

So the canonical store is `data/btc_duckdbs/analytics.duckdb`. Measured, read-only, both stores:

| store | table | rows | newest row | `head_identity_json` |
| --- | --- | --- | --- | --- |
| **btc_duckdbs/analytics.duckdb** (canonical, mtime 08-11 07:25) | `round_state_snapshots` | 186,985 | **2026-07-25T15:00:01** | **ABSENT** |
| | `champion_snapshots` | 186,985 | **2026-07-25T15:00:01** | **ABSENT** |
| data/analytics.duckdb (non-canonical, mtime 08-09 12:12) | `round_state_snapshots` | 4,128 | 2026-08-08T08:11:32 | present |
| | `champion_snapshots` | 115,193 | 2026-08-08T08:11:32 | present |

Two distinct problems, and the second is the one that costs evidence.

**The column.** The migration exists and is additive — `database.py:816` declares
`ADD COLUMN head_identity_json VARCHAR DEFAULT '{}'` for `round_state_snapshots`, and `:791` the
same for `champion_snapshots`. It has simply never been applied to the canonical file. The
consequence is correctly handled in code, not silently: `head_health.py:148-153` tests for the
column and, when absent, appends a blocker —

    round_state_snapshots.head_identity_json is absent; run the normal database migration,
    then collect new artifact-attributed outcomes

— rather than pooling unattributable rows. That is the right behaviour and the right refusal.
The practical effect is that **no head can currently be certified from the canonical store**,
because artifact-bound stratification (`head_health.py:195-199` filters on
`json_extract_string(head_identity_json, '$.<head>.sha256')`) has no column to read.

**The divergence.** The canonical store's snapshots stop on **2026-07-25**; the non-canonical
store carries them to **2026-08-08**. Roughly two weeks of `champion_snapshots` — 115,193 rows,
against 4,128 round-state rows — sit in the file the app no longer reads. `database.py:28-37`
documents that these two stores were previously written by different processes and that unifying
them was the point of the declaration. The unification took effect; the older data did not move
with it.

Anyone about to retrain or promote should settle this first. It is not a code change: it is a
migration plus a decision about whether the 07-25 → 08-08 rows in the non-canonical store are
evidence to be merged or a superseded lane to be abandoned. **I did not run the migration and did
not write to either store** — that writes to the live archive while another session is working,
and the merge question is not mine to answer.

One oddity worth a second pair of eyes: in the canonical store `round_state_snapshots` and
`champion_snapshots` report **exactly** the same row count (186,985) and exactly the same newest
timestamp (2026-07-25T15:00:01). Organically written tables rarely agree to the row. That pattern
suggests a bulk import rather than accumulated live writes. **I did not investigate this** — it
may be entirely expected.

## 2. `TERMINAL_TABLES` is hand-maintained with nothing tying it to the schema

The new rule is only as good as its list, and the list is a literal:

```python
TERMINAL_TABLES = {
    "price_to_beat", "kronos_predictions", "model_predictions", "forward_ev_ledger",
    "fsr_ppo_decisions", "ab_results", "shadow_signals", "round_settlement_truth",
    "settlement_checkpoint", "pm_round_settlements",
}
```

A table not on it falls back to the old column+literal heuristic — the one the fence's own probe
demonstrates is blind to bound parameters, missing column lists, and f-strings. Nothing fails when
a new evidence table is added and the list is not.

Parsed every `CREATE TABLE` in `backend/` and cross-referenced against `TERMINAL_COLUMNS`:

    tables parsed                    : 126
    holding a terminal column        : 14
    NOT in TERMINAL_TABLES           : 7

      complete_trade_forecasts_v2     resolved
      hf_crossing_events              move            <-- has a REPLACE writer
      historical_replay_predictions   actual_price    <-- has a REPLACE writer
      model_revision_outcomes         actual_direction
      open_position_action_outcomes   settlement_source
      predictions_5m                  hit, resolved
      shadow_orders                   resolved

Assessed each rather than reporting the count:

- **`hf_crossing_events.move`** is a **false positive**. It is `DOUBLE NOT NULL`, the measured
  price move at the crossing instant — an immutable property of the event, not an outcome filled
  in later. Outcomes live in the separate `hf_crossing_labels` table, joined by `crossing_id`
  (`crossing_recorder_hf.py:501-504`). The name heuristic is what flags it, not the semantics.
- **`historical_replay_predictions.actual_price`** is written in the same statement that creates
  the row (`database.py:2142`), for an offline replay explicitly held "outside live accuracy
  tables". Re-running a replay overwriting its own row is plausibly intended idempotency. Weak.
- **`shadow_orders.resolved`** and **`predictions_5m.hit, resolved`** are the ones that matter.
  `shadow_orders` is the direct sibling of `shadow_signals` — same file, same schema block
  (`decision/shadow_store.py:69-85`), same `resolved BOOLEAN DEFAULT FALSE`. One was added to
  `TERMINAL_TABLES`, the other was not. `predictions_5m` is a core live predictions table.

**Neither currently has a REPLACE writer, so there is no live destruction path.** This is a latent
gap: the protection is one forgotten edit away, and the thing that would catch that edit is the
heuristic already known to be blind.

The repo already has the right pattern for this — the shrink-only `KNOWN_UNFIXED` list that fails
on stale entries. Apply it here: derive the candidate set from `CREATE TABLE` statements
containing a `TERMINAL_COLUMNS` name, and fail when such a table is absent from `TERMINAL_TABLES`
without an explicit, justified exemption (which is where `hf_crossing_events` would go). That
makes the list self-maintaining instead of a thing to remember.

## 3. The drift gate was wired into the selftest, re-coupling CI to production data

R2-2 asked for `SCHEMA_DRIFT`/`UNIT_MISMATCH` to be fatal. It was implemented inside
`recorder_evidence_check.selftest()`, which runs `audit()` — and `audit()` reads the live stores.

That reintroduces a coupling the same file records as having been deliberately removed.
`derive_status`'s docstring (`recorder_evidence_check.py:109-117`):

> The reachability check used to assert that some real recorder was currently NEVER_RAN, or that
> every recorder sat in a healthy state - so it passed or failed on whatever the store happened to
> hold, and went red the moment one recorder entered SCHEMA_DRIFT, which is a state this audit
> exists to REPORT. A selftest that depends on production data tells you about production, not
> about the code.

The new block fails the selftest on exactly that condition. Today it passes (exit 0 — all ten
recorders are `ADVANCING` or `STALLED`), so nothing is broken right now. But CI runs the
**selftest**, not the audit (`invariants.yml:590`, `:927`), so the first genuine schema drift in a
live store turns CI red for a data condition rather than a code defect — and the previous fix for
that same problem was reverted for that reason.

My round-2 wording invited this: I wrote "have the selftest (or the audit's exit code) fail". The
parenthetical was the right half. **Put the fatal check on `audit()`'s exit code and run
`recorder_evidence_check.py` (no `--selftest`) as its own CI step.** The selftest then keeps
testing the code, the audit gates the data, and neither pretends to be the other.

Minor, same block: the failure path prints `RECORDER EVIDENCE SELFTEST: PASS (10 checks)` and then
the `FATAL DECLARATION DRIFT` lines before returning 1. An operator reading the tail of that log
sees PASS on a failing run. Move the summary line below the drift check.

---

## Corrections to rounds 1 and 2

**Both earlier documents are dated 2026-08-10. They were written on 2026-08-11.** The filenames
carry the wrong date too. I have not renamed them, because the parallel session is evidently
working from them and renaming would break those references; a correction note has been added
inside each instead.

**Round 2, finding 6 — two errors.** I reported the recorder fleet as stopping "~12 hours before
this scan" and listed `crossing_recorder_hf` as the only one advancing, at
`2026-08-10T19:39:39`. Re-measured at 2026-08-11 05:30 UTC:

    crossing_recorder_hf.py   ADVANCING   age 0.11 h   newest 2026-08-11T05:24:28   rows 32,579
    multi_venue_recorder.py   STALLED     age 20.82 h  newest 2026-08-10T08:41:49
    binance_l2_recorder.py    STALLED     age 20.82 h  newest 2026-08-10T08:41:51

The stalled fleet is **~20.8 hours** stale, not 12. And `crossing_recorder_hf` is genuinely live,
writing within the last seven minutes — it was advancing then too; I misread a stale reading as
near-threshold. The substance of that finding (eight recorders stopped together around 08:41 on
08-10, one still running) stands; the elapsed figure was wrong.

---

## Checked, no defect

- **`head_health.py` behaviour when the column is missing.** Refuses with an explicit blocker
  rather than pooling unattributed rows (`:148-153`). Correct, and the reason finding 1 is an
  operational blocker rather than a silent-corruption bug.
- **The updated recorder selftests.** Both pass, exit 0:
  `recorder_evidence_check --selftest` (10 checks), `recorder_health --selftest` (29 checks). The
  `received_ts_ms` fix resolves correctly — `binance_l2_recorder` now returns `STALLED` with a real
  age of 20.82 h and 519,706 rows, instead of `SCHEMA_DRIFT`.
- **The converted REPLACE writers.** `shadow_signals`, `round_settlement_truth`,
  `settlement_checkpoint` and `pm_round_settlements` no longer carry any REPLACE statement.
- **`price_to_beat` integrity across all four analytics stores.** `resolved` and `actual_price`
  present in every one, including both V3-era archives.

## Suggested order

1. **Finding 1** — blocks head certification and strands two weeks of champion evidence. Needs a
   migration and a merge decision, not a patch.
2. **Finding 3** — move the drift gate from the selftest onto the audit's exit code, before the
   first real drift turns CI red for the wrong reason.
3. **Finding 2** — derive `TERMINAL_TABLES` from the schema; add `shadow_orders` and
   `predictions_5m` now, exempt `hf_crossing_events` explicitly.

Findings 2 and 3 are hygiene on gates that currently work. Finding 1 is the only one that changes
what evidence a retrain can see.

---

## Resolution recorded after the scan

- **Finding 2 fixed:** the terminal-outcome contract now parses production `CREATE TABLE`
  statements, requires every terminal-looking schema to be protected or explicitly exempt, and
  covers all `predictions_{tf}m` horizons by pattern. The only exemptions are the immutable
  crossing-event `move` and intentionally idempotent offline replay rows.
- **Finding 3 fixed:** `--selftest` no longer opens live stores. The normal evidence-audit command
  is registered separately in Linux CI, Windows CI and `start.bat`, and it alone exits nonzero for
  schema/unit drift. Its final offline run found ten valid schemas and ten STALLED recorders.
- **Finding 1 handled fail-closed:** normal startup applies the additive identity-column migration.
  The non-canonical rows are not silently merged because they lack reliable serving-artifact
  identity; newly attributed forward evidence starts at zero for release authority.
