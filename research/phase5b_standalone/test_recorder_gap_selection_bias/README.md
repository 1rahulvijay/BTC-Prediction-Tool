# P5B_87_RECORDER_GAP_SELECTION_BIAS

## Question

Are recorder gaps selective?

## Frozen Contract

- Engine: `system_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_recorder_gap_selection_bias\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_recorder_gap_selection_bias\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
