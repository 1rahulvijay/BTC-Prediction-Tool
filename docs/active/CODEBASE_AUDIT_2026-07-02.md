# Full Codebase Audit - 2026-07-02

## Verdict

The checked source tree is mechanically healthy: all Python files compile, the frontend builds, the
saved main ensemble matches the 69-feature serving contract, core logic self-tests pass, and both
Polymarket recorders remained live throughout the audit.

This does **not** prove a profitable trading edge. It proves that the current implementation is much
less likely to corrupt its own evidence or silently present stale calculations as valid decisions.

## Scope

- 402 repository files inventoried.
- 213 Python source files compiled.
- JavaScript/HTML IDs, duplicate functions, and production build checked.
- 132 Markdown files checked for local-link integrity.
- Core paths reviewed: startup, feeds, feature/label construction, model persistence, OOF stacking,
  verification, DuckDB, price-to-beat, Champion, round-state heads, and Polymarket recorders.
- Existing dirty worktree and research outputs were preserved. No running process was stopped.

## Confirmed Fixes

### 1. Invalid late Pyth anchors could poison accuracy

`PriceToBeatTracker` previously used the current Pyth tick when a boundary was missed by more than
three seconds. Because Pyth has no same-feed historical candle in this path, that value was not the
round-open or round-close price.

Fixed behavior:

- skip a late round open when no same-feed boundary can be reconstructed;
- invalidate a late close rather than recording a false win/loss or paper PnL;
- atomically remove dependent persistence, Champion, round-state, and paper-rule rows.

### 2. Cold-start feed mixing

When Pyth was unavailable before any Pyth/Binance offset had been measured, the settlement tracker
could open on raw Binance and later resolve on Pyth. It now fails closed until Pyth is fresh or a
measured conversion offset exists. The separate Binance mirror continues normally.

### 3. Silent live-loop failures

Pyth and price-to-beat loops swallowed all exceptions. They now emit throttled warnings for operational
failures and debug messages for non-critical metric/broadcast failures.

### 4. Startup event-loop blocking

Polymarket Gamma discovery uses synchronous `requests`. Startup now runs that call in
`asyncio.to_thread`, preventing a slow HTTP response from delaying live async tasks.

### 5. Per-base-model analytics were stale

The UI queried `model_predictions`, but current votes were only embedded as JSON in the ensemble row.
The live log/resolve transaction now persists and grades every base-model vote using strict close sign.
This changes analytics only; it does not change ensemble output or weights.

### 6. Artifact/data path inconsistency

The path and fade trainers, plus optional Parquet archive writers, now honor `BTC_DATA_DIR`. Custom data
directories no longer train an artifact in one folder while serving looks in another.

### 7. Unreliable compatibility preflight

Importing the full ML stack in a short-lived Python 3.13 process intermittently exited with Windows
`0xC0000005` during native-library teardown. A new lightweight `model_contract.py` owns the 69-feature
schema and architecture version. The preflight now avoids initializing Torch/XGBoost/LightGBM/CatBoost
and passed 10 consecutive runs.

### 8. Validation hygiene

- `shadow_store.py` self-test no longer opens the live execution database unless `--init-real` is explicit.
- Removed one dead frontend element reference.
- Corrected the two broken local Markdown links found by the scanner.

## Verification Results

| Check | Result |
|---|---:|
| Python compile | 213/213 pass |
| Vite production build | pass |
| Saved architecture | compatible |
| Active model feature count | 69 |
| Raw feature vector | 136 |
| Compatibility stability | 10/10 pass |
| Markdown local links | 0 broken |
| Duplicate HTML IDs/functions | 0 |
| Core/research self-tests executed | 30+ pass |
| Late-anchor synthetic test | pass |
| Invalid-round transaction test | pass |
| Base-model ledger test | pass |
| L2 exact VWAP/queue test | pass |
| Recorder liveness after audit | both processes responsive; 5m/15m bridge fresh |

## Remaining Risks

### Superseded: next-start training window

This audit originally recorded a pending 400-day retrain. That run completed on 2026-07-03 and the
operator subsequently selected a 1,500-day window. `start.bat` now targets 1,500 days and uses
`full_retrain_1500d_complete.json`; see `FULL_1500D_RETRAIN_RUNBOOK_2026-07-03.md` for the current
disk, runtime, incumbent-serving and post-training validation contract.

### No pytest suite

`pytest` discovers no formal tests. The project has many useful script-level self-tests, but no single
CI-style command enforces them. A future task should collect the safe synthetic checks into a small
pytest smoke suite.

### Shared Python environment conflicts

`pip check` reports unrelated global-environment conflicts for Reflex/Starlette and
Streamlit/Packaging/PyArrow. They are not application dependencies in `requirements.txt`, but a project
virtual environment would remove this ambient risk.

### Evidence and profitability

The recorder is operating and exact L2 data is accruing, but no real-money action is justified until
independent quote, depth, fee, latency, and official-settlement outcomes produce positive net EV with a
positive conservative lower bound. The round-state panel remains information/shadow only.

## Files Changed By This Audit

- `backend/server.py`
- `backend/price_to_beat.py`
- `backend/database.py`
- `backend/model_contract.py`
- `backend/model.py`
- `backend/check_model_compatibility.py`
- `backend/train_path_forecaster.py`
- `backend/train_fade_model.py`
- `backend/decision/shadow_store.py`
- `src/main.js`
- two corrected documentation links
