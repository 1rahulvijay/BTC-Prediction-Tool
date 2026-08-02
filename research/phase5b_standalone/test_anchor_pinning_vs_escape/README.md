# P5B_69_ANCHOR_PINNING_VS_ESCAPE

## Question

Will BTC pin, cross or escape the anchor?

## Frozen Contract

- Engine: `pm_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_anchor_pinning_vs_escape\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_anchor_pinning_vs_escape\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
