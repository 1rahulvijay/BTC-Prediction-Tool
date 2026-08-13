# Retrain Readiness: 30-Day Smoke To 900-Day Run

Date: 2026-08-13

## Scope

This pass repaired the failed 30-day research-matrix build and validated the exact startup,
training, model-publication, paper-trading, recorder, backend, and frontend gates used by
`start.bat`. It did not claim that any model or strategy is profitable.

## Failure And Root Cause

The failed run reconstructed 30 days of Binance spot aggregate trades correctly, ending on
2026-08-11. The OHLC parity gate compared those rows only with
`data/cache/btcusdt_1m_3d.json`, whose final official Binance candle was 2026-07-04. There were
zero overlapping minutes because the reference was stale, not because reconstructed OHLC was
wrong.

The old implementation treated zero date overlap as an OHLC mismatch and stopped all specialist
and ensemble training. Preserving the previous matrix was correct; selecting a fixed stale cache
was not.

## Code Corrections

### Overlap-aware official OHLC parity

`backend/build_research_matrix.py` now:

- examines available official Binance 1-minute cache files in freshness order;
- selects a reference only when at least 100 timestamps overlap the reconstructed bars;
- normalizes seconds, milliseconds, and microseconds safely;
- fetches and atomically caches a small aligned official Binance REST tail only when no local
  reference overlaps;
- records every checked reference and overlap count in the matrix manifest;
- still fails closed on a real OHLC mismatch or on insufficient proof;
- preserves the previous valid matrix on failure.

A regression test proves that a stale short cache is skipped in favor of an overlapping valid
cache. The existing deliberate-price-corruption test still fails parity as required.

### One-knob 30-day to 900-day transition

`start.bat` now accepts `BTC_HISTORICAL_DAYS` as an environment override while retaining 30 days
as the default. The train/holdout split follows the requested window automatically:

- 30 days or less: `BTC_TRAIN_SPLIT_FRAC=0.95` for a usable smoke holdout;
- more than 30 days, including 900: `BTC_TRAIN_SPLIT_FRAC=0.98`;
- an explicitly pre-set `BTC_TRAIN_SPLIT_FRAC` remains an operator override.

The historical, serving warm-up, model-training, backfill, completion-marker, and split settings
were validated for both 30 and 900 days.

## Measured 30-Day Matrix Result

The complete forced rebuild succeeded using cached source files:

| Check | Result |
|---|---:|
| Rows | 43,200 |
| Unique timestamps | 43,200 |
| Span | 30.0 days |
| Sorted timestamps | yes |
| Trade-feature coverage | 100.00% |
| Cross-venue coverage | 100.00% |
| Monthly quality gate | passed, 2 months |
| Official reference overlap | 37,928 minutes |
| OHLC median absolute difference | 0.0 USD |
| OHLC p99 absolute difference | 0.0 USD |
| Infinite numeric cells | 0 |

The 30 null cells are only the unavoidable unresolved future-label tail. They are not input-feature
gaps.

## Feature-Parity Result

The application continues to build 136 raw fields because the UI and diagnostics consume them.
The main fitted ensemble uses 63 model fields classified as `KEEP` or `PARITY-FIX`. Live-only
order-book, derivatives, options, liquidation, external, regime-snapshot, and Polymarket fields
without causal historical parity remain excluded from the main model. The warning about 66
live-signal columns therefore does not mean those zero-history fields enter the fitted ensemble.

## Validation Executed

- complete `start.bat` invariant suite: passed;
- complete backend Python compilation: passed;
- research-matrix selftest, including stale-cache regression: passed;
- real 30-day matrix build: passed;
- training identity check: passed;
- specialist-head transactional dry run: passed;
- training integrity: passed;
- bundle completeness and rollback tests: passed;
- 30-day launcher configuration simulation: passed, 95/5;
- 900-day launcher configuration simulation: passed, 98/2;
- 900-day resource preflight: passed in `REBUILD` mode;
- frontend Vite production build: passed;
- standalone capture application: 21 tests passed from repository-root discovery;
- all changed capture/research Python files: compilation and Pyflakes passed;
- all research-lane JSON result artifacts: parsed successfully;
- whitespace and Python lint checks for the parity change: passed.

The 900-day preflight measured more than 1,300 days of both trade-feature and cross-venue derived
coverage, more than 1,000 cached aggregate-trade files, and enough free disk for the 80 GB rebuild
floor. This is a readiness check, not an estimate that the full training will be fast.

## Operator Sequence

### First run: 30-day smoke

Run `start.bat` normally. Do not set `BTC_HISTORICAL_DAYS`; 30 is the default. Let the full head
and ensemble training finish. A successful run writes
`data/saved_models/full_retrain_30d_complete.json`. Do not treat the 30-day artifact as the
production candidate; it validates mechanics and publication.

### Second run: 900-day candidate

Close the backend after the 30-day smoke completes, then launch from PowerShell with:

```powershell
$env:BTC_HISTORICAL_DAYS = "900"
.\start.bat
```

The launcher automatically sets model-training days, backfill days, serving warm-up, completion
marker, and the 98/2 split to 900-day values. Existing daily cache files are reused.

## Honest Limits

- Passing software and data-integrity tests does not guarantee accuracy, win rate, profit, or
  sustainable income.
- The 30-day smoke must not be promoted on its training metrics.
- The 900-day candidate still needs untouched-tail evaluation, staged publication, smoke reload,
  and forward paper evidence before promotion.
- Both the Binance and Polymarket money paths remain paper-only until cost-adjusted, live,
  bundle-scoped evidence clears their predeclared gates.
- A clean Git commit is mandatory before training so artifact manifests can record reproducible
  code provenance. Training from a dirty tree remains blocked intentionally.
