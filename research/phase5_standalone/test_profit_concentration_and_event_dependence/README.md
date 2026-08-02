# P5_41_PROFIT_CONCENTRATION_AND_EVENT_DEPENDENCE

## Question

Is apparent profitability concentrated in one day, week, event, or handful of trades?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_profit_concentration_and_event_dependence\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_profit_concentration_and_event_dependence\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
