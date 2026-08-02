# P5B_66_TRADE_SIGN_ENTROPY

## Question

Does trade-sign entropy predict flow persistence?

## Frozen Contract

- Engine: `event_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_trade_sign_entropy\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_trade_sign_entropy\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
