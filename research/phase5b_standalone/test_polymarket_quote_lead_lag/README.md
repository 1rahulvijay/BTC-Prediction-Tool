# P5B_73_POLYMARKET_QUOTE_LEAD_LAG

## Question

Which PM quote component reacts first to BTC?

## Frozen Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_polymarket_quote_lead_lag\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_polymarket_quote_lead_lag\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
