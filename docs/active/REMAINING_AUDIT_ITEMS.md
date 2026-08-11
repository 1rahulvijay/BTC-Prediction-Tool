# Remaining audit items — handoff

Written 2026-08-10 at `9478850`. Everything below was **verified present**, not inferred from
an audit. Line numbers are from that commit; re-confirm before editing.

## How work here is expected to land

Every fix in this batch followed the same loop, and the loop is the point — several of these
defects existed *because* an earlier fix was never mutation-tested:

1. Measure the defect on real data or a faithful fixture before changing anything.
2. Fix it.
3. Write a test that **executes the shipped arithmetic** — lift expressions from source via
   AST rather than recomputing them in the test. A copy in the test only proves the copy.
4. Mutation-test against a **verified-passing baseline**: assert `rc == 0` before mutating,
   or a broken baseline reports fake catches.
5. Register the test in **both** blocks of `.github/workflows/invariants.yml` (the
   `invariants` job *and* the `startbat` script). An unregistered test never runs.
6. `python backend/audit/current_state.py` to refresh SOURCE_STATE, then
   `python backend/run_ci_locally.py` — the only real gate; GitHub Actions has never run.
7. Commit with the tree clean. `code_dirty != False` makes every artifact refuse.

Recurring traps that cost time this session, all real:

- **Substring checks match your own prose.** A docstring quoting `INSERT OR REPLACE` failed a
  scan for `INSERT OR REPLACE`. Use AST.
- **Assertions that pass for the wrong reason.** A 15m-horizon denial was actually firing on
  `ARTIFACT_MISMATCH` because an earlier fixture had rewritten the artifact.
- **Fixtures that never reach the code.** A champion fixture without `current_price` returned
  at a feed-staleness guard 20 lines above the change under test.
- **Bash heredocs collapse `\n`.** Write patch scripts to a file, or use the Edit tool.

## Resolved in the 2026-08-10 core validation pass

- Terminal prediction evidence is immutable across duplicate logging. All former
  `INSERT OR REPLACE` writers that could erase resolution fields now use guarded upserts, and
  `test_terminal_outcome_relogging.py` executes the write-resolve-rewrite sequence in DuckDB.
- Head health is artifact-, horizon-, region-, and evidence-time-bound. Old model rows, stale
  outcomes, pooled horizons, and pooled operating regions cannot authorize a current head.
- Signed quantiles use purged train/calibration/test partitions and report coverage only on the
  untouched test. Magnitude quantiles purge the horizon, gate every advertised quantile, and
  both serving/reporting paths project independent quantiles into monotone order.
- All versioned head trainers resolve `BTC_MODEL_TRAINING_DAYS` through one canonical resolver.
  Import or missing-version failures are explicit instead of silently becoming legacy skips.
- Binance paper capital starvation now uses the actual minimum order margin rather than treating
  a maximum-notional ceiling as a minimum. Sub-minimum orders are rejected and executable-price
  maintenance liquidation is enforced.
- Perpetual CVD rejects duplicate aggregate-trade IDs and late older-minute messages. Bullish and
  bearish liquidity-sweep timing no longer share one break timestamp.
- A/B promotion resamples paired UTC-day clusters and scopes paired outcomes plus economic
  results to exact model bundle IDs. Reusing a label cannot inherit an older model's evidence.
- Challenger promotion rejects health reports with missing, future, or stale outcome timestamps.
- The specialist probability-bucket audit defines its verified loader before execution and exits
  nonzero instead of publishing a partial report when any expected head fails.

Every item above is registered in both CI execution paths and the `start.bat` launch selftests.

## Resolved in the second core sweep

- Recorder health uses Binance L2's actual receive timestamp and the real evidence audit fails on
  schema/unit drift. Launcher parsing excludes audit and selftest references.
- The all-time accuracy cache stamps freshness only after a successful query and exposes stale/
  unavailable state. DuckDB's anchor connection now uses the retrying path it documents.
- Model liquidity and volatility controls fail closed on missing/non-finite inputs and bind their
  ordinals from `FEATURE_NAMES`.
- Deterministic execution, model-bundle, training-integrity, complete-trade and recorder-schema
  tests that previously never ran are now in every automatic gate. Destructive/large-data drills
  remain explicitly manual.
- `binance_paper/types.py` was renamed to `paper_types.py`; paper-position persistence now lists
  every field explicitly, so neither stdlib shadowing nor dataclass field order can corrupt it.
- Terminal evidence tables no longer use REPLACE. The fence recognizes dynamic f-strings,
  no-column forms and bound parameters by prohibiting REPLACE on terminal tables altogether.
- Exact Polymarket settlement truth, checkpoints and official settlement rows are first-write
  immutable. Shadow signal re-logging can update only unresolved rows.
- Canonical analytics/execution additive migrations were backed up, applied and schema-verified.
  Binance L2 synchronization-time gaps now persist one terminal forensic row and increment the
  session gap count exactly once.

## Open items

The round-3 gate findings are resolved: terminal-table coverage is schema-derived with reviewed
exemptions, and recorder code selftests are independent from the separately fatal live evidence
audit. The canonical database migration remains intentionally additive at normal startup;
unattributed history is not copied from the non-canonical store into certification populations.

The migration has now been run against the canonical stores after hash-verified backups. Existing
unattributed rows remain excluded from authority, as designed. The sequential non-canonical
segment remains preserved as an archive rather than being silently merged into live certification
tables.

### 1. Per-head source provenance still needs trainer-owned manifests

`train_heads.py` now fails explicitly on trainer import/version errors and uses the canonical
training-window namespace, but it still starts from the shared research-matrix identity when it
stamps every specialist artifact. That is exact for matrix-trained heads; it is not exact for
`persistence_dataset.parquet`, round-state parquets, or champion-meta DuckDB joins.

This cannot be fixed honestly by guessing file names in the orchestrator. Each trainer must emit
an atomic source manifest for the rows it actually consumed, including source hashes, query/table
identity, row/time bounds, split boundaries, and dependency-artifact hashes. The orchestrator must
then validate that trainer-owned manifest before stamping or promotion. Until that exists, these
non-matrix heads remain provenance-limited and must not gain authority solely from the generic
manifest.

### 2. Snapshot-derived queue/spoof telemetry is not event-level truth

Queue add/cancel, replenishment, and spoof estimates are derived from `depth20` snapshots. A
snapshot delta cannot distinguish cancellation from fills outside the observed trade stream and
cannot establish spoof intent. These fields are pruned from the active 63-feature ensemble and are
research/display telemetry only. Promoting them requires sequenced L2 updates, gap detection,
trade reconciliation, and a new feature-semantics version.

### Historical items retained below for audit provenance

### 3. Five `INSERT OR REPLACE` statements can still destroy terminal outcomes (resolved)
`backend/database.py` — `kronos_predictions`, `model_predictions` (×2), `forward_ev_ledger`,
`fsr_ppo_decisions`, `ab_results`. Each named a terminal column and hard-coded it to
`FALSE`/`NULL`, so re-inserting an id reset that named terminal state. DuckDB preserves omitted
columns when a REPLACE has an explicit column list; the observed damage signature was therefore
`resolved=FALSE` while outcome fields could remain populated, not blank outcomes.

Fenced by `backend/test_terminal_outcomes_not_replaceable.py`: REPLACE is now forbidden for every
terminal evidence table, including dynamic f-strings and no-column statements. `KNOWN_UNFIXED`
is empty and may not grow.

Pattern to copy: `log_price_to_beat` in `database.py`, converted to
`INSERT ... ON CONFLICT (id) DO UPDATE SET <prediction columns only> WHERE <table>.resolved =
FALSE`. Note the SET clause is **whitelisted** in its test, not blacklisted — a blacklist
missed `actual_direction = NULL`.

### 4. Head-health aggregates rows it cannot attribute (resolved)
`backend/monitoring/head_health.py` queries `round_state_snapshots` with no filter on which
artifact produced each row, then stamps the report with whatever is serving now. Model B can
inherit model A's history — the inheritance bug the artifact binding exists to prevent, one
layer down.

`head_identity_json` **is declared** on that table (`database.py:811`) and populated from
`price_to_beat._active_head_identity()`, which now records the sha captured at
deserialization. But the column is **absent from the live canonical DB** — the migration never
ran there while `DB_PATH` resolved to the other store. Check it exists before relying on it.

Filter by the per-row sha; a newly trained artifact should then read `n=0` →
`INSUFFICIENT_DATA` naturally.

### 5. Health freshness measures the file, not the evidence (resolved)
`head_permissions._load()` uses `os.path.getmtime(REPORT)` against a 14-day bar. Re-running
health against a database whose newest outcome is three weeks old produces a "fresh" report;
so does touching the file. Record `evidence_last_ts` per block and gate on
`now - evidence_last_ts`.

### 6. Operating region is measured but never enforced (resolved)
`head_health.run()` now measures 15-30/30-60/60-90/90-120s separately (`OPERATING_REGIONS`),
but `head_state()` selects only artifact + horizon and never reads `by_region`. A head strong
at 20s can price at 100s. Add `seconds_left` to the permission key with **no fallback** to the
pooled horizon — the horizon binding is the pattern to copy (`HORIZON_UNMEASURED` denies
rather than falling back).

### 7. Execution-side batch (examined)
Confirmed and fixed: capital governor ceiling/minimum inversion, maintenance liquidation,
direction-shared sweep state, perpetual CVD monotonicity/deduplication, overlapping-row A/B
bootstrap, magnitude-tail gating, and signed-CQR evaluation leakage. Stop/target checks already
used executable bid/ask and post-fill geometry and required no change. Queue/spoof limitations are
documented above and stay outside active model authority.

## Not blocking a retrain

None of the above changes what a model learns. The artifact-corrupting defects found this
session are fixed: both P(Hold) leaks (same-minute feature join, outcome-end split overlap),
keeper and path purges, the keeper target definition, and the selectivity target. These affect
how much to trust the numbers afterward and how the paper lane behaves.

## Corrections to the audits, for whoever reads them next

- `head_identity_json` **is** declared on `round_state_snapshots`; it is missing from the live
  DB, which is a different problem with a different fix.
- The selectivity findings named `train_selectivity_model.py` and `_v2.py`; both are unused
  siblings that already took a train-only cut. The wired trainer is
  `backend/decision/train_selectivity_models.py`.
- Keeper target thresholds were rated P0; measured on the live 1,440,000-row matrix the effect
  was **+0.79% on p75 and 0.26% of labels**. Fixed anyway because severity scales with
  `BTC_TRAIN_SPLIT_FRAC`, which goes to 0.50.
- "Keeper tier boundaries are in-sample" was correct, but the recommended out-of-fold fix
  measured **worse** (t3 fired 17.9% vs 11.0% against a 10% nominal). It was shipped, measured,
  and reverted. `test_keeper_head_purge.py` re-measures both bases so the same argument cannot
  re-apply the same regression.

---

## Pre-retrain blockers (added 2026-08-10, from the `3c78352` scan)

Four items the latest scan calls blockers **before** the 1,000-day build, because each changes
what a trained artifact is or how it is identified. NOT yet verified by me — confirm each
before editing, the way every other entry here was confirmed.

- **R1 — signed quantiles.** `train_signed_quantiles.py` fits q10/q50/q90 on the first 98%,
  estimates the CQR widening on the last 2%, and reports `cov80_cqr` on that same 2%. The
  coverage is ~80% by construction. Needs train → purge → calibration → purge → untouched
  test, with the widening frozen on calibration and coverage measured only on test.
- **R2 — magnitude quantiles.** `train_magnitude_quantiles.py` saves the whole head when only
  q50 pinball beats a constant baseline, so q10/q90 can be useless and still serve the bands
  used for stops and expected range. Gate each quantile against its own unconditional
  baseline; add the horizon purge; guarantee q10 <= q50 <= q90 per served row or
  monotone-project.
- **R3 — per-head provenance (resolved 2026-08-11).** `train_heads.py` now requires an
  executed-source receipt. File-backed heads attest exact source bytes; dynamically assembled
  archive heads attest aligned in-memory feature/label rows; champion-meta attests its exact
  joined frame. Source mutation, missing receipts and trainer import/version failures block
  publication.
- **R4 — training-window namespace.** Keeper version tags derive from `BTC_HISTORICAL_DAYS` /
  `BTC_BACKFILL_DAYS` rather than `BTC_MODEL_TRAINING_DAYS`. Harmless when all three are 1000,
  but `start_instant.bat` deliberately sets `BTC_HISTORICAL_DAYS=3` with
  `BTC_MODEL_TRAINING_DAYS=1000`, so identity becomes ambiguous. Small; do it now.

R1 and R2 are the same class as the purges already fixed in the keeper and path heads —
`test_path_head_purge.py` is the closest pattern to copy.

**Retrain hygiene the scan recommends and I agree with:** freeze the commit SHA, train into a
staging/challenger directory, never overwrite `saved_models` during the run, preserve the
untouched holdouts, write manifests atomically.

---

## STATE AT HANDOFF — read this before trusting anything above

Written at `7077bc0`. **A parallel session is working this repo and its changes are
UNCOMMITTED in the tree right now.** Reconcile before editing; never clobber.

Uncommitted when this was written (`git status --porcelain`):

    .github/workflows/invariants.yml
    backend/ab_testing.py
    backend/binance_paper/config.py
    backend/binance_paper/governor.py
    backend/binance_paper/portfolio.py
    backend/binance_paper/risk_engine.py
    backend/binance_paper/service.py
    backend/binance_paper/test_strategy_economics.py
    backend/test_terminal_outcomes_not_replaceable.py

That maps onto section 5 above (governor max/min, maintenance-margin liquidation, A/B
bootstrap) plus section 1. **`KNOWN_UNFIXED` is now an empty set in the working tree**, so the
other four `INSERT OR REPLACE` statements appear to have been repaired there. Section 1 above
is therefore accurate as of HEAD and probably stale in the tree — verify with

    python backend/test_terminal_outcomes_not_replaceable.py

before assuming either way. SOURCE_STATE was deliberately NOT regenerated for this append,
because doing so would hash another session's in-flight code into a state document that does
not match HEAD. Whoever commits that batch should regenerate it then.

### What is committed and verified at `7077bc0`

Local CI green, 233 checks, tree clean at commit time. Fixed and mutation-tested this session:

| area | what was wrong |
| --- | --- |
| datastore | `BTC_DATA_DIR` made the canonical declaration unreachable under start.bat |
| server startup | paper-service init sat outside the try that exists to contain it |
| champion-meta | split snapshots not rounds; no ORDER BY; gate counted rows not resolutions |
| keeper heads | no purge at either OOF site or the 98/2 boundary; target thresholds full-span |
| path head | no purge at any of three boundaries incl. the production refit |
| selectivity | USD target over the full frame; unpurged OOS; "60d" window was 1000d |
| head authority | 6 of 9 artifact filenames wrong; health outranked the registry cap |
| identity | row sha rehashed the path, not the deserialized bundle; sha was optional |
| champion-meta veto | bundle declared `release_scoped=False` and was used anyway |
| price_to_beat | `INSERT OR REPLACE` erased settled outcomes (14,368 resolved rounds) |
| forward_ev_ledger | same defect; reset `resolved_at` / `outcome_status` |
| P(Hold) | same-minute feature leak; split ignored outcome end |
| regime | transition forecast discarded probability mass on duplicate labels |
| recorder evidence | selftest asserted live DB state instead of the classifier |
| keeper identity | tagged the warm-up window, not the training window |

### Still open at `7077bc0`

R1 (signed-quantile CQR) and R2 (per-quantile magnitude gates) were resolved in the next repair
batch; R3 (per-head provenance) was resolved on 2026-08-11. Sections 2-4 above remain historical
handoff context unless a newer validation document explicitly closes them. Three audit claims did
not survive measurement in this session, so verification is not ceremony here.
