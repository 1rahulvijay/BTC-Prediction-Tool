# Repo scan findings — core app

Written 2026-08-10 against HEAD `804a108`, **with 40 uncommitted files in the tree from a
parallel session**. Every finding below was reproduced by running something, not inferred from
reading. Where a claim is measured, the command that measured it is given. Where I did not
verify, it says so.

Nothing here was fixed. No repo file was modified by this scan except this document.

> Date correction (2026-08-11): this scan ran on **2026-08-11**, not 2026-08-10. The filename
> carries the wrong date and is left unrenamed only because other work already references it.

> Resolution note (2026-08-11): findings 1-7 and both latent contracts in finding 8 were handled
> by the subsequent core hardening pass. Terminal-table REPLACE is now forbidden by an expanded
> AST fence; deterministic core tests are gated; risk inputs fail closed and use name-derived
> indices; Binance paper types were renamed; position persistence is explicit. Large historical
> scorecards, the destructive recorder crash drill and the full training smoke remain manual by
> design. See `CORE_APP_VALIDATION_2026-08-10.md` for executed validation.

The four probe scripts cited below live **outside the repo**, in the session scratchpad:

    C:\Users\rahul\AppData\Local\Temp\claude\C--Users-rahul-OneDrive-Documents-BTC-Prediction-Tool\
      64e4879b-9b88-4472-8678-1e2ad116fb93\scratchpad\
        probe_fence_gap.py               finding 1
        probe_duckdb_replace_semantics.py  findings 1, 2
        probe_dead_tests_final.py        finding 3
        probe_feature_indices.py         finding 8

That directory is session-scoped and will not survive. Move them into the repo if any of these
findings is worked, so the measurement can be re-run against the fix.

A second pass covering recorder health, `server.py` TTL caches, DB boot and the frontend is in
`REPO_SCAN_FINDINGS_2026-08-10_ROUND2.md`. No overlap with this file.

## Scope and method

Scanned `backend/` (570 modules, ~160k lines) plus the gate configuration. Defect classes swept:
terminal-outcome destruction, look-ahead leakage, silent fallbacks in the serving path,
positional/ordinal binding, test-gate coverage, module shadowing.

Three candidate findings **did not survive measurement** and were dropped rather than written up.
They are recorded under "Checked, no defect" because knowing they were checked is worth as much as
the findings — this is the same discipline the audit corrections section of
`REMAINING_AUDIT_ITEMS.md` records.

---

## 1. The terminal-outcome fence misses the more destructive half of the bug class

`backend/test_terminal_outcomes_not_replaceable.py` enforces the `log_price_to_beat` rule
structurally. Its detector (`violations()`, lines 56-85) requires **all** of:

- the SQL to be an `ast.Constant` (a plain string literal),
- a `(col, col, ...) VALUES` form — `cols` and `vals` regexes both must match,
- a literal `FALSE` or `NULL` inside the `VALUES (...)` text.

Any writer failing one of those is invisible to it. Measured against six statements that are each
exactly as destructive as the original defect:

    detector saw 3 of 6 equivalent defects

      CAUGHT  literal FALSE in VALUES
      MISSED  bound parameter  (VALUES (?,?,?) with False in the params list)
      CAUGHT  NULL on a named terminal column
      CAUGHT  multi-row VALUES
      MISSED  no column list   (INSERT OR REPLACE INTO t VALUES (?,?,?))
      MISSED  f-string SQL     (ast.JoinedStr, not ast.Constant)

This matters more than a coverage gap, because of what the two forms actually do. Measured on
DuckDB 1.4.4 and SQLite 3.45.3, same table, same settled row:

| engine | statement form | unnamed columns after REPLACE |
| --- | --- | --- |
| DuckDB | `(cols) VALUES` | **preserved** (`actual_price` survived) |
| DuckDB | no column list | **wiped to NULL** |
| SQLite | `(cols) VALUES` | wiped to NULL |

DuckDB's `INSERT OR REPLACE` with a column list behaves as `ON CONFLICT DO UPDATE SET <named
columns>` — it touches only what is named. Without a column list it rewrites the whole row.

**So the detector catches the bounded form and misses the unbounded one.** Its detection is
inverted relative to blast radius on the engine this repo actually uses.

Real writers sitting in the blind spot today:

- `backend/decision/shadow_store.py:110` and `:132` — f-string SQL, `shadow_signals`
- `backend/polymarket/live_btc_updown_recorder.py:794`, `:803`, `:870` — no column list, on
  `settlement_checkpoint`, `pm_round_truth_attempts`, `pm_round_settlements`
- `backend/binance_paper/store.py:280` — no column list, `paper_equity_snapshots`

`KNOWN_UNFIXED` is an empty set and the suite reports green. That green is narrower than it reads.

**Not yet established:** whether any of those writers can *currently* destroy a settled row in
practice. `_persist_round_truth` (`live_btc_updown_recorder.py:714`) has no
"already ADMISSIBLE, skip" guard and REPLACEs unconditionally at `:767`, but both quarantine paths
return early (`:731`, `:749`), so a later evidence-less re-attempt cannot erase an existing truth
row. Re-deriving a round whose reference prices changed is the untested path. Measure before
treating it as live.

Suggested shape of the fix, consistent with the existing pattern: detect on the **statement**, not
the literal — walk `ast.JoinedStr` as well as `ast.Constant`, treat a missing column list as
"names every column", and resolve bound parameters to their call-site values where they are
constants. Then re-derive `KNOWN_UNFIXED` from what that finds.

Reproduce: `scratchpad/probe_fence_gap.py`, `scratchpad/probe_duckdb_replace_semantics.py`.

## 2. The recorded blast radius of the original defect is wrong for DuckDB

Both `test_terminal_outcomes_not_replaceable.py:4-9` and `REMAINING_AUDIT_ITEMS.md` state that the
twelve columns `log_price_to_beat` omitted — `actual_price`, `actual_direction`, `hit`, `move`,
`settlement_source` — "reverted to their defaults".

Measured above: on DuckDB with a column list, **omitted columns are preserved**. The damage from
that statement was `resolved` being driven back to `FALSE` — a column it *named* — leaving rows
that still carried their outcome data while reporting themselves unresolved.

That is still a real defect and the fix was still correct. But it changes what to look for when
auditing the live archive: the signature is `resolved = FALSE` on rows that have a non-null
`actual_price`, not rows with their outcome fields blanked. Anyone who greps for blanked outcome
columns to size the damage will conclude, wrongly, that nothing happened.

This is the fourth audit claim in this repo to not survive measurement. Worth adding to the
corrections list in `REMAINING_AUDIT_ITEMS.md` when that file is next touched — I did not edit it,
because the parallel session has it checked out.

## 3. 39 backend selftests never execute; 19 are core app

A test runs only if a gate names it (`invariants.yml` / `start.bat`) or pytest collects it. pytest
collects only module-level `test_*` **functions**; a script-style file whose assertions live in
`main()` collects zero tests and exits 0 — the trap `pytest.ini:13-18` already documents.

Cross-checked the gate files against the real output of `python -m pytest --collect-only -q` (the
exact command `invariants.yml:825` runs; it collects 155 tests from 31 files, no errors):

    backend test_*.py files    : 139
    NEVER executed by any gate : 39   (19 core app, 20 research)

Core app:

    backend/binance_paper/test_engine.py
    backend/binance_paper/test_model_consensus_probability_namespace.py
    backend/binance_paper/test_period_loss_boundaries.py
    backend/binance_paper/test_post_fill_risk_budget.py
    backend/binance_paper/test_sizing_exit_cost.py
    backend/quant_platform/test_kernel.py
    backend/quant_platform/test_research_validation.py
    backend/test_5m_15m_30d.py
    backend/test_algodesk_17_agents_30d.py
    backend/test_crossing_recorder_crash_recovery.py
    backend/test_deadfeatures_30d.py
    backend/test_meta_model_contract.py
    backend/test_model_bundle_completeness.py
    backend/test_train_smoke_end_to_end.py
    backend/test_training_integrity_20260731.py
    backend/trade_forecast/test_complete_trade_forecast.py
    backend/trade_forecast/test_evidence_completion.py
    backend/trade_forecast/test_ledger_v2_end_to_end.py
    backend/trade_forecast/test_serving_integration.py

The concentration matters more than the count. **Every `binance_paper` selftest except
`test_strategy_economics.py` is in this list** — that is the execution lane the 2026-08-10 pass
changed (capital-governor ceiling/minimum inversion, maintenance-margin liquidation). The fixes
landed; the tests that hold them in place are not wired to anything.

`test_model_bundle_completeness.py` (12 assertions) and `test_meta_model_contract.py` (6) are
likewise unenforced artifact contracts.

**No active regression is hiding here.** I ran all of them; every one passes when invoked
correctly. The finding is that 19 contracts currently provide zero protection, not that something
is broken behind them.

Reproduce: `scratchpad/probe_dead_tests_final.py`.

## 4. `backend/binance_paper/types.py` shadows the stdlib `types` module

Running any script from that directory puts the directory first on `sys.path`, so `import types`
resolves to the local file and the interpreter fails while importing `enum`:

    $ python backend/binance_paper/test_engine.py
    ImportError: cannot import name 'MappingProxyType' from 'types' (consider renaming
    'backend\binance_paper\types.py' since it has the same name as the standard library
    module named 'types')

All five `binance_paper` selftests fail this way as scripts, and all five pass under `-m`:

    python -m backend.binance_paper.test_engine                  rc=0
    python -m backend.binance_paper.test_post_fill_risk_budget   rc=0
    python -m backend.binance_paper.test_period_loss_boundaries  rc=0
    python -m backend.binance_paper.test_sizing_exit_cost        rc=0
    python -m backend.binance_paper.test_model_consensus_probability_namespace  rc=0

This is a standing hazard for anything later added to that directory, not just tests. Renaming the
module to `paper_types.py` removes the class of failure; registering with `-m` works around it.

## 5. Registration is not uniform — the two halves need opposite invocations

Relevant to whoever fixes finding 3, because it is not a copy-paste job:

- `binance_paper/*` tests work **only** under `python -m ...` (finding 4).
- `test_meta_model_contract.py` and `test_model_bundle_completeness.py` work **only** as scripts —
  they use flat imports (`import model`, `import meta_model`) that resolve when `backend/` is the
  script directory. Under `-m` both fail with `ModuleNotFoundError`.

Registering either half in the wrong form produces a green line that ran nothing, which is the
failure mode `run_ci_locally.py:88-90` exists to prevent.

## 6. `model.py:2485` fabricates a liquidity input instead of refusing

```python
try:
    spread_norm = float(seq[-1, 15])
    vacuum = float(seq[-1, 56])
except Exception:
    spread_norm = 0.5
    vacuum = 0.0
liquidity_score = max(0.0, min(1.0, 1.0 - spread_norm))
```

`_signal_quality` produces `tradeability` and the A+/A/B grade that decides whether a signal is
actionable. On any failure reading the sequence — short feature vector, `None`, dtype problem —
it substitutes a mid-range `0.5`, yielding `liquidity_score = 0.5` and a grade that looks
ordinary. No log line, no denial, no marker on the output.

The conditions that trigger it are exactly the ones where the grade should be refused. Everywhere
else in this codebase an unmeasured input denies (`HORIZON_UNMEASURED`, `INSUFFICIENT_DATA`); here
it is silently invented.

## 7. `model.py:2860` lets a risk control switch itself off silently

```python
if seq.shape[1] > 50:
    vol_accel = seq[-1, 49]
    ...
    if agreement < agreement_threshold and (vol_accel > 0.3 or spread > 0.8):
        direction = "NEUTRAL"
```

The meta-model trust filter — the branch that forces `NEUTRAL` on low agreement in fast or wide
markets — and the EWMA-vol confidence scaling both sit inside that guard. A feature vector with 50
or fewer columns skips both. There is no `else`, no log, no flag on the result: the model returns a
directional call at full confidence, and nothing records that the safety filter did not run.

Findings 6 and 7 are the same shape: degrade quietly rather than refuse. Both are in
`_signal_quality`/`_finalize`, both are in the live serving path, and neither is in the dirty set,
so they are accurate as of HEAD.

## 8. Latent, not live — no action needed yet, but nothing pins them

Recorded so they are not re-discovered as emergencies later. Both are currently **correct**;
neither has a test binding it.

- **Positional feature indices.** `model.py` reads `seq[-1, 15]`, `[49]`, `[50]`, `[56]` by literal
  index (lines 2486, 2487, 2861, 2862, 2863) while `features.py:564` holds the canonical
  `FEATURE_NAMES` (136 entries). Checked all four against it: `15=spread_norm`,
  `49=vol_acceleration`, `50=ewma_vol`, `56=vacuum_detected` — all match today. A reordering of
  `FEATURE_NAMES` would silently repoint them at different features with **no exception raised**,
  which is worse than the crash path in finding 6.
- **`asdict()` bound positionally.** `binance_paper/store.py:213` and `:269` pass
  `list(asdict(position).values())` into a hand-written 9-column list. Field order in
  `PositionState` (`types.py:115-124`) matches the SQL column order exactly today. Adding or
  reordering a dataclass field silently shifts every subsequent value into the wrong column.

Reproduce: `scratchpad/probe_feature_indices.py`.

---

## Checked, no defect — do not re-open without new evidence

- **Label leakage into the feature set.** `make_labels` (`build_research_matrix.py:443-465`) writes
  `ret_5m` — pure future information, `close[t+5] - close[t]` — into the matrix parquet under a
  name that the `future_` prefix filter does not catch. That filter (`:191`) turns out to be inside
  `_monthly_quality_report`, a data-quality report, **not** the training feature selector. The
  wired trainers select by explicit allowlist (`SELECTIVITY_FEATURES` etc. in
  `decision/train_selectivity_models.py`; `FEATURE_COLUMNS` in `trade_forecast/`), so `ret_5m`
  never reaches a model. No leak.
- **Advisory gates in the `startbat` job.** `run_ci_locally.py:114-115` downgrades any `startbat`
  command lacking `|| exit /b 1` to advisory. Counted them: 97 test invocations, **97 guarded, 0
  advisory**. No hole.
- **`shadow_signals` re-log destroying outcomes.** `log_shadow_signal` names 18 of 26 columns and
  omits `resolved`, `realized_pnl_bps`, the MFE/MAE fields. Structurally identical to the
  `price_to_beat` defect — but run against DuckDB through the shipped writer, in the
  write → resolve → rewrite sequence, the outcome is **preserved** (finding 1's table explains
  why). It is still in the fence's blind spot, and it would destroy data verbatim on SQLite, but it
  is not a live defect today.
- **pytest collection health.** `python -m pytest --collect-only -q` from the repo root: 155 tests,
  0 collection errors. (Naming those files individually on the command line *does* produce import
  errors, but that is not how any gate invokes them, so it is not a repo defect.)

---

## Suggested order

1. **Finding 3 + 5** — registration, `binance_paper` first. Cheapest, and it is the lane whose
   fixes are newest and least protected. Watch the invocation form per file.
2. **Finding 1** — widen the fence detector, then re-derive `KNOWN_UNFIXED` from what it finds.
   Expect the list to stop being empty; that is the point.
3. **Findings 6 and 7** — make the serving path refuse instead of inventing. Small, contained.
4. **Finding 2** — one paragraph into the corrections section of `REMAINING_AUDIT_ITEMS.md`, once
   the parallel session's changes are committed.
5. **Finding 4** — rename `types.py`, or accept `-m` registration as the permanent answer.
6. **Finding 8** — a contract test each, whenever those files are next opened.

None of this changes what a model learns. Findings 6 and 7 change what the live app *serves*;
finding 3 changes how much the green suite is worth.
