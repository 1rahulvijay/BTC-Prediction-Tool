# P5B_64_TOXIC_FLOW_VETO

## Question

Can toxicity veto improve an unchanged policy?

## Frozen Contract

- Engine: `l2_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_toxic_flow_veto\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_toxic_flow_veto\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
