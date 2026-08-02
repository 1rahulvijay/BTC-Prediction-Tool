# P5B_76_SEQUENTIAL_VALUE_OF_INFORMATION

## Question

Is acting now better than waiting?

## Frozen Contract

- Engine: `pm_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_sequential_value_of_information\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_sequential_value_of_information\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
