# Final 1,000-day retrain rescan - 2026-08-11

## Verdict

The canonical source tree is ready to start the deliberate 1,000-day retrain after this
change is committed. This rescan did **not** run the application or fit production models.
It traced the launcher, every active trainer, the model registry, both paper engines and all
runtime Polymarket rule names, then executed the complete local workflow.

This is a source/retrain-readiness verdict, not a claim that future predictions are accurate
or profitable. Existing saved artifacts remain legacy/incompatible and are correctly refused
until the retrain publishes current manifests.

## Defects found and fixed

### Round-state provenance named the wrong source directory

`train_round_state_heads.py` reads:

`data/research/round_state_stopping_180d_30s/{late_snapshots,transition_drought}.parquet`

`train_heads.py` declared the nonexistent older path:

`data/research/round_state/{late_snapshots,transition_drought}.parquet`

A round-state fit could therefore succeed and then fail strict source stamping, causing the
forced specialist stage to return nonzero and preventing main-ensemble training. The
orchestrator now declares the trainer's actual paths. The provenance regression test imports
the real trainer constants and compares them with the orchestrator paths.

### Fingerprint evidence was stamped against data it did not use

`build_fingerprints_historical.py` reconstructs one-minute OHLC from cached aggregate trades;
it does not train from `research_matrix_1m.parquet`. The orchestrator previously stamped its
output with the matrix identity. The builder now records an exact aligned feature/label/time
receipt for both 5m and 15m cells, binds it to the output parquet SHA-256, and `train_heads.py`
requires that trainer-owned receipt. If an optional rebuild declines, the old parquet, manifest,
integrity sidecar and source receipt are removed together rather than leaving stale evidence.

## Retrain inventory

### Main ensemble - trained by backend startup

Horizons: `1, 3, 5, 7, 10, 15, 30` minutes.

Direction seats:

- XGBoost
- Random Forest
- LightGBM
- CatBoost when installed
- HistGradientBoosting
- TCN/deep sequence seat when PyTorch is available
- Logistic Regression

The ensemble also trains the move-size regressor and purged OOF stackers. The separate rolling
exchange endpoint settlement head is fitted from the main candidate's rows and saved with that
candidate. Promotion remains gated on the untouched recent 2% tail. A passing full-data refit
is a shadow challenger, not an automatic production replacement.

### Standalone specialist transaction - run by `train_heads.py`

Mandatory outputs whose failure aborts the overnight pipeline:

- selectivity
- signed quantiles
- P(Hold) persistence
- path forecaster
- big-move keeper
- big-drop keeper
- directional keeper
- activity keeper

Optional evidence/noise-gated outputs that may honestly abstain without blocking the bundle:

- round-state heads
- champion meta-model
- beat research head
- magnitude research head
- path-label research head
- historical fingerprint evidence

Every successful fitted output is integrity-hashed and source-stamped. Optional absence is not
permission to accept a stale or tampered file: a present invalid optional artifact still blocks
serving.

### Deliberately outside the 1,000-day automatic retrain

- `fade_model.pkl`: research-only; both causal challengers missed frozen promotion gates.
- `crossing_heads.pkl`: serviceable research bundle with no price/rank/size authority.
- complete-trade share-path, BTC-path and execution artifacts: separate L2/settlement dataset,
  separate evidence gates and separate promotion pipeline.
- Kronos: wrapper/fallback path only until an identity-bound production artifact exists.
- FSR-PPO: disabled research challenger.

These exclusions are intentional. Reporting them as automatically retrained would be false.

## Paper strategy inventory

### Binance perpetual paper engine

Five implementations are registered and passed engine/economic tests:

- trend following
- breakout
- mean reversion
- model consensus
- random control

The default `$500` competition enables only `model_consensus` and `random_control`. Model
consensus fails closed until it receives a current endpoint head, empirical uncertainty and an
exact-policy value lower bound. The other three strategies remain available outside
competition-only mode; they are not silently mixed into the `$500` comparison.

### Polymarket paper/shadow rules

The registry audit found 18 runtime rules and verified every one is logged, exposed and named:

- `CHAMPION_DYNAMIC_PAPER_V1`
- `CHEAP_SAFE_EARLY_V1`
- `LATE_LEADER_15M_SHADOW_V1`
- `LATE_LEADER_15S_V1`
- `LATE_LEADER_30S_V1`
- `LATE_LEADER_60S_V1`
- `LATE_LEADER_MAKER_V1`
- `MID_SCALP_LIVE_V1`
- `MODEL_CROSSFLIP_L1_V1`
- `MODEL_CROSSFLIP_L2_V1`
- `MODEL_FADE_LIVE_V1`
- `MODEL_RIDE_LIVE_V1`
- `MODEL_SEQUENTIAL_REVERSAL_V1`
- `MODEL_STRADDLE_LIVE_V1`
- `PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1`
- `SHOCK_SNIPER_LIVE_V1`
- `STRADDLE_LIVE_V1`
- `TP_OR_SETTLE_LIVE_V1`

They remain paper/shadow evidence collectors. No test result grants real-order authority.

## Validation evidence

| Check | Result |
| --- | --- |
| clean canonical branch before audit | `master == origin/master` at `033be8a` |
| `python -m pytest -q` | 155 passed, 13 third-party warnings |
| compile all backend Python | passed |
| pyflakes across backend | passed |
| Vite production build | passed |
| specialist source-provenance regression | passed |
| Polymarket strategy registry | 18/18 consistent |
| Binance strategy economics | 7 checks passed |
| Binance paper engine | passed |
| model-dynamic Polymarket paper strategy | passed |
| `$500` paper competition | passed |
| settlement-head wiring/selftest | 27 + 37 checks passed |
| model registry | 14 entries, passed |
| crossing-head status | serviceable, no authority |
| 1,000-day launcher dry validation | force heads/main, split 0.98, freeze after completion |
| 1,000-day data/resource preflight | passed; 348 GB free, 1,301+ source days |
| complete local CI workflow | all 217 gates passed in 899 seconds |

The workflow's feature-contract line remains advisory because the files currently on disk are
the intentionally refused pre-retrain artifacts. Startup enforces strict identity and the
forced retrain is the path that replaces them.

## Expected first launch behavior

`start.bat` will rebuild the 1,000-day research matrix, run specialist trainers sequentially in
a staging transaction, then start the backend and train the main candidate in the background.
If a mandatory specialist, strict manifest, candidate holdout gate, reload smoke test or atomic
promotion fails, the completion marker is not written and the application must remain without
an active current model rather than fall back to an unverified artifact.

After training, do not interpret artifact existence as proof of profit. Verify the completion
marker, strict artifact report, release-scoped calibration, forward paper outcomes, recorder
health and both venue-specific economic gates.
