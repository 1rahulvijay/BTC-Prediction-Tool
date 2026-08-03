# Production Correctness And Retrain Handoff

Date: 2026-08-03

## Purpose

This document records the reliability and evidence corrections completed before the next
1,000-day training run. It is the current handoff for startup, model promotion, paper trading,
Protocol B/C evidence, recorder health and post-training checks.

It does not claim that a model is accurate, that a paper strategy is profitable, or that real
orders are authorized. The current model artifacts remain unavailable until the compatible
retrain completes and the promotion gates pass.

## Current Verdict

The maintained source tree passes its launcher and workflow-derived validation suites. The app is
ready to be committed and then started for retraining, recording, shadow forecasting and paper
trading.

It is not ready for capital:

- the main ensemble is still an incompatible v11 artifact while current code requires v14;
- no specialist head currently has a complete current provenance identity;
- P(Hold) calibration remains unavailable until compatible models are trained;
- Protocol B and C have not accumulated enough official forward outcomes;
- no paper strategy has passed a robust post-cost forward promotion gate;
- no real-order adapter is available or authorized.

## Implemented Corrections

### 1. Protocol C now requires realized official outcomes

`open_position_action_recorder.py` now separates three different facts:

1. a causal action snapshot was captured;
2. a provisional Pyth proxy outcome was observed;
3. an official Polymarket settlement resolved the action arms.

The five declared action arms are still valued from the same position and timestamp:

```text
HOLD, EXIT, REDUCE_50, SWITCH, LOCK
```

Immediate `settlement_floor_net` is no longer treated as proof of a resolved outcome. Official
settlement appends immutable action-arm outcomes containing gross and net value. Pyth proxy rows
may support diagnostics, but cannot make Protocol C complete.

### 2. Protocol B is wired end to end

The live recorder now stores causal crossing state and post-entry crossing outcomes. It records:

- side before and after the anchor crossing;
- crossing timestamp and distance from the anchor;
- whether the new side reverted after 5, 15, 30 and 60 seconds;
- whether the crossing was final, but only after official settlement.

Protocol B remains forward evidence. Existing historical rows are not retroactively manufactured.
The protocol is permitted to inform HOLD/REDUCE/EXIT decisions for an existing position; it does
not authorize automatic opposite-side entry.

### 3. Recorder liveness is independent of open positions

Every capture cycle now writes an `open_position_recorder_heartbeats` row, including the
`NO_OPEN_POSITIONS` case. A healthy recorder can therefore report `COLLECTING` before any strategy
enters. Liveness is no longer inferred from the presence of positions or action snapshots.

### 4. Readiness is safe while the app owns DuckDB

`bc_forward_readiness_report.py` first attempts a direct read. If the live writer owns the DuckDB
lock, it falls back to:

```text
http://127.0.0.1:8000/api/evidence-readiness
```

The endpoint is counts-and-coverage only. It does not reveal performance during a frozen evidence
window. Failure to read both sources returns an error instead of guessing a status.

### 5. Specialist heads train transactionally

`train_heads.py --transactional-live` now:

1. copies the active `saved_models` directory to a unique staging directory;
2. trains every required head into staging via `BTC_MODEL_OUTPUT_DIR`;
3. validates mandatory artifact identities as a complete bundle;
4. preserves the active bundle if any trainer or validation step fails;
5. swaps the staged directory into service only after every mandatory check passes;
6. retains a timestamped rollback bundle.

All specialist trainers and path-label builders honor `BTC_MODEL_OUTPUT_DIR`. `start.bat` always
uses transactional head training. A dry run creates and removes staging without promotion.

### 6. Main ensemble promotion is transactional and manifest-last

`model_promotion.py` validates the staged root manifest, integrity sidecars and hashes before
promotion. It backs up only main-model files, leaving specialist heads intact. Data files are
published first and the root manifest last, so the live identity never points at a partial bundle.
Handled failures attempt rollback and report any incomplete restoration.

The 98/2 flow remains:

1. train on the older 98% after purging;
2. evaluate on the recent untouched 2%;
3. compare with predeclared precision and calibration gates;
4. generate out-of-fold calibration/conformal information;
5. refit an accepted candidate on all usable 1,000-day data;
6. stage, hash-check, smoke-test and transactionally promote it with the manifest published last;
7. keep the full-data refit in live shadow verification because it has no untouched tail of its
   own.

An evaluation failure does not replace the incumbent. A promotion failure does not leave a mixed
model bundle.

### 7. Dynamic paper behavior is explicit when models are unavailable

`CHAMPION_DYNAMIC_PAPER_V1` continues to fail closed for new entries when P(Hold) is unavailable.
For an already-open paper position, it reports:

```text
STATIC_RISK_ONLY / p_hold_unavailable_static_risk_only
```

Only the predefined target and stop risk controls remain available in that state. Model
invalidation, edge-decay profit lock and last-chance model exit cannot silently claim to be active.
The frontend shows this degraded state on the Polymarket card.

The separate nine-rule `decision/event_exits.py` engine is intentionally marked
`RESEARCH_ONLY=True` and removed from the production package export. It is not silently presented
as live decision authority.

### 8. System Health now represents trust, not process existence

The UI and `/api/system-health` expose:

- canonical analytics database: `data/analytics.duckdb`;
- all required and optional recorder states;
- Protocol B/C status and eligible counts;
- main ensemble, P(Hold) and round-state model readiness;
- Polymarket and Binance paper-engine state;
- explicit `DO_NOT_TRUST` when a required feed, recorder or model prerequisite is unavailable.

Deribit options is displayed as optional. Binance paper remains off by default and is reported as
`PAPER_ENGINE_DISABLED`, not as a healthy running strategy.

### 9. Resource leaks and state ambiguity were contained

Selftests no longer use `TemporaryDirectory(ignore_cleanup_errors=True)`. DuckDB shutdown paths now
close their process-local anchor connections, so a leaked handle fails a test instead of silently
leaving another temporary directory.

`report_master_runtime_state.py` now names every missing archive stream instead of reporting only
an opaque `8/9` count.

Startup explicitly sets the canonical data directory to the repository `data` directory. The
following legacy copies were not deleted or merged because their authority cannot be inferred from
file size or timestamp alone:

- `data/btc_duckdbs/analytics.duckdb`;
- `btc_full_project/btc-tool/data/analytics.duckdb`;
- `%LOCALAPPDATA%/BTCQuantTrader/analytics.duckdb`.

Likewise, existing leaked Windows temp directories, the nested project copy and Git object storage
were not destructively cleaned. They are operator hygiene tasks, not correctness edits.

## Validation Executed

| validation | result |
|---|---|
| `start.bat` startup-validation branch | PASS; 1,000d, split 0.98, full refit enabled |
| `start.bat` self-test-only branch | PASS; all invariant groups; no server/training started |
| workflow-derived local CI | PASS; 116/116 Python steps in 349.6s |
| pytest inside CI | PASS; 86 tests |
| Python compilation and maintained static checks | PASS |
| frontend production build | PASS |
| specialist transactional dry run | PASS; no live promotion |
| main-model promotion/rollback selftest | PASS |
| Protocol B/C readiness selftest | PASS; 40 checks |
| open-position action recorder selftest | PASS; 23 checks |
| dynamic-paper degraded-mode selftest | PASS |
| paper restart/official-settlement integrity | PASS |
| Binance paper engine and typed API selftests | PASS |

The Node build passed separately. The local CI command without `--all` intentionally skipped npm
dependency installation/audit because the existing environment was used.

## Exact 1,000-Day Startup State

`start.bat` currently reports:

```text
historical days             1000
backfill days               1000
train/evaluation split      0.98 / 0.02
full-data refit after gate  enabled
direction sample cap        40000
LightGBM device             CPU
Binance paper               disabled by default
```

Preflight measured about 347 GiB free and found derived cross-venue/trade sources spanning roughly
1,292 days. It classified the 1,000-day rebuild as serviceable without a bulk historical download.

The trainers refuse a dirty Git tree unless `BTC_ALLOW_DIRTY_TRAINING=1`. Do not bypass that guard
for this run. Commit the exact reviewed source and documentation first so every artifact can carry
reproducible code provenance.

## Operator Runbook

Before starting:

```powershell
git status --short
$env:BTC_VALIDATE_STARTUP='1'; .\start.bat
```

`git status --short` must be empty. Remove the temporary validation environment variable before a
normal launch if it remains in the shell.

Start the application once:

```powershell
.\start.bat
```

Do not launch a second backend. Do not edit training/model code during the run. Browser refreshes do
not restart the backend because reload mode is disabled.

After training completes:

```powershell
python backend\production_readiness.py --mode paper
python backend\report_master_runtime_state.py
python backend\bc_forward_readiness_report.py
python backend\check_feature_contract.py --report
```

Also inspect the System Health tab for:

- code current;
- main model and required heads serviceable;
- P(Hold) calibration state;
- recorder freshness;
- Protocol B/C collection status;
- `DATA OK` rather than `DO_NOT_TRUST`.

Protocol B/C should normally remain `COLLECTING` or `NOT_READY_DATA` immediately after restart.
Training cannot fabricate independent forward evidence.

## Remaining Non-Code Gates

1. Commit the reviewed tree so dirty-training protection permits the run.
2. Complete the 1,000-day retrain and accepted full-data refit.
3. Confirm all current artifacts have valid manifests, hashes and semantics versions.
4. Accumulate official Protocol B/C outcomes to their frozen stopping rules.
5. Configure a dedicated production virtual environment, explicit allowed origins and control
   tokens before any production paper deployment.
6. Run recorders continuously and investigate every stale required source.
7. Demonstrate robust forward post-cost value before considering capital.

No amount of training history guarantees a profitable model. More history broadens regime coverage;
it can also dilute recent behavior. Promotion remains controlled by untouched-tail, calibration,
economic and forward-shadow evidence rather than training completion alone.
