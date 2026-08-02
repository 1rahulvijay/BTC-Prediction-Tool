# P5B_81_CAPITAL_EFFICIENCY

## Question

What is PnL per capital-minute?

## Frozen Contract

- Engine: `system_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_capital_efficiency\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_capital_efficiency\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
