# Codebase Integrity Audit

Date: 2026-07-04

## Scope

This pass synchronized the current code with the latest paper-strategy, 1,500-day training,
validated-refit and live-shadow work. It targeted defects that could overstate accuracy, promote the
wrong model, corrupt paper P/L, or make the long retrain unsafe.

The app remains a research and paper-trading system. These fixes improve evidence quality; they do
not establish a profitable live edge.

## High-Impact Fixes

### Training window

`start.bat` had drifted from the approved 1,500 days to 2,200 and then 1,865 while the runbook,
backup, disk estimate and archive checks still described 1,500. The launcher now consistently uses:

- `BTC_HISTORICAL_DAYS=1500`
- `BTC_BACKFILL_DAYS=1500`
- 98% candidate fit and approximately 30 recent days held out
- `full_retrain_1500d_complete.json`

### Stable model identity

Saved ensembles did not persist bundle identity or full-refit state. A restart could rename the same
artifact, break durable A/B matching and lose the legal validation boundary. `bundle_metadata.pkl`
now persists and restores bundle ID, split fraction, split index and full-refit state.

### No fake backtest after 100% refit

A promoted full-data model could inherit the incumbent's old train boundary and score fitted rows as
out-of-sample. A full refit now records a null boundary and `full_refit=true`. Historical backtest is
disabled for that active bundle. Its valid evidence is the saved candidate holdout report, purged OOF
artifacts and live shadow comparison.

### Challenger must beat the primary

The former promoter required time, sample size, PF and expectancy but not superiority to the primary.
After restart, it also reconstructed bootstrap arrays from aggregate totals, destroying pairing.

Promotion now requires all of:

- 500 exact paired outcomes on identical prediction IDs
- positive challenger-minus-primary accuracy
- positive 95% paired-bootstrap lower bound
- 30 calendar days
- 500 cost-adjusted directional trades
- profit factor above 1.20
- positive expectancy

Pairs come directly from DuckDB and the bootstrap is deterministic.

### First boot uses the same promotion pipeline

With no incumbent, startup previously bypassed evaluation and trained directly active. A first boot
now stages the 98% candidate normally. If it passes, that evaluated candidate becomes the temporary
primary and the 100% refit remains silent shadow. A failed first candidate gets no completion marker.

### Auditable paper exits

The ledger stored P/L but not gross exit proceeds, exit fee or reason. The UI therefore reconstructed
exit proceeds from P/L, which repeated rather than verified the calculation.

New rows store `exit_gross`, `exit_fee`, `exit_reason` and `state_json`. The API independently checks:

```text
pnl = exit_gross - exit_fee - entry_ask - entry_fee
```

The Trades tab marks rows `exact`, `mismatch` or `legacy`. Existing rows remain legacy because their
missing raw components cannot be recovered honestly.

### Restart-safe straddles

Individual straddle exits existed only in memory. A restart after one leg took profit could settle as
though both legs were still held. Leg bid and fee state is now persisted after every exit, restored
for an open round and used by settlement. Legacy rows retain the conservative $1 total floor.

### Honest path-threshold labels

Path classifiers use basis-point labels derived at training time, but the UI always printed `$50` and
`$100`. The backend now converts saved thresholds to current live-dollar equivalents before display.

### Resumable disk guard

The fixed 300GB check could reject a retry because completed cache files consumed disk. A first long
build still requires 300GB. Once at least 1,000 source-day files exist, a resume uses an 80GB hard
floor for the sequence memmap, staged bundles and database/parquet rewrites.

## Paper-Ledger Semantics

- Entry is the executable ask, except maker shadows that explicitly use their resting bid.
- Early exit is the visible bid; exit fee is stored separately.
- Settlement gross is 1 for a winning share and 0 for a losing share.
- A straddle can return more than 1 gross if one leg was sold before the other settled.
- `BTC @ buy/exit` is the app's Pyth reference, not the share's sole profit driver.
- UP can profit while BTC falls from entry if BTC remains above the round anchor.

Only rules with a fixed evaluation checkpoint currently have complete SKIP/NO_QUOTE/NO_FILL
denominators. Several conditional shadows log entries only. Their entered-trade EV is valid, but their
opportunity coverage is not a complete denominator.

## Validation

- Backend `compileall`: pass.
- Critical-name pyflakes scan: pass. Remaining warnings are unused symbols and placeholder-free
  f-strings, mostly in offline research scripts.
- Frontend production build: pass.
- Saved main-model compatibility: pass, 69 active features and GLOBAL 5m/15m seats.
- `model_promotion.py` self-test: pass.
- Bundle metadata round trip: pass.
- Paired A/B test: better challenger promoted; reversed challenger did not.
- Isolated DuckDB accounting: pass for early exit, partial straddle restart and legacy fallback.
- Launcher validation: 1,500 days, 98/2 split, full-refit flow enabled.
- Port-guard safe mode: detected live PIDs on 3000/8000, aborted, and left both running.
- `git diff --check`: pass apart from repository line-ending notices.

## Remaining Risks

1. No model or paper rule is real-money approved.
2. Legacy ledger rows cannot become independently auditable retroactively.
3. Full-refit probability quality must be established live because all historical rows were fitted.
4. Some strategy opportunity denominators remain entry-only.
5. The 1,500-day run may take several days and can expose source outages absent in a short check.
6. Endpoint direction remains weak; path, hold, touch, volatility and risk have stronger historical
   evidence but still require current live calibration.

## Activation

The current backend does not hot-reload these changes. They activate on the next deliberate restart.
`start.bat` now stops existing listeners on ports 3000/8000 before long work begins; set
`BTC_AUTO_STOP_EXISTING_APP=0` to warn and abort instead. The RULE STATUS tile exposes the boot SHA-256
build hash and warns when the current files no longer match it.
