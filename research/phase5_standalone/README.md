# Phase 5 Standalone Research Suite

This directory contains 42 isolated research experiments. None can alter serving artifacts,
register a live strategy, or acquire capital authority.

## Guarantees

- Four chronological partitions: TRAIN, CALIBRATION, POLICY_SELECTION, UNTOUCHED_TEST.
- Purge gaps at every partition boundary.
- Frozen protocol hash per experiment.
- Causal-source validation before modeling.
- Binance dollar moves are converted to fractional returns before applying basis-point costs.
- Polymarket uses executable ask/bid prices and the canonical crypto taker-fee formula.
- Five-minute Binance signals cannot overlap; controls preserve action count, side balance,
  holding period and day.
- Thresholds and model choice are locked before the untouched test.
- Reports are immutable and identify protocol, loaded data slice, Git state and suite source hash.
- `capital_authority=false` in every protocol and every report.

## Validate Everything

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\run_all.py --selftest
```

Run a capped real-data integration campaign:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\run_all.py --smoke --maximum-rows 5000
```

Run all available rows only when the machine is otherwise idle:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\run_all.py --run
```

Every persisted run needs a new output directory. Existing reports are never overwritten.

## Candidate Evidence Contract

Experiments 27-35 and 38-42 require a canonical per-decision file at:

```text
data/research/phase5_candidate_evidence.parquet
```

The union schema is:

```text
ts_ms
alpha_id
action
market_return
gross_return
gross_pnl
net_pnl
cost
holding_seconds
regime
current_cost
predicted_ev
configuration_id
```

Individual frozen protocols declare the subset they require. A summary JSON is not accepted
because matched controls and causal audits require per-decision rows.

## Interpretation

`PASS_CANDIDATE` means only that a frozen standalone protocol cleared its declared historical
gate. It is not permission to wire the result into the app. Promotion still requires independent
forward evidence and a separate reviewed deployment change.
