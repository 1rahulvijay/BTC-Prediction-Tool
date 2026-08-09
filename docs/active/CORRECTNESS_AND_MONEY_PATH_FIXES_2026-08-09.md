# Correctness And Money-Path Fixes

Date: 2026-08-09  
Scope: confirmed Scan-6/ChatGPT audit defects and the pre-1000-day training gate.

## Verdict

The corrected code is suitable for another **paper-only** training and evidence run. It is not
proof of profit and it does not authorize real orders. The old artifacts on disk remain refused
because their identity is unprovable; the next full retrain must create the first serviceable
release under these contracts.

The most important behavioral change is intentional abstention. When empirical uncertainty,
independent grouping, forward evidence, a complete release, or a valid HMM partition is absent,
the app now refuses to adapt or open model-driven Binance paper risk instead of substituting a
number-shaped heuristic.

## Implemented Fixes

### Release and training integrity

- SAFE feature pruning now fails closed. A missing/invalid pruning decision cannot silently
  change a 63-feature architecture into the full 136-feature architecture.
- Partial HMM labels fail before fitting. `None` is explicit `GLOBAL_ONLY`; it is not a hidden
  switch to the retired ADX/volatility partition.
- HMM fitting is mandatory for the production training path. Failure stops before the expensive
  expert fit instead of producing experts that cannot be routed by their training partition.
- Save requires a restorable HMM, GLOBAL XGB/HistGB/LogReg/RF direction seats, a GLOBAL move
  head, class priors, and move-size state for every served horizon.
- Load consumes only files named by the committed artifact manifest. Stale files left by an
  older architecture are ignored.
- Every declared file must pass its per-file integrity sidecar. Required support artifacts,
  feature schema, architecture version, bundle ID, horizon set and HMM state are all mandatory.
- Any load error clears every partially loaded component. `is_trained` cannot become true from
  one surviving seat.
- Bootstrap candidates are promoted into the active directory with the same rollback transaction
  used by normal promotions. They are no longer active only in memory and lost after restart.

The promotion transaction still copies files before committing the manifest rather than swapping
one directory pointer. This is availability hardening, not a mixed-release correctness hole: the
old manifest fails its hash while files are in transit, and the new manifest is committed last.
The loader can therefore refuse briefly but cannot assemble generations.

### Outcome, calibration and adaptation integrity

- Prediction rows now persist both observations explicitly:
  - contract resolution (`actual_direction`, barrier/timeout resolution fields);
  - horizon endpoint economics (`endpoint_price`, `endpoint_move`, basis=`ENDPOINT`).
- Restored expert feedback uses the persisted contract outcome. Legacy rows with no declared
  outcome are excluded rather than reinterpreted from move sign.
- Per-model regime accuracy is now keyed by **horizon -> regime -> model**. Fast 5m resolutions
  can no longer set the 15m expert weights.
- The trust/meta model filters on exact `release_id` and `target_contract`, trains on endpoint
  economic return after estimated costs, and labels this as counterfactual endpoint economics,
  not realized execution P/L.
- Its temporal split purges a full horizon by timestamp. A one-row embargo was insufficient when
  predictions occur every few seconds.
- A trained meta-model inference exception fails closed (`SKIP`), not open (`TRADE`).
- Regime weights, confidence recalibration, threshold adaptation, online relearn and promotion
  are all refused while required forward recorders are dark in evidence mode.
- Forward recorder health is cached for 30 seconds in the loop to avoid turning the safety check
  into a database probe every prediction tick.

### Binance paper lifecycle

- Strict paper defaults are restored:
  - `BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV=0`
  - `BTC_BINANCE_PAPER_ALLOW_UNGROUPED_HEAD=0`
- An environment gate that is disabled after an exit was queued no longer strands inventory.
  Pending exits continue in `CLOSE_ONLY`; pending entries are cancelled.
- A reversal is two interactions. After the close fills, the strategy is re-evaluated on the
  new snapshot; the opposite thesis must persist and the new entry receives a fresh decision
  timestamp and a full second latency leg.
- If the thesis disappears after the close, `REVERSAL_CANCELLED` is persisted.

### Scheduling and launchers

- REST polls, precision refresh, maintenance and feedback use monotonic deadlines. Their cadence
  no longer assumes every main-loop iteration lasts exactly two seconds.
- `BTC_DIR_MARGIN_5` defaults to zero. Measurement showed the 0.015 dead zone selected a more
  UP-skewed subset rather than correcting the skew.
- `start_instant.bat` now separates its 3-day live warm-up from the 1000-day artifact identity.
  It no longer rejects a valid full-window model as a 3-vs-1000-day mismatch.

### Database migration integrity

- Additive DuckDB migrations now use `ADD COLUMN IF NOT EXISTS` through one checked helper.
- The previous broad `except: pass` blocks could not distinguish an existing column from a
  malformed query, locked/corrupt database, incompatible type or storage failure.
- Initialization now stops on a genuine migration error. It is better for startup to fail with
  an exact database error than to run with a partially migrated evidence schema and write rows
  whose meaning cannot be reconstructed later.

## Regression Evidence

Passed in this change set:

- Python compileall and pyflakes on every changed production module.
- Frontend production build (`vite build`).
- Launcher integrity and single history-window resolver.
- Promotion/activation boundary and candidate-only auto-finetune.
- Artifact identity, serviceability, completeness and load-refusal tests.
- HMM bundle binding, causal regime filter and shadow partition tests.
- Contract parity, endpoint/barrier observation consistency and model metrics persistence.
- Calibration release/contract/raw-score tests and OOF/serving parity.
- Meta-model release/contract/purge/fail-closed test.
- Binance paper engine, API, strategy economics and the full Phase-1 selftest, including new
  close-only exit and two-leg reversal assertions.
- Production HTTP surface, control-plane security, feed protocol, keepalive, kline-time and
  writer-load tests.
- Fresh and repeated DuckDB schema initialization, including all additive migrations.

The 1000-day training itself was deliberately not run by the audit. That run is the remaining
end-to-end proof that all required seats can fit, save, reload and smoke-test on the real matrix.

## Expected First Launch

1. `start.bat` rebuilds/validates the 1000-day matrix and trains because the existing bundle is
   legacy/unmanifested.
2. If HMM, feature pruning, core seats, identity, holdout gates, full-refit staging or smoke tests
   fail, activation stops and the terminal names the failing contract.
3. If the 98/2 gate passes, the evaluated candidate becomes the bootstrap primary and the 100%
   refit runs as the shadow challenger.
4. Adaptation remains frozen whenever the required forward recorders are not advancing.
5. The strict Binance model-consensus paper account may report no entries until empirical
   interval/group evidence exists. That is correct behavior, not a startup failure.

## Still Not Proven

- No backtest result currently proves the complete served seven-seat ensemble plus decision
  policy profitable after fills, fees and slippage.
- The meta target is counterfactual endpoint economics because prediction rows are not order
  fills. Realized execution labels must come from the paper/execution ledger.
- Existing artifacts are 0% serviceable until retrained under the new manifest contract.
- Precision, accuracy and profit must be established from forward, release-bound outcomes with
  independent round/day uncertainty. No code change guarantees them.
