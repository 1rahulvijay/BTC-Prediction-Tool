# Production and retrain readiness - 2026-08-11

## Verdict

The source tree is ready for the deliberate **1,000-day retrain** after this change set is
committed. It is **not yet ready to serve as a production decision service**, because the current
artifacts are legacy/incompatible and the deployment evidence gates are correctly closed.

This distinction is intentional:

- `start.bat` is the training and local paper-evidence launcher.
- `start_production.bat` is a frozen, no-training, paper/shadow deployment launcher.
- Real-order flags remain forced off. No code result establishes future profit or accuracy.

## Bugs fixed in this pass

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
| `python -m pytest -q` | 155 passed |
| Python compileall | passed |
| pyflakes on changed Python files | passed |
| paper-trading integrity | passed |
| open-position official settlement/crossing selftest | 23 checks passed |
| B/C forward-readiness selftest | 42 checks passed |
| Binance paper engine | passed |
| production launcher dry-run | passed; canonical DB, 3-day warm-up, 1,000-day model identity |
| 1,000-day resource/data preflight | passed; REBUILD mode, 344 GB free, 1,301+ source days |
| frontend build/high-severity audit | passed inside local CI |
| complete local workflow | source/runtime tests passed; generated source state still needs final regeneration |

The final workflow must be rerun after regenerating `SOURCE_STATE.*` and committing this exact
source. Training refuses a dirty working tree, so the commit is part of the correctness contract.

## What `start.bat` will do next

The validated launcher state is:

- `BTC_HISTORICAL_DAYS=1000`
- `BTC_MODEL_TRAINING_DAYS=1000`
- `BTC_TRAIN_SPLIT_FRAC=0.98`
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
2. Run `start.bat` and allow the 1,000-day retrain to finish.
3. Confirm the completion marker exists and strict artifact checks pass.
4. Accumulate fresh bundle-attributed paper outcomes and recorder evidence.
5. Retrain/promote complete-trade champions only through their separate evidence gates.
6. Restore the missing liquidation stream or keep liquidation-dependent research blocked.
7. Create `.venv-prod` (or `.venv`) from pinned requirements and set distinct 32+ character
   `BTC_ADMIN_TOKEN` and `BTC_CONTROL_TOKEN` values outside the repository.
8. Run `start_production.bat`; do not bypass a failed readiness item.

## Accuracy and profitability boundary

This pass fixes provenance, abstention, datastore selection, startup ordering, and regression
coverage. It does not create a predictive edge. Both Binance and Polymarket must remain paper-only
until cost-adjusted, release-scoped, forward outcomes clear the predeclared gates. Higher training
accuracy, a completed backtest, or a larger data window is not sufficient evidence of sustainable
profit.
