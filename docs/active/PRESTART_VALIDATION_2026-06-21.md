# Pre-Start Validation - 2026-06-21

## Verdict

**GO with `start.bat`. Do not use `start_instant.bat` until the full retrain finishes once.**

Current code serves horizons `{5, 15}` (1m dropped 2026-06-22; arch `2horizon-5-15`) with 69 selected model features. The saved main ensemble is
the older seven-horizon bundle, so normal startup will reject it and start a fresh 150-day background
training run. This is expected and required.

## Validation Results

| Check | Result |
|---|---|
| All backend Python files compile | PASS |
| Core imports including `server` | PASS |
| Undefined-name static analysis | PASS |
| Core project self-tests | PASS |
| Frontend Vite production build | PASS |
| Frontend JavaScript syntax | PASS |
| Node dependency tree | PASS |
| DuckDB readability/schema | PASS |
| Saved pickle load sweep | PASS - 415/415 |
| Saved JSON manifests | PASS - 7/7 |
| Recent three-day OHLC/data audit | PASS - zero bad bars/gaps/duplicates |
| Research matrix | PASS - 216,000 rows, 150-day coverage |
| Model feature contract | PASS - 136 raw, 69 selected |
| Sequence/label contract | PASS - horizons 5m/15m |
| Polymarket official settlement backlog | PASS - 364/364 resolved |

## Bugs Fixed During Preflight

1. **Unsafe instant startup:** the instant launcher used a three-day window even when the saved model was
   incompatible. It now fails fast instead of training and potentially overwriting the keeper from only
   three days.
2. **Sequence memory waste:** training expanded all 136 raw features before applying the 69-feature model
   mask. Pruning now happens before sequence expansion, reducing the estimated 150-day sequence tensor
   from **6.56 GiB to 3.33 GiB** without changing model inputs.
3. **Backtest boundary contamination:** the persisted out-of-sample boundary was hard-coded at 80% while
   the configured fit split is 98%. The server now records the exact split used by the model.
4. **DuckDB WAL recovery:** the inactive 8.2 MB analytics WAL was opened and checkpointed successfully;
   the database now starts from a clean checkpoint.

## Expected First Launch

1. `start.bat` starts the Polymarket recorder.
2. Incremental backfills and the 150-day research-matrix freshness check run.
3. Version-aware specialist heads skip unless stale.
4. Frontend and backend start.
5. The old seven-horizon main bundle is rejected.
6. The new 5m/15m ensemble trains in the background and saves the current architecture.
7. After the completion log, future launches may use `start_instant.bat`.

The dashboard can stream market data while background training runs, but main-ensemble predictions remain
unavailable/WAIT until training completes.

## Machine Notes

- RAM: 15.7 GB total; 5.9 GB free during this audit. Close browsers, IDEs, and unrelated applications for
  the 150-day training run.
- Disk: 446 GB free, sufficient.
- PyTorch is CPU-only (`2.12.0+cpu`), so TCN training does not use the RTX 4050. Booster GPU behavior is
  separate. This affects runtime, not model compatibility.
- Global `pip check` reports Reflex/Streamlit version conflicts. Those frameworks are not imported by this
  app; core runtime imports pass. Do not change the working FastAPI/Starlette versions solely to satisfy
  unrelated globally installed packages.

## Operator Command

```powershell
.\start.bat
```

Keep the terminal open until the log contains `Background startup training complete`. Do not run a second
launcher concurrently.
