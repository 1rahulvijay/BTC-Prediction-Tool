# P5B_47_SHARED_INFORMATION_FALSE_CONSENSUS

## Question

How independent are ensemble votes?

## Frozen Contract

- Engine: `ensemble_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_shared_information_false_consensus\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_shared_information_false_consensus\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
