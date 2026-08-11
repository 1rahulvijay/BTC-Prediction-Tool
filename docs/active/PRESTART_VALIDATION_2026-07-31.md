# Pre-Start Codebase Validation

Date: 2026-07-31

## Verdict

The current source is ready for the operator to start the application manually for the planned
1,265-day retrain and continued paper/shadow collection. The source, launcher and deterministic
invariant gates pass.

The current saved models are not ready to serve. This is an intentional fail-closed state:

- the saved main ensemble has an older architecture/version;
- the current feature semantics are v4 and training semantics are v3;
- 0 of 11 standalone artifacts have current training-identity manifests;
- no `full_retrain_1265d_complete.json` marker exists.

The next normal `start.bat` run will therefore force one complete retrain. Do not interpret model
cards as trusted predictions until training, untouched-tail evaluation, full-data refit, artifact
publication and explicit challenger promotion have completed.

No code audit or model retrain can guarantee accuracy, precision, win rate or profit. Real-money
order submission remains unimplemented and unauthorized.

## Defects Fixed

### Round-state artifact reload

`backend/round_state_panel.py` could retain a previously loaded model if a changed replacement
failed identity verification or deserialization. That was a fail-open reload: status could contain
an error while scoring continued with stale bytes.

The loader now:

- clears the active model on every failed verification/load;
- records an artifact mtime only after a successful load;
- retries identity failures after the normal check interval;
- includes a regression test that begins with a valid in-memory model, simulates a corrupt
  replacement and proves that scoring becomes unavailable.

The self-test is now part of both `start.bat` and the repository CI workflow.

### Nullable live state

REST timeouts and partial payloads can legitimately produce `None`. Several nested lookups assumed
that a present value was always a dictionary.

The following paths now normalize nullable values before nested access:

- Binance derivative liquidation preservation during REST refresh;
- regime and horizon signal-policy selection;
- window-favorability display data;
- Price-to-Beat pending/resolved round lookup.

These changes prevent a missing optional feed or display record from terminating the main loop.
They do not alter prediction probabilities, gates, labels or trading economics.

### Pytest discovery

The repository contains many historical/research executables named `test_*.py`. Unrestricted
pytest discovery imported archived files with duplicate module names and top-level research-only
imports, causing collection failure before genuine tests ran.

`pytest.ini` now limits pytest discovery to:

- `tests/`;
- `backend/venues/`.

The broader invariant suite remains `python backend/tests/run_ci_locally.py --all`; it executes the
standalone validation programs declared by the canonical GitHub workflow.

## Validation Evidence

Completed on the final source:

- repository backend/test compile;
- backend/test Pyflakes;
- default pytest: 5 passed;
- round-state corrupt-artifact regression;
- launcher integrity and repository layout;
- Vite production build and high-severity npm audit;
- complete canonical local CI: 70 of 70 steps passed;
- exact Windows `start.bat` path with `BTC_SELFTEST_ONLY=1`: passed and exited before startup.

The 70-step gate includes:

- target/label alignment and causal regime filtering;
- paper execution, fees, fills, risk, recovery and accounting;
- HTTP/WebSocket/control-plane boundaries;
- task supervision and close-only safety;
- model registry, verified deserialization and atomic promotion;
- feature/model weighting and timestamp contracts;
- live-feed protocol health and quarantine;
- research isolation, multiple-testing controls and promotion gates;
- frontend lockfile, build and dependency audit.

## Next-Run State

The launcher preflight measured:

| item | result |
|---|---|
| configured history | 1,265 days |
| derived source coverage | approximately 1,289-1,291 days |
| free system-drive space | approximately 183 GB |
| bulk historical download | not required by preflight |
| completion marker | missing |
| next normal launch | forced full retrain |

The specialist heads train into `data/saved_models_challenger_1265d`; they do not replace the
incumbent merely because training finishes. The main ensemble follows the 98/2 evaluation,
out-of-fold calibration, full-data refit and shadow-admission flow.

## Operator Sequence

Start manually:

```powershell
.\start.bat
```

After the run reports completion, leave the challenger gated and inspect it first:

```powershell
python backend\check_model_compatibility.py
python backend\check_feature_contract.py --enforce-serving
python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265
```

Only if the dry-run promotion report passes every declared gate:

```powershell
python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265 --apply
```

Then rerun the paper-production readiness report. Do not enable strict artifact identity or treat
the new bundle as primary before the manifest-writing retrain and promotion have succeeded.

## Remaining Operational Limits

- GitHub Actions remains billing-blocked; the locally parsed workflow is the enforceable gate.
- Current live archive coverage is under one day and one required multi-venue stream is absent.
- Research families that depend on long forward collection remain unready.
- Calibration is currently inactive because the compatible retrain has not completed.
- Real-order adapters remain absent. The validated execution surface is paper/shadow only.
