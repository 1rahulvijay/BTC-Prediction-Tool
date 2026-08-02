# P5_35_CONFORMAL_NET_EV_GATE

## Question

Does a positive lower confidence bound improve candidate economics?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_conformal_net_ev_gate\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_conformal_net_ev_gate\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
