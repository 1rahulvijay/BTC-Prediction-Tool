# P5B_88_TIMESTAMP_UNCERTAINTY_STRESS

## Question

Does edge survive timestamp uncertainty?

## Frozen Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_timestamp_uncertainty_stress\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_timestamp_uncertainty_stress\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
