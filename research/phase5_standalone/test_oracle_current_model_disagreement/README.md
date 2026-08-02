# P5_04_ORACLE_CURRENT_MODEL_DISAGREEMENT

## Question

Does current code improve upon the July 4 Oracle deployment on paired forward decisions?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_oracle_current_model_disagreement\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_oracle_current_model_disagreement\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
