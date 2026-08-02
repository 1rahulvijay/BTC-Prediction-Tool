# P5B_63_BUY_SELL_IMPACT_ASYMMETRY

## Question

Does signed flow have asymmetric impact?

## Frozen Contract

- Engine: `l2_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_buy_sell_impact_asymmetry\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_buy_sell_impact_asymmetry\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
