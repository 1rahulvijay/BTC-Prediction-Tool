# P5B_49_MODEL_CONFIDENCE_COLLAPSE_HAZARD

## Question

Will strong confidence collapse shortly?

## Frozen Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_model_confidence_collapse_hazard\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_model_confidence_collapse_hazard\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
