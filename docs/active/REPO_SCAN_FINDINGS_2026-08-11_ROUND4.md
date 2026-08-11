# Repo scan findings — round 4 (datastore topology, L2 gap forensics)

Written **2026-08-11 ~06:00 UTC** against HEAD `804a108`, tree carrying 61 uncommitted files from
the parallel session.

Fourth pass. Rounds 1-3 are in `REPO_SCAN_FINDINGS_2026-08-10.md`,
`..._2026-08-10_ROUND2.md`, `..._2026-08-11_ROUND3.md`.

This round does two things: it **corrects round 3's central claim**, which was wrong in a way that
would have cost real evidence if acted on, and it closes the `l2_gaps` question rounds 2 and 3
both flagged and neither answered.

All measurements read-only. No repo file written except this document; no store was modified.

Probe scripts (session scratchpad):

    probe_split_store_drift.py     finding 2
    probe_label_loss.py            findings 1, 4
    probe_label_overlap.py         finding 1
    probe_id_scheme.py             finding 1
    probe_coverage_hole.py         findings 1, 3

---

## 0. CORRECTION to round 3 — the stores are sequential segments, not rival copies

Round 3, finding 1, said roughly two weeks of evidence was "stranded" in the non-canonical store
and framed the canonical store as the stale one. **That framing was wrong.** It was built on row
counts and newest-timestamps without checking whether the two stores describe the same rounds.
They do not.

Measured `price_to_beat` daily coverage in both stores:

    day           canonical  non-canonical
    2026-06-12            0            765
    ...                   0        1,400-2,500/day
    2026-07-04            0             98
    2026-07-05          648              0   <- handover
    2026-07-06          762              0
    ...                 650-760          0
    2026-07-25          468              0
    2026-08-06            0              7
    2026-08-08            0            ...

    days with data in BOTH stores : 0
    days canonical only           : 21
    days non-canonical only       : 19

Zero overlap by round `id`, zero by `(timestamp, horizon)`, zero by calendar day. The two files
are **consecutive segments of one timeline** with a clean handover on 2026-07-05, not two copies
competing for the same period. The id conventions differ across the boundary too — canonical
carries `ptb_binance_15m_<ts>` alongside `ptb_5m_<ts>`; the non-canonical store uses `ptb_15m_<ts>`.

What follows from that, and what round 3 got backwards:

- **A union is safe.** No double counting is possible — 14,368 + 19,120 = 33,488 distinct resolved
  rounds. Merging is not a risk to weigh; it is what is required to see the whole history.
- **The canonical store is the richer one for certification, not the poorer.** Officially settled
  rounds: **6,725 canonical vs 693 non-canonical**. `head_health` filters on
  `settlement_source LIKE 'official:%'`, so 91% of all official evidence that exists is in the
  canonical window. Round 3 implied switching to canonical loses evidence; for official settlement
  the opposite is true.
- **The row-count comparisons in round 3's table were real but did not mean what I said.** Canonical
  holds *more* `champion_snapshots` (186,985 vs 115,193), not fewer.

Round 3's actual defect finding — `head_identity_json` absent from the canonical store, so
`head_health` refuses to certify any head against it — **still stands unchanged**. That is a
migration that has not run. What was wrong was the surrounding story about stranded data.

Also resolved: round 3 flagged as odd that `round_state_snapshots` and `champion_snapshots` both
report exactly 186,985 rows. Consistent with a single writer emitting one row to each per cycle
across a continuously recorded window. Not evidence of a bulk import.

## 1. An 11-day hole in `price_to_beat` spanning both stores

Falls out of the same coverage table, and neither store shows it on its own.

    last canonical day     : 2026-07-25   (468 rounds)
    next day with any data : 2026-08-06   (7 rounds, non-canonical)

**Between 2026-07-26 and 2026-08-05 inclusive, neither store holds a single `price_to_beat`
round.** Eleven days. Recording resumes on 08-06 at 7 rounds and 08-08, against a normal rate of
650-760/day in the canonical window.

Smaller holes exist in the non-canonical segment too: 2026-06-17, 06-24 to 06-26, and 07-02 have
no rows.

This is not a code defect and may be entirely known — a deliberate stand-down, a migration window.
It is recorded because a training window specified as "last 1000 days" or "since June" silently
spans this hole, and because the low-volume tail (7 rounds on 08-06) is the kind of partial day
that skews a per-day cluster bootstrap. Worth an explicit decision before the retrain, not a
discovery afterwards.

## 2. Switching to the canonical store loses schema, not just rows

Round 3 checked one column. Comparing full schemas across every store present in both roots shows
the canonical files are consistently the **older schema**, missing columns that the non-canonical
ones have.

`analytics.duckdb` — columns present in non-canonical, absent from canonical:

| table | missing from canonical |
| --- | --- |
| `predictions_5m`, `predictions_15m` | `actual_direction`, `resolution_status`, `resolution_basis`, `resolution_price`, `resolution_event_ts`, `target_contract`, `label_version`, `neutral_band`, `release_id`, `invalid_reason` |
| `price_to_beat` | `grade_usable`, `grading_contract`, `horizon_overlap`, `pred_contract` |
| `ab_results`, `kronos_predictions`, `fsr_ppo_decisions`, `model_predictions` | `resolution_status`, `invalid_reason` |
| `round_state_snapshots`, `champion_snapshots` | `head_identity_json` |

`execution_layer.duckdb` — whole tables absent from canonical:

    pm_reference_prices        43,462 rows
    rejection_events           70,795 rows
    shadow_signals                638 rows
    pm_round_truth_attempts       481 rows
    pm_export_health                4 rows
    round_settlement_truth          0 rows
    settlement_checkpoint           0 rows
    shadow_orders                   0 rows

plus `pm_round_snapshots` missing 19 columns in canonical, including the entire book surface —
`up_ladder`, `down_ladder`, `up_b1/b2/b5`, `down_b1/b2/b5`, `up_book_hash`, `down_book_hash`,
`book_age_s`, `decision_ts`, `artifact_hash` — and `pm_round_meta` missing
`required_reference_source`, `resolution_source`, `rule_text`, `fee_rate`, `fees_enabled`.

Two things worth noting. `round_settlement_truth` and `settlement_checkpoint` — the settlement
truth tables the round-1 fence work hardened — **exist only in the non-canonical
`execution_layer.duckdb`, and hold zero rows there.** Whatever writes them has never written to
the canonical store. And `pm_round_meta` in canonical lacks `required_reference_source`, which
`_persist_round_truth` reads as the first thing it does (`live_btc_updown_recorder.py:718-723`);
against the canonical store that query would fail or return nothing, and every round would
quarantine.

The additive migrations exist in `database.py`. They have simply never been run against the
canonical files. Running `init_db()` against the canonical path is the mechanical part; deciding
whether the pre-07-05 and post-07-25 segments get merged into it is the judgement part. **I did
neither** — writing to the live archive while another session works is not mine to do.

## 3. `l2_gaps` has never recorded a row, and cannot, on the path that actually fires

Rounds 2 and 3 both flagged `l2_gaps` as empty and explicitly declined to conclude. Answering it
now: it is a real defect, and the empty table is the symptom rather than the cause.

`backend/venues/binance_l2_recorder.py` has **two** origins for `BookSequenceGap`:

**Path A — instrumented.** `apply_and_record()` catches it at `:383`, writes a diff row with
`disposition="SEQUENCE_GAP"`, calls `store.gap(...)` at `:393` — which inserts into `l2_gaps` and
does `gap_count = gap_count + 1` — then re-raises.

**Path B — not instrumented.** `:467` raises `BookSequenceGap("buffer did not overlap the REST
snapshot")` directly during synchronisation, outside `apply_and_record`. It propagates to `:513`,
which calls `store.finish(session_id, "GAP", ...)`. `finish()` (`:340-365`) sets `status`,
`ended_ts_ms`, `final_update_id`, `applied_diffs`, `stale_diffs`, `error` — and touches neither
`l2_gaps` nor `gap_count`.

The live store says which path fires:

    l2_gaps rows            : 0
    sum(l2_sessions.gap_count): 0
    session status mix      : ERROR 12, GAP 3, SYNCED 1     (16 sessions)
    l2_diffs disposition    : APPLIED 519,672, STALE 34     (no SEQUENCE_GAP rows at all)

Three sessions terminated in `GAP`, and there is not one `SEQUENCE_GAP` diff, not one `l2_gaps`
row, and `gap_count` is zero. **Every gap this recorder has ever encountered went down path B.**

The consequence is not just an empty table. `gap_count` is what the recorder's own health summary
aggregates (`:643`, `COALESCE(SUM(gap_count), 0)`), so that summary reports a gap-free book stream
while three sessions died of sequence gaps. The forensic columns `l2_gaps` exists to hold —
`detected_ts_ms`, `local_update_id`, `first_update_id`, `final_update_id`, `previous_update_id`,
`reason` — are exactly what is needed to tell "the venue skipped ids" from "our buffer never
overlapped the snapshot", and they are never written for the failure that actually happens.

Fix shape: have the `:513` handler call `store.gap(...)` before `finish()`, or move the
`gap_count`/`l2_gaps` write into `finish()` when `status == "GAP"`. The second is harder to bypass.
Path B has no `event` dict to record, so the venue-supplied `U`/`u`/`pu` columns would be NULL
there — which is itself the diagnostic signal that distinguishes the two causes.

## 4. Observation — the Binance L2 recorder has essentially never completed a healthy session

From the same query, across all 16 sessions ever recorded:

    ERROR   12
    GAP      3
    SYNCED   1

with 519,672 diffs applied and 34 stale. One session in sixteen reached `SYNCED`. Combined with
round 2's finding that this recorder's health declaration was broken from the start (fixed since),
and `recorder_evidence_check`'s docstring recording that it once held zero rows despite being
CI-gated, this component has a consistent history of being wired and green while not working.

Not a code finding — I did not read the 12 `error` strings, which is the obvious next step and
cheap. Recorded so it is not mistaken for healthy on the strength of its 519,706-row store.

---

## Checked, no defect

- **`price_to_beat` outcome integrity across both stores.** Zero rows anywhere with
  `resolved = FALSE AND actual_price IS NOT NULL` — the true DuckDB signature of the
  `log_price_to_beat` defect, per round 1 finding 2. Whatever damage that statement did has been
  repaired or predates these files. Also zero disagreement on `actual_direction` for overlapping
  rows, trivially, since no rows overlap.
- **`model_metrics.duckdb`.** Only difference between the two copies is `ptb_log` freshness. No
  schema drift, no missing tables.
- **Non-canonical stores are not simply "newer".** The non-canonical `analytics.duckdb` ends
  2026-08-08 but its official-settlement count is 693 against canonical's 6,725. Neither file is
  strictly better; they are different periods with different settlement regimes.

## Suggested order

1. **Finding 2 + round 3's finding 1** — one migration pass over the canonical files brings the
   schema forward, including `head_identity_json`. Do this before any head certification.
2. **Finding 0/1** — decide explicitly what the training window is, given the segment handover on
   07-05 and the 11-day hole from 07-26. This is a decision, not a patch, and it is the one that
   most affects a retrain.
3. **Finding 3** — a few lines in the recorder; restores gap forensics and stops the health
   summary reporting zero gaps when sessions are dying of them.
4. **Finding 4** — read the 12 error strings before trusting this recorder.

Nothing in this round changes what a model learns today. Findings 0-2 change what evidence a
retrain can see, which is upstream of that.

---

## Resolution recorded after the scan

- Canonical analytics and active execution stores were copied to timestamped backups and verified
  byte-for-byte with SHA-256 before migration. The repository's additive migrations then ran and
  the missing identity, resolution, reference-source and book-surface columns were verified.
- Historical segments were not merged into certification populations. They remain preserved, but
  rows without serving-artifact identity cannot authorize a newly trained release.
- The L2 gap defect is fixed. `L2Store.gap()` is idempotent per session and the outer
  synchronization handler always calls the shared gap finalizer. The selftest exercises both the
  event-aware and event-less paths and asserts exactly one gap row/count per failed session. A
  hash-verified backup preceded reconciliation of the live archive; it now reports three GAP
  sessions, three total gap counts and three forensic rows.
- The observed ERROR sessions are transport failures (`keepalive ping timeout` and transient DNS
  lookup failures). They remain visible and reconnectable; they are not relabeled as clean market
  sessions or used as gap evidence.
