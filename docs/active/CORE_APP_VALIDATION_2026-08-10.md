# Core App Validation - 2026-08-10

## Scope

This pass audited and changed core model evaluation, head authority, database durability,
Binance paper risk, live market-data ordering, A/B promotion, artifact promotion, launch gates,
and production frontend compilation. It did not claim that any strategy is profitable. Software
correctness can prevent false evidence and unsafe paper behavior; it cannot create predictive
information or guarantee income.

## Correctness fixes

1. **Quantile evidence:** signed bands now use purged train/calibration/test partitions, finite-
   sample conformal widening, untouched-test coverage, and monotone serving. Magnitude q10/q50/q90
   each must beat its own constant baseline on a purged test.
2. **Head authority:** live evidence is filtered to the serving artifact SHA and selected by exact
   horizon plus seconds-left region. Freshness comes from the newest attributable outcome, not the
   report file modification time. Missing or stale evidence denies pricing/ranking authority.
3. **Terminal evidence:** duplicate prediction logging cannot reset resolved Kronos, individual-
   model, FSR-PPO, or A/B outcomes.
4. **A/B validity:** outcome pairs and profit statistics are scoped to exact bundle IDs. Confidence
   bounds resample UTC-day clusters rather than treating overlapping predictions as independent.
5. **Binance paper risk:** maximum notional is no longer misread as a minimum; a real minimum-order
   floor is enforced, and positions crossing executable-price maintenance margin liquidate in the
   simulator.
6. **Stream ordering:** perpetual aggregate trades are ID-deduplicated and cannot roll CVD into an
   older minute. High-break and low-break timestamps are independent for sweep/reversal evidence.
7. **Training identity:** all versioned head trainers use the canonical model-training-day resolver.
   Trainer import or missing-version failures are fatal rather than silent legacy skips.
8. **Promotion/reporting:** challenger promotion rejects stale outcome evidence. The specialist
   bucket audit can no longer call its verified loader before definition or publish after partial
   failures.
9. **Recorder truth:** Binance L2 health now reads the real `received_ts_ms` clock. Schema/unit
   drift is fatal to the evidence audit, while stopped/never-started recorders remain explicit
   operational states rather than code-test failures. Audit/selftest filenames are not counted as
   launched recorders.
10. **Serving fail-closed behavior:** missing, short, or non-finite liquidity/volatility inputs
    cannot receive a normal signal grade or silently disable the meta risk filter. Serving indices
    are derived from canonical feature names rather than hard-coded ordinals.
11. **Paper risk persistence:** Binance position writes bind fields explicitly rather than relying
    on dataclass order. Position sizing and post-fill checks both include exit cost, and any
    stop-loss exposure above the approved budget is rejected.
12. **Settlement immutability:** shadow outcomes, exact Polymarket settlement truth, checkpoints,
    and official round settlements no longer use destructive REPLACE semantics. The static fence
    now detects f-strings, bound/no-column statements, and all REPLACE operations on terminal
    evidence tables.
13. **Boot/cache reliability:** the DuckDB anchor uses the retrying connection path. A failed
    all-time-accuracy refresh is marked unavailable/stale and cannot overwrite live tracker values
    with an old result stamped as fresh.
14. **Test execution:** deterministic Binance paper, quant-platform, artifact, training-integrity,
    complete-trade, recorder-schema, and serving-risk contracts are now registered in Linux CI,
    Windows CI, and `start.bat`. The Binance paper `types.py` standard-library collision was
    removed by renaming it to `paper_types.py`.
15. **Self-maintaining outcome fence:** production schemas declaring terminal fields are derived
    from executable `CREATE TABLE` statements. Every such table must be protected from REPLACE or
    carry a reviewed exemption; new horizons under `predictions_{tf}m` are covered automatically.
16. **Recorder gate separation:** recorder selftests now validate code and launcher wiring without
    querying live stores. The separately executed evidence audit reads real stores and alone fails
    on schema/unit drift, while STALLED remains a truthful operational state.
17. **L2 gap evidence:** live-diff and snapshot-overlap sequence failures converge on one
    idempotent finalizer. Every GAP session writes exactly one forensic row and increments
    `gap_count` once; synchronization failures can no longer look like a gap-free archive. The
    additive reconciliation restored all three historical GAP sessions after a verified backup.

## Operational state before next launch

- The declared canonical analytics store is
  `data/btc_duckdbs/analytics.duckdb`. Another newer-looking analytics file exists, but the app
  must not silently switch stores; changing the declaration requires an explicit data migration.
- The canonical offline store does not yet contain `round_state_snapshots.head_identity_json`.
  Normal `start.bat` database initialization performs the additive migration. Historical rows
  without artifact identity do not authorize a new model; new evidence starts from zero.
- Signed-quantile and other version-tag changes require their affected heads to retrain. Existing
  artifacts are not reinterpreted as if they had passed the new tests.
- Keep real-money routing disabled. Use paper execution and forward evidence until each exact
  artifact/horizon/region has enough fresh resolved outcomes and economic lower bounds clear the
  predeclared gates.
- All standalone recorders were `STALLED` at the final offline check. This is an operator state,
  not a schema defect; `start.bat` must start them and the UI/evidence audit must show row progress
  before new forward data is treated as current.
- The newer non-canonical analytics file is not merged automatically. Its historical rows do not
  carry trustworthy serving-artifact identity and therefore cannot authorize the next release.
  The additive canonical migration plus newly collected attributable outcomes is the safe path.
- Before the final gate, SHA-256-verified backups were written under `data/migration_backups/` and
  the existing additive migrations were applied to canonical analytics and execution stores.
  Required identity, resolution, reference-source and Polymarket book columns were verified by a
  read-only schema check. Migrations did not rewrite or attribute historical rows.
- Existing serving artifacts remain legacy/UNKNOWN under feature semantics v5. This is expected
  before the forced retrain: strict identity refuses them, and the new training path writes the
  semantics version, code/tree state, data hashes and cutoff into its manifests.

## Remaining limitations

- Non-matrix specialist trainers still need trainer-owned source manifests. The shared
  orchestrator cannot truthfully infer a DuckDB query snapshot or dependency artifact after the
  fact. This remains a provenance blocker for promoting those heads solely from generic metadata.
- Depth20 queue/cancel/spoof fields are snapshot estimates, not sequenced L2 event truth. They are
  not active ensemble features and must remain research-only until an event-level recorder and
  reconciliation tests exist.
- No validation result guarantees future accuracy, precision, win rate, or profit. Promotion must
  be based on live, cost-adjusted, bundle-scoped evidence rather than training metrics.

## Regression tests added or strengthened

- `quantile_safety.py`
- `test_training_window_namespace.py`
- `test_terminal_outcome_relogging.py`
- `test_market_stream_ordering.py`
- `test_ab_day_block_bootstrap.py`
- `test_ab_bundle_scope.py`
- `test_probability_bucket_audit_contract.py`
- `test_model_serving_risk_contract.py`
- `venues/binance_l2_recorder.py --selftest` now covers event-aware and synchronization-time gaps
- head-health, head-permission, champion-authority, promotion, fixed-stake, and Binance strategy
  economics selftests
- deterministic Binance paper, quant-platform, model-bundle, training-integrity, complete-trade,
  and real recorder-schema checks that previously existed but were not executed by any gate

These tests are registered in GitHub invariants and the local `start.bat` selftest gate.

Long-running historical scorecards, the deliberate recorder crash/kill drill, and the end-to-end
training smoke are retained as manual research/integration tests. Running them on every app start
would train against large local data or kill a recorder process; they are not silent CI contracts.
