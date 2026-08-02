# P5B_75_SETTLEMENT_SOURCE_BASIS_HAZARD

## Question

Can settlement-reference basis cross the anchor?

## Frozen Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_settlement_source_basis_hazard\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_settlement_source_basis_hazard\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
