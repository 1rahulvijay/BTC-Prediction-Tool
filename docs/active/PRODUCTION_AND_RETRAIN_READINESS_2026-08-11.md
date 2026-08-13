# Production and retrain readiness - 2026-08-11

## Verdict

The source tree is ready for a deliberate **30-day full-pipeline smoke retrain** selected on
2026-08-13. A 900-day evaluated release remains the next step after the smoke succeeds. The fixes
are committed on local `master`; the branch is two commits ahead of `origin/master`. It is **not
yet ready to serve as a production decision service**, because the current
artifacts are legacy/incompatible and the deployment evidence gates are correctly closed.

This distinction is intentional:

- `start.bat` is the training and local paper-evidence launcher.
- `start_production.bat` is a frozen, no-training, paper/shadow deployment launcher.
- Real-order flags remain forced off. No code result establishes future profit or accuracy.

## Bugs fixed in this pass

### Startup selftest isolation and diagnostics

`start.bat` enables `BTC_EVIDENCE_MODE=1` before running offline invariants. The meta-model
contract fixture inherited that production setting and attempted its positive training case
before recorders were launched, so the real forward-evidence gate correctly refused the fixture
and startup stopped. The fixture now explicitly runs its positive case with evidence mode off and
its refusal case with evidence mode on, restoring the caller's environment after each case. The
live gate was not weakened. The launcher's multi-command block now records the exact failing
command instead of always misreporting `head_permissions.py`.

### Final rescan addendum

The final trainer-by-trainer trace found and fixed two additional provenance defects before the
overnight run:

- round-state declared `data/research/round_state/` while its trainer reads
  `data/research/round_state_stopping_180d_30s/`; a successful fit would have failed stamping;
- historical fingerprint evidence reconstructed OHLC from aggregate trades but was stamped as
  research-matrix-trained; it now emits an exact aligned trainer-owned receipt bound to the
  output parquet hash.

See `FINAL_RETRAIN_RESCAN_2026-08-11.md` for the complete model/head/strategy inventory and the
217-step validation result.

### 1. Specialist retrains could finish and still be rejected

`train_heads.py` stamped specialist artifacts from the generic research-matrix identity while
strict serving requires proof of the data actually used. A successful overnight run could
therefore create new artifacts with `executed_identity_recorded=False`, then reject them during
the staged-bundle validation.

Fixed behavior:

- file-backed heads attest the exact immutable source files they read;
- persistence attests both the research matrix and `persistence_dataset.parquet`;
- round-state attests the matrix plus both round-state source parquets;
- archive/dynamic trainers embed an exact aligned in-memory feature/label receipt;
- champion-meta attests the exact joined training frame;
- source mutation during training or hashing refuses publication;
- provenance is computed after a successful fit, so an optional head can still decline cleanly
  when data is insufficient;
- trainer import/version failures remain fatal and explicit.

### 2. Optional noise-gated heads contradicted the serving gate

The trainer deliberately permits optional heads to save nothing when they fail an evidence/noise
gate. The serving checker nevertheless required every optional ranking head to exist. That made an
honest abstention indistinguishable from a broken bundle.

Fixed behavior:

- a missing optional head is `OPTIONAL_ABSENT` and serving must abstain from that feature;
- a required missing head still blocks;
- an optional artifact that exists but is stale, unmanifested, or tampered still blocks.

### 3. Production startup selected the wrong DuckDB

The pending production launcher pointed explicitly at `data/analytics.duckdb`. The committed
canonical write path is `data/btc_duckdbs/analytics.duckdb`; those stores have divergent history.

Fixed behavior:

- production names the committed canonical path by default;
- an explicit operator `BTC_DB_PATH` still wins for a deliberate snapshot/deployment;
- a static regression test rejects the divergent root store;
- the launcher exports the selected Python executable to all recorder processes.

### 4. Cold production startup required stopped recorders to already be live

Production readiness requires forward evidence to advance, but the launcher previously checked
readiness before starting the recorders.

Fixed order:

1. refuse an occupied API port;
2. build immutable frontend assets;
3. start each standalone recorder exactly once;
4. wait at most 90 seconds for the two mandatory evidence streams;
5. run the complete fail-closed production readiness gate;
6. start one Uvicorn worker without reload.

The wait is bounded and returns non-zero when evidence remains dark. It never enables trading.

## Regression coverage added

- `backend/tests/test_specialist_source_provenance.py`
- `backend/tests/test_feature_contract_optional.py`
- `backend/wait_for_forward_evidence.py --selftest`
- `backend/tests/test_production_launcher_contract.py`

All are registered in GitHub invariants and the Windows `start.bat` selftest gate.

## Validation performed

| Gate | Result |
| --- | --- |
| clean tree / branch | clean local `master` at `971ba01`; two commits ahead of `origin/master` |
| source-state registry | fresh: 9 sources hashed, 14 registry rows, 12 purposes |
| exact `start.bat` selftest-only sequence | all invariant selftests passed |
| launcher integrity | all labels/guards valid; all 105 invoked paths exist |
| `python -m pytest -q` | 155 passed |
| Python compileall | passed |
| pyflakes across `backend/` | passed |
| complete local workflow | 216/216 gates passed in 694 seconds |
| real ensemble train/save/load smoke | 13/13 functional checks passed; 21 OOF seat/bucket records, 12 artifacts, zero prediction drift after reload |
| canonical DuckDB initialization | passed against `data/btc_duckdbs/analytics.duckdb`; required identity/resolution columns present |
| recorder declaration audit | 10/10 declared stores readable; all `STALLED` because the app is closed; no schema/unit drift |
| Binance paper engine | passed, including fills, fees, slippage, funding, recovery, rollback and risk gates |
| Polymarket dynamic paper / equal-capital race | both passed |
| settlement head | 37 checks passed; exchange proxy is prohibited from Polymarket EV pricing |
| strategy registry | 18/18 Polymarket strategies logged, exposed and consistently named |
| production HTTP/WebSocket surface | passed; readiness fails closed, admin mutation and foreign origins refused |
| Vite production build | passed |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| 30-day smoke preflight | passed; `SHORT_WINDOW`, no long-build disk gate required |
| planned 900-day resource/data preflight | passed; `REBUILD`, source coverage exceeds the request |

The standalone real-fit smoke printed its final explicit `PASS (13 checks)` after completing
temporary-directory cleanup, but the external PTY wrapper reported status 1 after the final
line and emitted no traceback. No model assertion failed and the same production contracts pass
the workflow gates. This is recorded as a test-runner exit-status anomaly, not concealed as a
second clean process exit and not used as the sole retrain-readiness evidence.

## What `start.bat` will do next

The validated launcher state is:

- `BTC_HISTORICAL_DAYS=30`
- `BTC_MODEL_TRAINING_DAYS=30`
- `BTC_TRAIN_SPLIT_FRAC=0.95`
- no completion marker, so heads and the main model are forced once;
- specialist heads train transactionally in a staging bundle;
- the active bundle swaps only after required heads and strict manifests pass;
- the main candidate is evaluated on its untouched recent 2% tail;
- the full-data refit is a shadow challenger, not an automatic live champion;
- after a successful complete run the marker freezes ordinary restarts.

Do not interrupt the first run unless it logs a fatal error. Daily source files are cached, so a
retry reuses completed downloads/build work.

## Operational blockers that code must not bypass

Current diagnostics correctly report:

- saved main ensemble v11 is incompatible with current v14;
- 0/11 active specialist artifacts are serviceable before retraining;
- P(Hold) calibration is off before a verified release exists;
- standalone recorders are stopped because the app is closed;
- the archive has 8/9 streams; liquidation evidence is still absent;
- three complete-trade artifacts fail current dataset/policy/code identity;
- production tokens and a dedicated production virtual environment are not configured.

These are not reasons to weaken the checks. Required actions are:

1. Commit this exact code.
2. Run `start.bat` and allow the 30-day pipeline smoke to finish.
3. Confirm the 30-day marker, staged artifacts, strict load and live inference all work.
4. Restore 900 days with a 0.98 split, rerun validation, and complete the evaluated release.
5. Accumulate fresh 900-day-bundle-attributed paper outcomes and recorder evidence.
6. Retrain/promote complete-trade champions only through their separate evidence gates.
7. Restore the missing liquidation stream or keep liquidation-dependent research blocked.
8. Create `.venv-prod` (or `.venv`) from pinned requirements and set distinct 32+ character
   `BTC_ADMIN_TOKEN` and `BTC_CONTROL_TOKEN` values outside the repository.
9. Run `start_production.bat`; do not bypass a failed readiness item. It remains pinned to the
   900-day artifact identity and rejects the temporary smoke model.

## Accuracy and profitability boundary

This pass fixes provenance, abstention, datastore selection, startup ordering, and regression
coverage. It does not create a predictive edge. Both Binance and Polymarket must remain paper-only
until cost-adjusted, release-scoped, forward outcomes clear the predeclared gates. Higher training
accuracy, a completed backtest, or a larger data window is not sufficient evidence of sustainable
profit.
