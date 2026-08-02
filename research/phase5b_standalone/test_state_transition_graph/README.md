# P5B_83_STATE_TRANSITION_GRAPH

## Question

Are state transitions predictable?

## Frozen Contract

- Engine: `matrix_research`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\phase5b_standalone\test_state_transition_graph\run.py `
  --data-dir data `
  --output data\research\phase5b_standalone\test_state_transition_graph\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
