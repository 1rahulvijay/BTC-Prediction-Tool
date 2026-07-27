# Serving And Evidence Completion

Date: 2026-07-27

Status: implemented and locally validated. This is evidence infrastructure, not proof of a
profitable strategy and not approval for real-money trading.

## Scope

This change completes the code path between a live Complete Trade Forecast, immutable decision
eligibility, own-recorder L2 execution reconstruction, one explicit evidence run, and a verified
promoted model bundle.

It does not change the separate main BTC direction ensemble. It does change the Complete Trade
Forecast policy and execution-head artifact contract, so that subsystem must be rebuilt and
retrained before it can collect a new admissible evidence run.

## Implemented

### 1. Immutable Clarification-001 eligibility

Every Ledger V2 row now commits:

- ledger schema version;
- candidate validity and reason codes;
- complete FEATURE_COLUMNS finiteness;
- full requested-size entry availability at the decision;
- decision book age;
- requested quantity;
- execution capacity q10;
- execution cost q80;
- eligibility verdict and SHA-256 over the decision-time fields;
- horizon and evidence-run identity.

The evaluator applies these gates before the score threshold. A high-scoring stale, incomplete,
non-finite, or under-capacity candidate cannot be selected. A malformed schema, eligibility hash,
or eligibility verdict makes the evidence set inadmissible.

The execution trainer and serving layer now produce capacity q10 directly. The evidence gate does
not substitute q50 for a missing q10.

Missing quote timestamps or quote age now mean no forecast. They are never converted into a
perfectly fresh zero-age quote.

### 2. Durable failed-write recovery

Failed Ledger V2 appends are atomically written to
`data/complete_trade_forecast_pending_v2/`.

Replay:

- inserts a missing immutable row;
- accepts an existing row only when every stored column is identical;
- refuses a forecast-id collision with different content;
- deletes a spool file only after recovery or an exact duplicate;
- exposes pending, failed-spool, and recovered counts in logger health.

The live forecaster attempts a small replay batch every 30 seconds. The in-memory dead-letter
buffer remains diagnostic; the disk spool is the recovery authority. An exact immutable
duplicate after a controlled backend restart is treated as an idempotent success. A duplicate ID
with different content is still refused and retained for investigation. In-memory checkpoint IDs
are pruned with inactive rounds so a multi-week evidence run does not grow without bound.

### 3. Own-L2 outcome reconstruction

`backend/trade_forecast/l2_outcome_reconstruction.py` reconstructs outcomes only from this host's
`execution_layer.duckdb`:

- first full-quantity ask VWAP at or after prediction + 500 ms;
- stress entry at or after prediction + 1,000 ms;
- no entry when the next recorder snapshot arrives more than the frozen five-second data-age
  limit after the requested latency;
- full-quantity bid VWAP exits only;
- no exit observation after the contract's recorded expiry;
- exact entry and exit taker fees;
- the forecast's frozen exit plan;
- official Polymarket settlement allowlist;
- same-round, same-checkpoint, eligible alternative candidate PnLs;
- a SHA-256 over the source recorder database and its DuckDB WAL, when present;
- explicit `OWN_L2_RECONSTRUCTION` provenance.

The production resolver no longer accepts caller-supplied PnL as official evidence. The old
dictionary resolver is retained only behind `test_only=True` for deterministic tests, and its
rows are excluded from production evaluator reads.

Run one explicit evidence set:

```powershell
python -m backend.trade_forecast.trade_outcome_resolver `
  --evidence-run-id <RUN_ID> `
  --recorder-db data/execution_layer.duckdb `
  --ledger-db data/complete_trade_forecast.duckdb
```

Run this against a stable recorder database. If either the database or its WAL changes while
reconstruction is reading it, the command refuses before writing outcomes.

### 4. Explicit run and frozen-protocol verification

The forward evaluator now requires `--evidence-run-id`. It no longer loads every V2 run by
default.

It verifies both frozen text hashes:

- `PREREG_COMPLETE_TRADE_M0_V2.md`;
- `PREREG_COMPLETE_TRADE_M0_V2_CLARIFICATION_001.md`.

The manifest requires singleton model bundle, bundle manifest, feature schema, policy, threshold,
preregistration, clarification, ledger schema, and evidence-run identities.

Dry-run:

```powershell
python -m backend.trade_forecast.evaluate_complete_trade_m0_v2_forward `
  --dry-run --evidence-run-id <RUN_ID>
```

Definitive one-time score:

```powershell
python -m backend.trade_forecast.evaluate_complete_trade_m0_v2_forward `
  --score-once --evidence-run-id <RUN_ID>
```

### 5. Verifiable model bundles

Promotion now requires exactly one readable manifest per artifact and verifies:

- artifact SHA-256;
- artifact type;
- dataset SHA-256 and version;
- feature schema hash;
- policy hash;
- model code hash;
- consistent policy identity across the bundle.

Promotion creates deterministic `bundle_manifest.json` containing every file path, byte size, and
SHA-256. `champion.json` commits both the complete bundle hash and the bundle-manifest hash.
Serving refuses missing, changed, undeclared, or extra files.

The immutable promotion time is the model freeze boundary. Process-local load time is no longer
used, so restarting the backend does not split one model into artificial evidence runs.

A verified promoted bundle can serve without the raw training parquet at its old absolute path.
Legacy loose artifacts still require the original training dataset. This is hash-based integrity,
not a public-key signature or protection from a malicious host administrator.

## Required Rebuild And Promotion

The Complete Trade Forecast execution artifact needs the new capacity q10 head, and the frozen
policy/code hashes changed. Rebuild and retrain in this order:

```powershell
python -m backend.trade_forecast.build_complete_trade_dataset
python -m backend.trade_forecast.train_btc_path_model
python -m backend.trade_forecast.train_execution_heads
python -m backend.trade_forecast.train_share_path_model
python backend/promote_challenger.py --challenger <CHALLENGER_DIR> --days <DAYS>
python backend/promote_challenger.py --challenger <CHALLENGER_DIR> --days <DAYS> --apply
python -m backend.trade_forecast.freeze_complete_trade_threshold
```

Freeze the threshold while the backend is stopped, then start a fresh controlled evidence run.
The freezer reads only the purged calibration partition, requires the promoted direct M0 score and
capacity-q10 and cost-q80 heads, and binds the immutable threshold to the verified champion bundle
hash. Its candidate filter is the same decision-time contract used live: valid candidate, complete
entry, finite features, q10 capacity at least the requested quantity, and a finite q80 cost.

Do not start an evidence clock until `share_path_serving.status()` reports:

- `bundle_verified: true`;
- `frozen: true`;
- no latched `freeze_violation`;
- non-empty `bundle_hash`;
- non-empty `bundle_manifest_sha256`;
- non-null `promoted_at`;
- a frozen threshold artifact matching that bundle and policy.

## Validation

New deterministic suite:

```powershell
python -m backend.trade_forecast.test_evidence_completion
```

It executes:

- eligibility-before-threshold selection;
- durable spool and replay;
- real temporary DuckDB forecast writes;
- own-L2 500 ms and 1,000 ms reconstruction;
- delayed-entry refusal and post-expiry path refusal;
- source database plus WAL provenance;
- fee-aware UP and DOWN outcomes;
- same-checkpoint matched alternatives;
- production rejection of caller-supplied PnL.

The suite is registered in both `start.bat` and Linux/Windows GitHub invariant jobs.

The frontend production bundle also builds from the lockfile, and the dependency audit reports
zero known vulnerabilities as of this validation. The PostCSS transitive dependency was updated
in `package-lock.json`; application source and model behavior were not changed by that update.

## Intentionally Not Claimed

- No strategy has passed the required 1,000-round, eight-occupied-week forward gate from this
  code change.
- No profitability, win-rate, calibration, or live fill claim is created by infrastructure tests.
- The scenario-engine economics remain diagnostic-only.
- A two-stage future-exit-liquidity head remains research work; it is not needed to keep the
  frozen evidence evaluator honest, because official outcomes are reconstructed from executable
  full-depth ladders.
- Cryptographic signing with an external private key is not implemented. The bundle is
  content-addressed and tamper-evident on this host.

The next valid step is rebuild, gated promotion, uninterrupted own-recorder collection, outcome
reconstruction, dry-run readiness checks, and only then the one-time frozen score.
