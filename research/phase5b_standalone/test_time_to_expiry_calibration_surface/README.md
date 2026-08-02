# P5B_48_TIME_TO_EXPIRY_CALIBRATION_SURFACE

## Question

How does calibration change with expiry?

## Frozen Contract

- Engine: `expiry_calibration`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_time_to_expiry_calibration_surface\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_time_to_expiry_calibration_surface\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
