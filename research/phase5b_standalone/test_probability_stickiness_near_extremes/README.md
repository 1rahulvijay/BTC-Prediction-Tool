# P5B_70_PROBABILITY_STICKINESS_NEAR_EXTREMES

## Question

Are extreme probabilities sticky?

## Frozen Contract

- Engine: `pm_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_probability_stickiness_near_extremes\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_probability_stickiness_near_extremes\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
