# Round-State Decision-Support Panel

Date: 2026-07-02  
Status: implemented, trained, and served as SHADOW/INFO only

## Purpose

Replace the misleading mental model of "UP 54% versus DOWN 46%" with synchronized questions the historical data can answer more reliably:

- Which side currently leads the price-to-beat round?
- What is the calibrated chance that side survives settlement?
- Can price cross the anchor again before expiry?
- Is another $20, $50, or $100 move still plausible in the remaining time?
- Does the opening path model describe QUIET, ACTIVE, CHOP, or TREND behavior?
- Is a similar path opportunity likely within the next three rounds?
- Is there a fresh executable Polymarket ask/depth quote, and does it clear costs?

This panel cannot place orders and does not modify Champion behavior.

## Implementation

| Component | File | Role |
|---|---|---|
| Trainer | `backend/train_round_state_heads.py` | builds compact deployable shadow heads |
| Scorer/composer | `backend/round_state_panel.py` | hot-loads, fail-closes, and creates one state object |
| Live tracker | `backend/price_to_beat.py` | tracks causal in-round state and invokes shadow scoring |
| API | `backend/server.py` | `GET /api/round-state` plus WebSocket payload |
| UI | `index.html`, `src/main.js`, `src/style.css` | plain 5m/15m round-state cards |
| Recorder launcher | `backend/start_recorders_once.ps1` | single-instance hidden collectors with logs |
| Artifact | `data/saved_models/round_state_heads.pkl` | 3.8 MB serving bundle |
| Metrics | `data/research/round_state_live/metrics.csv` | complete held-out evidence |

`backend/train_heads.py` now treats the bundle as a version-aware optional head. Missing large research inputs cannot block app startup. `start.bat` also launches the exact Polymarket L2 recorder unless `BTC_SKIP_PM_L2_RECORDER=1`.

## Feature Contract

### Final 30-120 Second Heads

```text
rv_15m
rv_30m
rv_60m
compression_ratio
shock_magnitude
seconds_left
distance_usd
abs_distance_usd
range_so_far_usd
recrosses_so_far
time_above_so_far
current_side_up
```

The five keeper values are joined from `research_matrix_1m.parquet`, matching `live_keepers.py`. Older similarly named 30-second research columns are explicitly discarded during training.

The live tracker samples side/recross occupancy every 30 seconds to match the historical label cadence. Running high/low are updated every tick.

If a round is first observed after its clock boundary, the anchor/path history is incomplete. All shadow probabilities fail closed and the panel displays AVOID until the next clean round.

### Next-Three-Round Opportunity

```text
rv_15m
rv_30m
rv_60m
compression_ratio
shock_magnitude
```

This head estimates a future path opportunity, not profit and not market mispricing.

## Models And Validation

Each target trains HistGradientBoostingClassifier, ExtraTreesClassifier and standardized LogisticRegression. The best two validation models are averaged. Isotonic calibration is fitted only on the middle temporal slice.

```text
oldest 70% rounds: model fit
next 15% rounds: model selection and calibration
latest 15% rounds: untouched test
```

Splits are grouped by whole round. Rows whose outcome window crosses a train/calibration boundary are purged. This is especially important for `next_opportunity_within_3_rounds`, whose label spans several future rounds.

## Complete Held-Out Results

| Horizon | Target | Test N | Base rate | AUC | Brier | ECE | Gate | Result | Members |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 5m | `future_side_flip` | 31,096 | 0.2039 | 0.8549 | 0.1199 | 0.0289 | 0.75 | PASS | histgb + logreg |
| 5m | `late_shock_20` | 31,096 | 0.6551 | 0.9198 | 0.1117 | 0.0497 | 0.75 | PASS | histgb + logreg |
| 5m | `late_shock_50` | 31,096 | 0.2636 | 0.9264 | 0.0966 | 0.0453 | 0.78 | PASS | histgb + logreg |
| 5m | `late_shock_100` | 31,096 | 0.0730 | 0.9485 | 0.0403 | 0.0192 | 0.82 | PASS | histgb + logreg |
| 5m | `next_opportunity_within_3_rounds` | 7,773 | 0.4899 | 0.8158 | 0.1796 | 0.0580 | 0.75 | PASS | histgb + logreg |
| 15m | `future_side_flip` | 10,368 | 0.0834 | 0.9225 | 0.0578 | 0.0312 | 0.75 | PASS | histgb + logreg |
| 15m | `late_shock_20` | 10,368 | 0.6032 | 0.9175 | 0.1164 | 0.0444 | 0.75 | PASS | histgb + logreg |
| 15m | `late_shock_50` | 10,368 | 0.2181 | 0.9303 | 0.0865 | 0.0359 | 0.78 | PASS | histgb + logreg |
| 15m | `late_shock_100` | 10,368 | 0.0546 | 0.9540 | 0.0337 | 0.0183 | 0.82 | PASS | histgb + logreg |
| 15m | `next_opportunity_within_3_rounds` | 2,590 | 0.6093 | 0.7846 | 0.1873 | 0.0695 | 0.75 | PASS | logreg + extra trees |

Every target clears its predeclared AUC gate and is marked `supported=true` inside the artifact. The scorer refuses to emit a probability from a failed target.

High AUC does not imply profit. The shock labels are strongly related to remaining time, distance, volatility and range already traveled. Their value is risk/timing support.

## Panel Meaning

### Leader Holds

The existing calibrated P(Hold) head: probability that the side already ahead finishes ahead.

### Flip Risk

The independent `future_side_flip` head: probability the current side crosses back before expiry. It is available only in the validated final 30-120 second window. Outside that window the panel may show `1 - P(Hold)` explicitly labeled as a settlement-failure proxy, not an any-cross probability.

P(Hold) and flip risk can disagree because the price can cross the anchor and later return to the original side.

### Move Before Expiry

Calibrated probability BTC moves at least $20/$50/$100 from the current checkpoint before the round expires. This is the requested remaining-window time-to-touch view. It does not forecast an exact touch second or direction.

### Round Type

The frozen opening path head maps `quiet` to QUIET, `two_sided` to CHOP, `one_sided` to TREND and `mixed` to ACTIVE.

### Better Setup Soon

Probability a same-horizon path opportunity occurs in the next three rounds. It supports WAIT discipline; it does not guarantee a cheap share or profitable fill.

### Execution Check

Uses a fresh exact-round quote from `pm_live_quotes.json`. Without an ask, depth and fee-adjusted edge, status is `WAITING FOR LIVE BOOK`. With a quote, the existing Champion still decides whether the conservative paper edge clears.

## Action Mapping

| Display | Meaning |
|---|---|
| WAIT | evidence or executable edge is incomplete |
| AVOID | Champion reports a risk conflict/stale state |
| PAPER | Champion found a positive simulated quote edge; still not live-betting approval |

No BUY/SELL order route was added. `round_state.champion_unchanged` is always true.

## Recorder State

Two independent no-order collectors were started:

1. `live_btc_updown_recorder.py`: REST `/book`, top ask/bid, depth bands, P(Hold), quote bridge and official settlement into `execution_layer.duckdb`.
2. `l2_recorder.py`: WebSocket full snapshots, level changes, trades, exact size-specific VWAP and book integrity into `polymarket_l2.duckdb`.

At startup the REST recorder intentionally skips a round discovered more than five seconds after its anchor. Therefore `pm_live_quotes.json` can temporarily contain an empty `markets` object until the next clean 5m/15m boundary. This prevents a fabricated anchor.

The L2 recorder reconnects automatically after network/WebSocket failures. Logs:

```text
data/pm_live_recorder.stdout.log
data/pm_live_recorder.stderr.log
data/pm_l2_recorder.stdout.log
data/pm_l2_recorder.stderr.log
```

`start.bat` invokes `start_recorders_once.ps1`. Existing writer processes are detected and reused, so refreshing or restarting the app does not create duplicate DuckDB writers. The three independent skip flags are `BTC_SKIP_PM_RECORDER`, `BTC_SKIP_PM_L2_RECORDER`, and `BTC_SKIP_MICROSTRUCTURE_RECORDER`.

Exact L2 is high-volume. The launcher applies a 10 GB `DB + WAL` safety cap; the collector stops cleanly at that limit rather than filling the laptop disk. Override with `BTC_PM_L2_MAX_GB` or the recorder's `--max-db-gb` option. A zero value disables the guard and is not recommended.

## Verification Performed

- trainer self-test
- scorer/composer self-test
- Python compilation for all modified backend modules
- purged full historical training
- artifact load and one-row inference
- complete `PriceToBeatTracker` integration test
- live recorder self-test
- exact L2/VWAP/queue self-test
- Vite production build
- both recorder processes confirmed running and writing

## Remaining Gates

1. Collect at least several hundred independent quote+settlement rounds.
2. Measure live calibration for every shadow head by horizon and seconds-left bucket.
3. Confirm the 30-second historical versus live sampling approximation does not drift.
4. Replay entries at actual ask and exits at bid/VWAP after fees and latency.
5. Require positive net EV, profit factor, drawdown control and a confidence interval excluding zero.
6. Only then consider whether any shadow field deserves Champion influence.

Until those gates pass, this is better decision support, not a profitable bot claim.
