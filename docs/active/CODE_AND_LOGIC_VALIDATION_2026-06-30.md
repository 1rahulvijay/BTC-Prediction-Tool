# Code And Logic Validation - 2026-06-30

## Scope

This audit resynchronized the newest Markdown and code, then checked the production paths most capable
of corrupting predictions or edge measurements:

- 136-feature construction and the selected 69-feature model input;
- saved 5m/15m ensemble compatibility and serialization;
- the new Layer-2 path forecaster;
- specialist-head and auto-finetune orchestration;
- Polymarket market discovery, anchor capture, quote recording, official settlement, and ROI analysis;
- frontend syntax and production build.

The app, backend server, and main direction retraining were not started.

## Current System Context

| Item | Current state |
|---|---|
| Live/trained direction horizons | 5m and 15m |
| Raw feature schema | 136 columns |
| Main model feature schema | 69 selected columns |
| Main saved architecture | `2horizon-5-15`, compatible |
| Main ensemble seats | XGB, LightGBM, CatBoost, HistGB, TCN, Logistic Regression, Random Forest |
| Regime buckets | TREND, RANGE, VOLATILE, GLOBAL |
| OOF stackers | present for both horizons in all four regimes |
| Research matrix | 518,400 rows, 360 days, 2025-07-02 through 2026-06-26 |
| Path model | v2 exact-dollar schema, rebuilt successfully |
| Direction conclusion | near coin-flip; do not add another direction family |
| Learnable targets | P(Hold), path magnitude, barrier touch, range, volume/activity |
| Profit status | unproven; execution remains blocked |

## Confirmed Defects Fixed

### 1. Live large-trade features still zero

Files:

- `backend/features.py`
- prior claim corrected in `MICROSTRUCTURE_PARITY_BUG_AND_FIXES_2026-06-28.md`

`server.py` removed sparse history keys so live values could be broadcast, but the feature builder passed
literal zero as the fallback for `large_trade_delta` and `large_trade_imbalance`. Both selected model
inputs therefore remained dead live.

The fallback now reads the current order-flow snapshot. A focused test produced exactly `0.37` and
`-0.42` in the corresponding feature columns.

### 2. Path plan was not stable

File: `backend/price_to_beat.py`

The plan was described as once-per-window but was erased and recomputed every specialist throttle. Later
keeper values were therefore combined with the old opening anchor.

The first valid near-open plan is now frozen for the round. Late-captured rounds are skipped. A focused
two-pass test confirmed that changing keepers after generation cannot change the saved round plan.

### 3. Dollar barriers were approximate basis-point labels

Files:

- `backend/train_path_forecaster.py`
- `backend/price_to_beat.py`

The old 7/14 bps thresholds only approximated $50/$100 near one BTC price. The trainer now labels exact
dollar excursions row by row and saves a versioned `threshold_units=usd` schema. Serving rejects the old
artifact instead of assigning it misleading dollar names.

### 4. Path model did not honor the finetune/full-retrain contracts

Files:

- `backend/auto_finetune.py`
- `backend/train_heads.py`
- `backend/price_to_beat.py`

The auto-finetune self-test still expected three jobs after a fourth was added, failures did not produce a
nonzero process exit, the path loader was load-once despite a hot-reload claim, and `train_heads.py` did not
include the new head.

All four issues are fixed. The path head is version-aware, included in full head training, hot-reloads on
mtime at most every 30 seconds, and is written atomically.

### 5. Recorder could manufacture a late anchor

File: `backend/polymarket/live_btc_updown_recorder.py`

When started during an already-open round, the recorder used the first observed BTC price as if it were
the opening anchor. It also discovered only the current deterministic slug, allowing the next round to be
found up to 30 seconds late.

The recorder now pre-discovers current and next 5m/15m slugs, accepts an anchor only within five seconds
of the true open, and skips a partial round with no trustworthy anchor. Token IDs are mapped by the
explicit `Up`/`Down` outcomes instead of assuming array order. Exact-slug discovery also continues when
the broad Gamma event request fails.

### 6. Recorder P(Hold) volatility did not match serving

File: `backend/polymarket/live_btc_updown_recorder.py`

The recorder used standard deviation of tick returns, while live P(Hold) uses standard deviation of the
price samples divided by the round anchor. The recorder now mirrors the serving formula.

### 7. Served P(Hold) joins mixed units and allowed future matches

File: `backend/polymarket/analyze_pm_recorder.py`

Recorder timestamps are epoch seconds; analytics timestamps are epoch milliseconds. The old nearest-time
join compared them directly and usually failed. It could also select a P(Hold) observed after the quote.

The analyzer now normalizes both to milliseconds and performs a same-horizon backward-only as-of join with
a five-second tolerance. It prefers the actual served keeper P(Hold), then the recorded base model, then a
base recomputation.

### 8. Corrupt historical anchor rounds polluted ROI analysis

File: `backend/polymarket/analyze_pm_recorder.py`

Of 35 rounds with quotes, 24 began more than five seconds late with exactly zero first-snapshot distance,
confirming the manufactured-anchor defect. The analyzer now excludes the entire affected round.

After filtering:

- 1,549 trustworthy snapshots remain across 11 rounds;
- 478 snapshots have a backward-matched served keeper P(Hold);
- 1,071 use recorded base P(Hold);
- only six one-entry-per-round signals exist;
- the analyzer reports `INSUFFICIENT DATA`, not a profit/no-profit conclusion.

### 9. Main-model saves could be marked complete after a disk failure

Files:

- `backend/model.py`
- `backend/check_model_compatibility.py`

`_save_models()` previously swallowed save errors. Training could return as successful and allow the
full-retrain completion marker even when artifacts were not committed.

Each artifact is now written through a process-specific temporary file and atomically replaced. The
architecture version commits last. A save failure returns false and raises from training, preventing the
completion marker. Instant-start preflight now checks feature-schema count/hash and loads/predicts through
the required GLOBAL XGBoost model for both horizons.

## Rebuilt Path Artifact

The path head was retrained only; the frozen direction ensemble was untouched.

| Horizon | Touch $50 AUC | Touch $100 AUC | Round-trip AUC | Asymmetric AUC | Net magnitude skill | High/low coverage |
|---|---:|---:|---:|---:|---:|---:|
| 5m | 0.795 | 0.799 | 0.851 | 0.820 | +0.193 | 0.50 / 0.49 |
| 15m | 0.837 | 0.786 | 0.758 | 0.728 | +0.175 | 0.50 / 0.49 |

These metrics validate path ranking, not profitability or endpoint direction.

## Validation Results

Passed:

- all backend Python files compile;
- no undefined names in Pyflakes;
- changed-file Pyflakes checks;
- server import without starting lifespan tasks;
- main saved model architecture, 69-feature schema, and GLOBAL 5m/15m XGBoost inference;
- all seven model families present for both horizons in TREND/RANGE/VOLATILE/GLOBAL;
- OOF stackers present for both horizons in every regime;
- feature snapshot test for large-trade delta/imbalance and VPIN;
- RAM/memmap sequence parity from the prior 360-day validation;
- path trainer self-test;
- path serving and once-per-round freeze test;
- auto-finetune self-test (8/8 scripts);
- specialist-head dry run recognizes the current path version;
- Polymarket recorder settlement/token/volatility self-test;
- Polymarket analyzer ROI/dedup/seconds-to-ms/backward-join self-test;
- decision gate, trade features, feature parity, direction tilt, and grade scorecard self-tests;
- JavaScript syntax check;
- Vite production build;
- `start.bat` validation mode, with no processes launched.

## Residual Risks And Honest Limits

1. VPIN remains zero for roughly the first hour after every process restart because its 750 BTC rolling
   bucket state is not persisted. This is expected cold-start behavior but still means reduced live input.
2. The recorder uses Pyth with Binance fallback for its BTC path; official market settlement is Polymarket
   CLOB/Gamma. Pyth is a proxy for the market oracle, not exact Chainlink tick history.
3. Historical late-anchor rows remain in DuckDB for forensics; they are excluded analytically, not deleted.
4. Only six trustworthy edge-qualified rounds exist. No profitability claim is valid before at least 500
   one-entry-per-round quote-plus-official-outcome observations and recent/horizon stability.
5. Several research-only scripts still mention retired 1m/3m/7m/10m/30m horizons. They are not imported by
   live serving, but should not be treated as descriptions of the current production roster.
6. Pyflakes reports non-blocking unused imports/variables and placeholder-free f-strings in research tools;
   no undefined-name or compile failures remain.
7. A frontend production build passed, but browser/runtime visual verification was not performed because the
   app was deliberately not launched during this audit.

## Operational Decision

The app is statically safe to start. The main model bundle is compatible and frozen. On the next run:

1. keep the Polymarket recorder running continuously so next-round anchors are captured near open;
2. wait at least one hour before judging VPIN parity;
3. rerun `probe_feature_parity.py` after live rows accrue;
4. rerun `analyze_pm_recorder.py` only as evidence grows;
5. do not enable real-money execution or claim profit from the current six signals.
