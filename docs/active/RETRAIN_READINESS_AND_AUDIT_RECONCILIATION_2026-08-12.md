# Retrain Readiness And Audit Reconciliation

Date: 2026-08-12  
Scope: current `master`, reconciled against the three external audits supplied on 2026-08-12.  
Operational target: one 1,000-day evaluated retrain, 98/2 temporal split, gated full-data refit,
paper/shadow trading only.

## Honest Verdict

The source tree is ready to **attempt** the 1,000-day retrain. This means the launcher preflight,
invariant tests, data-coverage check, memory-bounded sequence path, artifact provenance, staged
publication, paper-accounting contracts, and serving refusal paths execute successfully.

It does **not** mean the future candidate will pass its holdout gates. It does not prove live edge,
profitability, sustainable income, or safe real-money operation. The existing artifacts remain
intentionally incompatible/unserviceable until the retrain completes and publishes new manifests.
Real order routing remains unavailable and disabled.

## Defects Fixed In This Pass

1. **False task health:** a supervisor wrapper sleeping after a worker crash was reported healthy.
   Health now requires the actual worker to be running; restart/flapping/stopped states are explicit.
   A completed supervisor can also own a later FastAPI lifespan.
2. **GPU fitting at import:** importing `model.py` fitted XGBoost and CatBoost probes. Probes now run
   once inside explicit training, so preflight/API imports cannot initialize CUDA or fit estimators.
3. **1,000-day provenance memory blow-up:** executed-array hashing called `X.tobytes()`, which could
   allocate a second copy of the complete memmapped sequence tensor. Hashing now streams bounded
   chunks and is byte-identical to the old digest.
4. **Required-artifact denominator bug:** readiness could say READY after excluding missing required
   files from the denominator. Missing required artifacts now block; absent optional heads abstain.
5. **Untrusted completion marker:** marker filename existence alone disabled all retraining. The
   launcher now validates schema, days, architecture, model feature hash, bundle id, bundle manifest,
   head completion, training completion, model directory, and deployment state.
6. **Settlement-head provenance:** the settlement artifact was stamped with matrix identity but no
   receipt for the arrays/labels actually fitted. It now carries the ensemble feature-array receipt
   plus settlement-label and decision-timestamp hashes.
7. **Stale backtest cache:** cache identity stopped at architecture version. It is now scoped to the
   exact model bundle id and model feature-schema hash.
8. **Wrong A/B economics:** profit-factor/expectancy used observation-time `actual_move`. It now uses
   only canonical `ENDPOINT` movement; rows without endpoint economics are excluded.
9. **Mixed-release prediction cycle:** a promotion between 5m and 15m inference could combine two
   releases in one cycle. Every release swap advances a generation; overlapping cycles are discarded
   and recomputed on the next tick before cadence predictions or decisions are persisted.
10. **WebSocket send race/stall:** concurrent producers could write the same socket while one slow
    client blocked all others. Broadcasts are serialized per event loop, fanned out concurrently,
    bounded to two seconds, and failed sockets are removed.
11. **Blocking DuckDB retry:** `_connect()` slept synchronously on the asyncio event loop. Async-thread
    callers now fail fast on a lock; off-loop workers retain bounded retries.
12. **Hidden migration failure:** the forward-EV migration swallowed every exception as though the
    column already existed. It now uses `ADD COLUMN IF NOT EXISTS` and lets real DB failures surface.
13. **False DB health:** directory writability was reported as database health. Health now executes a
    real query against the canonical DuckDB and requires both live prediction tables.
14. **Datastore fallback:** an import/declaration failure could silently redirect persistence to a
    sibling `analytics.duckdb`. The canonical declaration now fails loudly instead of falling back.
15. **Frozen boot fetched 1,000 days:** training identity overrode the intended 3-day live warm-up.
    Training identity and serving warm-up are separate; `start.bat` uses 1,000/1,000, while frozen and
    production launchers use 1,000-day identity with a 3-day warm-up.
16. **Recorder duplicate identity:** duplicate detection matched a script basename from any checkout.
    Recorder processes now use and match the exact Python executable and absolute repository script.
17. **Pyth resource lifetime:** the persistent HTTP session is explicitly closed on cancellation.
18. **Polymarket startup coupling:** slow market discovery moved into its supervised worker, so it no
    longer delays Binance/Pyth workers or FastAPI startup.
19. **Avoidable launcher downtime:** ports were killed before preflight/selftests. Existing processes
    are now stopped only after all non-destructive validation passes and before the first side effect.
20. **Interpreter drift:** launcher subprocesses and recorders now share the selected project Python;
    dependency installation uses `python -m pip`.

## Verified Existing Fixes From The Attached Audits

- Specialist trainer-owned source receipts and strict provenance were already fixed at `a39035b`.
- Head permission selftest isolation was already fixed at `971ba01`.
- Prediction rows already carry target contract, release id, endpoint basis, and terminal outcome
  protections; the current invariant suite exercises those paths.
- Production already runs fail-closed readiness inside FastAPI lifespan, not only in a batch wrapper.
- DuckDB additive migrations already use a checked helper except for the one legacy forward-EV clause
  corrected in this pass.

## Deliberately Open Architecture Work

These are real limitations, but are not silent retrain/artifact correctness defects and were not mixed
into the overnight path:

1. **Launcher decomposition:** `start.bat` still combines validation, recorder startup, data builders,
   specialist training, frontend, and backend. Split launchers would improve operations, but changing
   orchestration immediately before a long run adds more risk than it removes.
2. **Cooperative job shutdown:** manual backtest/relearn/replay tasks retain handles, but native
   XGBoost/CatBoost/sklearn work cannot be safely interrupted by cancelling its asyncio wrapper.
   Correct closure needs cooperative cancellation between training stages and transactional cleanup.
3. **Venue-scoped authority:** the system health response still has a global `DO_NOT_TRUST` state.
   Binance and Polymarket need independent authority blocks so a Polymarket outage cannot visually
   invalidate an otherwise healthy Binance paper lane, and vice versa.
4. **Recorder rollback:** recorders are intentionally independent long-lived evidence collectors. If a
   later app startup step fails, they remain running; this preserves irreplaceable forward data but is
   not transactional launcher rollback.
5. **Import side effects:** `server.py` still creates its data directory during import. Production
   readiness checks the actual datastore/schema, so an empty self-created directory cannot pass, but
   moving all filesystem setup into lifespan remains cleaner.

## Validation Record

- Exact `start.bat` selftest-only path: PASS, both directly and through the local CI mirror.
- Long-window preflight: PASS in `REBUILD` mode; derived source coverage exceeds 1,000 days and no bulk
  source download is required.
- Focused regressions: supervisor, marker contract, startup import side effects, required artifacts,
  serving warm-up namespace, WebSocket broadcast, async DB retry, real DB health, streaming array hash,
  release-cycle atomicity, A/B endpoint economics, specialist provenance, and bundle completeness: PASS.
- Python compile and static checks: PASS.
- Pytest: 155 passed (13 third-party matplotlib/pyparsing deprecation warnings; no failures).
- Frontend production build: PASS. `npm audit --audit-level=high`: zero vulnerabilities.
- Full local CI mirror: PASS, all 216 registered gates in 1,503 seconds.
- Machine-generated source-state check: fresh after the final source changes.

## What The Next `start.bat` Run Will Do

1. Validate the 1,000-day configuration, disk, source coverage, and invariant suite.
2. Start forward-data recorders.
3. Incrementally refresh/reuse cached sources and rebuild the 1,000-day research matrix.
4. Enforce matrix/training identity before fitting.
5. Train specialist heads transactionally, one by one.
6. Start the UI/backend and train the main candidate in the background.
7. Evaluate the candidate on the untouched recent 2% tail.
8. If gates pass, refit on all rows, smoke-test the staged bundle, and install it as a live shadow.
9. Write a completion marker only after required heads and the main model flow complete.

Do not interpret the UI as model-ready while training is running. Keep both venues paper-only until
fresh forward evidence clears their separate economic gates; passing code tests cannot create alpha.

## 2026-08-13 Runtime Follow-up

The first 1,000-day attempt exposed a previously unobserved cross-process conflict: the 04:00
`BTC_AutoFinetune` scheduled task rebuilt the canonical matrix to its old 360-day fallback while
specialist heads were fitting. Provenance checks rejected the transaction and serving artifacts were
not changed. The full diagnosis, measured partial results, fix and rerun instructions are in
[TRAINING_PIPELINE_CONCURRENCY_INCIDENT_2026-08-13.md](TRAINING_PIPELINE_CONCURRENCY_INCIDENT_2026-08-13.md).
