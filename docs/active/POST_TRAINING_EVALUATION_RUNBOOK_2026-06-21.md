# Post-Training Evaluation Runbook - 2026-06-21

## Scope Lock

Do not change model logic, thresholds, features, labels, horizon ownership, or ensemble membership during
the retrain or its measurement window. After training, evaluate only:

1. P(Hold) calibration by horizon;
2. retained-call precision for 5m and 15m;
3. the regime gate in shadow;
4. `champion_v2` versus plain P(Hold) at matched coverage;
5. Polymarket quote+official-settlement edge.

No new model family is justified until these five measurements produce enough forward evidence.

## Phase 1 - Confirm Training Finished

Do not stop the app when model fitting merely reaches its final horizon. Wait for all of these:

- terminal log: `Background startup training complete`;
- UI relearn state: `complete` / not running;
- `data/saved_models/architecture_version.pkl` has a new modification time;
- no training failure traceback;
- predictions resume for 1m, 5m, and 15m.

Run this after the completion log:

```powershell
python backend\check_model_compatibility.py
```

Required result: exit code `0` and `Compatible saved main ensemble`. If it still reports the old
seven-horizon architecture, the retrain did not save successfully. Do not use `start_instant.bat`.

## Phase 2 - Accrue Clean Forward Evidence

Keep the app and Polymarket recorder running after training. Immediate analysis mostly contains predictions
from the previous model and cannot validate the new ensemble.

For the new `model_version`, wait for:

| Evidence | Minimum before a decision | Preferred |
|---|---:|---:|
| Resolved retained 5m calls | 100 | 250+ |
| Resolved retained 15m calls | 100 | 250+ |
| UP calls per horizon | 30 | 75+ |
| DOWN calls per horizon | 30 | 75+ |
| Post-training regime-gate rounds | 250 | 500+ |
| Polymarket rounds with quote + official outcome | 500 | 1,000+ |

Do not combine old and new model versions when judging retained-call precision. The metrics report includes a
`model_version x horizon` section; use the newly saved bundle only.

## Phase 3 - Stop Cleanly for Offline Analysis

After enough rounds accrue, stop the backend with `Ctrl+C` and close the recorder cleanly. This avoids
Windows DuckDB writer-lock conflicts. Do not retrain or edit code between stopping and producing reports.

## Evaluation 1 - P(Hold) Calibration by Horizon

```powershell
python backend\calibration_monitor.py
```

Use report-only mode. Do **not** use `--recalibrate` during the evaluation.

Record for 1m, 5m, and 15m:

- sample count;
- ECE;
- Brier score;
- realized rates at P(Hold) >= 0.85, 0.90, 0.93, and 0.95;
- predicted-minus-realized drift.

Decision rules:

- **PASS:** ECE <= 0.03 and no well-sampled high-confidence tier is more than 3 points optimistic.
- **WATCH:** ECE 0.03-0.05 or one tier is 3-4 points optimistic.
- **FAIL:** ECE > 0.05 or repeated high tiers are more than 4 points optimistic.

Judge each horizon separately. A good overall ECE cannot hide a drifting 1m head.

## Evaluation 2 - 5m/15m Retained-Call Precision

```powershell
python backend\research\standalone\analyze_duckdb_metrics.py
python backend\research\standalone\analyze_timeframe_performance.py --source pyth --hours 168 --last 250
```

Read only the current model version and retained/actionable UP or DOWN calls. For each horizon capture:

- retained directional calls and abstention/coverage;
- retained-call accuracy;
- precision(UP) and precision(DOWN);
- support for each side;
- recent-window result;
- Wilson lower bound where available.

Decision rules:

- Do not judge a horizon below 100 retained calls or with fewer than 30 calls on either side.
- **Candidate precision edge:** point precision above 55% and Wilson lower bound above 50% on the full
  post-training window, without collapse in the recent window.
- **No edge:** Wilson lower bound <= 50%, severe UP/DOWN imbalance, or improvement exists only in one small
  day/regime slice.

Higher abstention is acceptable when retained precision improves. Coverage must always be reported beside
precision; a 70% result from five calls is not evidence.

## Evaluation 3 - Regime Gate Shadow

```powershell
python backend\regime_gate_shadow.py --recent 250
```

Keep this shadow-only. Promotion requires all of the following:

- at least 250 genuinely post-training rounds;
- overall Wilson lower bound above 50%;
- a non-overlapping recent forward window also above 50%;
- 5m and 15m do not show a material contradiction;
- the result is not carried by one tiny regime.

Any recent-window lower bound at or below 50% means **do not promote**.

## Evaluation 4 - champion_v2 Versus Plain P(Hold)

```powershell
python backend\champion_v2_shadow.py --source pyth --split 0.70 --recent 250
```

Compare `meta-skip top25` with `P(Hold) top25` and `meta-skip top10` with `P(Hold) top10` at matched
coverage. Do not compare unmatched trade counts.

Decision rules:

- Keep plain P(Hold) when the matched-coverage improvement is below 2 percentage points.
- Consider `champion_v2` only when both top-25% and top-10% cuts improve, the lift survives the temporal
  holdout/recent window, and accepted support is at least 200 rounds.
- High held-rate alone is not profit; it must also clear the market-price test below.

## Evaluation 5 - Polymarket Quote + Settlement Edge

```powershell
python backend\polymarket\live_btc_updown_recorder.py --report
python backend\polymarket\analyze_pm_recorder.py
```

Only rounds containing all of these count:

- P(Hold) captured at decision time;
- executable held-side ask;
- official CLOB/Gamma outcome;
- one entry per market.

Hard gate before paper/live execution:

- at least 500 joined quote+outcome rounds;
- positive average ROI after realistic costs;
- positive result at a buffer of at least 3 cents;
- result is not concentrated in a few markets or one side;
- stability across 5m/15m and recent windows.

Fewer than 500 joined rounds means **INSUFFICIENT DATA**, regardless of the displayed ROI.

## Final Decision Table

| Result | Action |
|---|---|
| Calibration passes; retained precision passes; Polymarket edge insufficient | Keep recording; no execution |
| Calibration passes; retained precision fails | Preserve abstention; do not add models; review selection only |
| Regime/champion fails to beat baseline | Keep plain P(Hold); leave shadows unpromoted |
| Polymarket edge positive at >=3c over >=500 joined rounds | Begin paper-trading design, not real money |
| Polymarket edge flat/negative | Do not build an execution bot for this thesis |

## Final Principle

The code is now structured for high precision. The next improvement must come from cleaner selection,
calibration, and recorder evidence. Another model family is not the next step.
