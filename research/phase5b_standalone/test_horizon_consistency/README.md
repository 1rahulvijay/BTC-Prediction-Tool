# P5B_84_HORIZON_CONSISTENCY

## Question

Are forecasts logically consistent across horizons?

## Frozen Contract

- Engine: `system_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_horizon_consistency\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_horizon_consistency\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
