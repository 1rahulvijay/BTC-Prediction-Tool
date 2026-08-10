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

## Open items

### 1. Five `INSERT OR REPLACE` statements can still destroy terminal outcomes
`backend/database.py` — `kronos_predictions`, `model_predictions` (×2), `forward_ev_ledger`,
`fsr_ppo_decisions`, `ab_results`. Each names a terminal column and hard-codes it to
`FALSE`/`NULL`, so re-inserting an id wipes resolution exactly as `price_to_beat` did.

Fenced by `backend/test_terminal_outcomes_not_replaceable.py`: new violations fail, these five
are an explicit `KNOWN_UNFIXED` set. **Remove each entry as you fix it** — the test also fails
on stale entries, so the list cannot drift.

Pattern to copy: `log_price_to_beat` in `database.py`, converted to
`INSERT ... ON CONFLICT (id) DO UPDATE SET <prediction columns only> WHERE <table>.resolved =
FALSE`. Note the SET clause is **whitelisted** in its test, not blacklisted — a blacklist
missed `actual_direction = NULL`.

### 2. Head-health aggregates rows it cannot attribute
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

### 3. Health freshness measures the file, not the evidence
`head_permissions._load()` uses `os.path.getmtime(REPORT)` against a 14-day bar. Re-running
health against a database whose newest outcome is three weeks old produces a "fresh" report;
so does touching the file. Record `evidence_last_ts` per block and gate on
`now - evidence_last_ts`.

### 4. Operating region is measured but never enforced
`head_health.run()` now measures 15-30/30-60/60-90/90-120s separately (`OPERATING_REGIONS`),
but `head_state()` selects only artifact + horizon and never reads `by_region`. A head strong
at 20s can price at 100s. Add `seconds_left` to the permission key with **no fallback** to the
pooled horizon — the horizon binding is the pattern to copy (`HORIZON_UNMEASURED` denies
rather than falling back).

### 5. Execution-side batch (not yet examined)
Named by the second audit; **I did not verify any of these**, so confirm before fixing:
capital governor treating `max_position_notional_usd` as a minimum; no maintenance-margin
liquidation path; stop/target validated against mark rather than executable fill; liquidity
sweep sharing one `last_break` across directions; queue add/cancel estimates from top-20
depth deltas; spoof score not establishing cancellation-without-execution; perp CVD bar
rollover with no monotonicity or dedupe; A/B promotion using an iid bootstrap over overlapping
predictions; magnitude head gating q50 only; signed-quantile CQR coverage measured on its own
calibration slice.

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
