# P5B_60_BID_ASK_REPLENISHMENT_ASYMMETRY

## Question

Which top-of-book side replenishes?

## Frozen Contract

- Engine: `l2_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_bid_ask_replenishment_asymmetry\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_bid_ask_replenishment_asymmetry\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
