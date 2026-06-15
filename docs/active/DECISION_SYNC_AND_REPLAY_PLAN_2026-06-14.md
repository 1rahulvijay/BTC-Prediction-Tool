# Decision Sync And Historical Replay Plan - 2026-06-14

This note records the current enhancement pass: what changed now, what does not need
training, what needs calibration, and how to use historical unseen data without fooling
ourselves.

## Short Answer: Can We Force-Create Data From Historical Unseen Data?

Yes, but only as **offline replay / paper backtest data**.

Historical unseen data can help with:

- confidence calibration
- BUY / SELL / AVOID threshold tuning
- regime-specific skip rules
- challenger model evaluation
- expected-move error measurement
- deciding whether a setup deserves live promotion

Historical unseen data cannot fully replace live data because:

- live exchange feeds disconnect or lag
- order book and trade streams can be missing, stale, or geo-blocked
- slippage and spread at execution time differ from candle history
- real-time features may not exist in historical candles
- crypto regime behavior decays quickly

The rule is simple: replay must predict timestamp `t` using only data available before
`t`. If any future candle, future close, final window result, or after-the-fact feature
leaks into the prediction row, the result becomes fake accuracy.

## Implemented In This Pass

These changes do **not** require model retraining.

| Change | File | Purpose |
|---|---|---|
| Added plain label for `wide_target_range` | `backend/decision_gate.py` | Prevent raw backend reason text from reaching the UI. |
| Preserved true model raw direction | `backend/model.py`, `backend/server.py` | Separate ensemble raw lean from locked/server-filtered/final direction. |
| Added locked/pre-server/final direction fields | `backend/model.py`, `backend/server.py` | Makes prediction flow auditable. |
| Preserved model confluence separately from setup quality | `backend/model.py`, `backend/server.py` | Stops one `confluence` field from meaning two different things. |
| Added final action fields after all filters | `backend/server.py` | Final action now reflects the last gate, not a stale pre-filter state. |
| Added DuckDB audit columns | `backend/database.py` | Persist raw/pre-server/final direction, trade verdict, reasons, and setup quality. |
| Added decision-state JSON logging | `backend/server.py`, `backend/database.py` | One compact record of why the model became BUY/SELL/AVOID. |
| Added historical replay table/helper | `backend/database.py` | Offline replay rows stay separate from live accuracy. |
| Added historical replay runner | `backend/historical_replay.py` | Manual CLI to run saved models on unseen candles without polluting live stats. |
| Added forward-EV paper ledger | `backend/database.py`, `backend/server.py` | Measures TRADE net PnL and AVOID avoided-loss/opportunity-cost after outcomes resolve. |
| Added P(hold) model status | `backend/price_to_beat.py`, `backend/server.py`, `src/main.js` | UI/API can show loaded/missing/disabled instead of silent absence. |
| Added replay/EV APIs | `backend/server.py` | `/api/historical-replay` and `/api/forward-ev`. |
| Added verifier context fields | `backend/prediction_verifier.py` | Resolved examples can retain clean decision state. |
| Made UI action stricter | `src/main.js` | UP/DOWN lean does not become BUY/SELL unless the gate says TRADE/actionable. |
| Added structured reason text to key panels | `src/main.js` | Scoreboard and flow panels show why the app says wait/avoid. |

## New Prediction-State Meaning

The backend now carries several direction fields instead of forcing one field to mean
everything.

| Field | Meaning |
|---|---|
| `modelRawDirection` | What the ensemble wanted before direction lock/hysteresis. |
| `rawDirection` | Backward-compatible raw lean field, now preserved from the model. |
| `lockedDirection` | Direction after hysteresis / anti-flip lock. |
| `modelFilteredDirection` | Direction after model-side internal filters. |
| `preServerDirection` | Direction received by the server before live quality gates. |
| `preNeutralDirection` | Last directional value before a neutralizer changed it to NEUTRAL. |
| `finalDirection` | Final direction after every server filter. |
| `finalSignal` | Final signal label after every server filter. |
| `trade_verdict` | `NO_TRADE`, `WEAK_LEAN`, or `TRADE`. |
| `finalAction` | UI/action alias for the final verdict. |

This is important because the old flow could show a target, neutral result, raw lean,
and action label that did not reconcile cleanly.

## DuckDB Fields Added

Each `predictions_{horizon}m` table now gets additive audit columns:

- `model_raw_direction`
- `pre_server_direction`
- `final_direction`
- `trade_verdict`
- `no_trade_reasons_json`
- `decision_state_json`
- `model_confluence`
- `setup_score`
- `setup_quality_json`

These columns allow later queries such as:

- How often did raw UP become final AVOID?
- Which no-trade reason saved the most bad calls?
- Which setup-quality score actually had edge?
- Did model confluence or setup quality matter more?
- How far was expected move from realized move when direction was correct?

## What Still Does Not Require Training

These are safe next implementation tasks:

1. Full UI cockpit rewrite around one top-level question: "Do I act now?"
2. Add a P(hold) loaded/missing/warming status indicator.
3. Remove or fully restore inactive Kronos labels.
4. Add DuckDB queries for reason-by-reason success rates.
5. Add a forward-EV paper ledger for P(hold) and TRADE calls.
6. Add endpoint/cards for:
   - raw lean accuracy
   - final action accuracy
   - no-trade reason success
   - setup-quality performance
   - target-size error
7. Add historical replay output tables for offline calibration.

## What Requires Calibration, Not Full Retraining

These need enough resolved examples but do not require rebuilding the base models:

| Enhancement | Needed Data |
|---|---|
| Confidence thresholds by horizon | At least 100 resolved predictions per horizon. |
| BUY / SELL / AVOID thresholds | At least 100 resolved per action type. |
| Regime-specific skip rules | Enough samples per regime. |
| P(hold) tier thresholds | Resolved price-to-beat / persistence rows. |
| Expected-value cutoff tuning | Paper/live ledger with fees/slippage assumptions. |

## What Requires Training Or Rebuild

These should wait until the decision-state schema is stable:

| Enhancement | Why training/rebuild is required |
|---|---|
| New model features | Feature vector changes. |
| Feature retirement | Model input distribution changes. |
| Triple-barrier relabeling | Target labels change. |
| CatBoost direction model | New base model. |
| Stacking meta-model | Learns from base-model outputs and outcomes. |
| Move-size / quantile model | Separate target for expected dollar move. |
| TCN sequence model | New deep model requiring sequence training. |
| PPO strategy | Needs a reward environment and paper-trade validation. |

## Correct Offline Replay Design

The historical replay should create a separate table, not overwrite live prediction
truth.

Implemented table:

```sql
historical_replay_predictions(
    id,
    replay_run_id,
    timestamp,
    horizon,
    model_bundle_id,
    feature_schema_hash,
    model_raw_direction,
    final_direction,
    trade_verdict,
    confidence,
    expected_move,
    target_price,
    actual_price,
    actual_move,
    direction_hit,
    price_match,
    move_error,
    no_trade_reasons_json,
    setup_quality_json,
    created_at
)
```

Manual runner:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' backend\historical_replay.py --days 7 --horizons 5 15 --max-samples 1000
```

Run safety:

- Do **not** combine this with `.\start.bat` in one PowerShell command.
- Best practice: stop the backend first, then run replay against the normal app DB.
- If the live backend is running, DuckDB can reject a second writer process. To avoid touching
  the live DB, point replay to a separate file:

```powershell
$env:BTC_DB_PATH='C:\tmp\btc_replay.duckdb'
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' backend\historical_replay.py --days 7 --horizons 5 15 --max-samples 1000
```

That separate-DB mode is useful for a smoke test, but the live UI will not see those replay rows
unless the backend is also pointed at the same DB or the rows are imported later.

Useful options:

- `--start-ms` / `--end-ms`: replay a specific unseen date range
- `--horizons 5 15`: choose horizons
- `--max-samples 1000`: cap runtime
- `--stateful`: keep model smoothing/locks across replay rows; default resets locks and smoothing to avoid wall-clock hysteresis distortion

Replay results are read through:

- `/api/historical-replay`

## In-App Replay Runner

Implemented backend controls:

- `POST /api/historical-replay/run?days=7&horizons=5,15&max_samples=1000`
- `GET /api/historical-replay/status`
- WebSocket payload keys:
  - `replay_status`
  - `historical_replay`
  - `threshold_recommendations`

The runner executes inside the backend process. This avoids the Windows/DuckDB
second-writer conflict that can happen when `backend/historical_replay.py` is run
as a separate Python process beside the live app.

`backend/historical_replay.py` also lazy-loads heavy ML/native imports only when
an actual replay starts. This keeps `--help` and backend import lightweight and
avoids native-library teardown crashes from loading the full model stack just to
show CLI help.

Implemented UI:

- Decision Center -> Replay Proof Lab
- `Run 7-Day Replay` button
- live progress/status
- per-horizon replay scorecards
- threshold recommendations
- recent replay examples showing raw direction, final direction, expected move,
  actual move, and direction correctness

Replay summary fields:

- `rows`: total replay rows stored for that run/horizon
- `directional_n`: raw UP/DOWN model calls only
- `directional_hits`: raw UP/DOWN calls that matched the actual price direction
- `directional_accuracy`: `directional_hits / directional_n`
- `price_match_rate`: directional calls where the direction was right and the dollar move was close to expected
- `avg_move_error_usd`: average absolute error between expected move and actual move

## Forward-EV Ledger

Implemented table:

```sql
forward_ev_ledger(
    id,
    prediction_id,
    source,
    timestamp,
    horizon,
    entry_price,
    target_price,
    expected_move,
    confidence,
    raw_direction,
    final_direction,
    trade_verdict,
    action,
    notional_usd,
    fee_bps,
    slippage_bps,
    no_trade_reasons_json,
    setup_quality_json,
    resolved,
    exit_price,
    actual_move,
    actual_direction,
    direction_hit,
    gross_pnl_usd,
    fees_usd,
    slippage_usd,
    net_pnl_usd,
    avoided_loss_usd,
    opportunity_cost_usd,
    resolved_at
)
```

Meaning:

- `TRADE`: scored as paper net PnL after fee/slippage assumptions
- `NO_TRADE` / `WEAK_LEAN`: scored as avoided loss or opportunity cost versus the raw model lean

Read through:

- WebSocket payload: `forward_ev`
- REST: `/api/forward-ev`

Environment controls:

- `BTC_PAPER_NOTIONAL_USD` default `1000`
- `BTC_TAKER_FEE_BPS` default `4.0`
- `BTC_PAPER_SLIPPAGE_BPS` default `2.0`

Replay rules:

1. Use a frozen model bundle.
2. Use a date range never used for training that bundle.
3. Generate features only from candles/signals available before the decision time.
4. Apply the lightweight final decision vocabulary (`NO_TRADE`, `WEAK_LEAN`, `TRADE`) without importing the live server process.
5. Resolve against future candles only after the prediction row is created.
6. Use replay only to calibrate thresholds or evaluate challengers.
7. Do not mix replay rows into live accuracy tables.

Current replay limitation:

- Replay uses historical candles plus the saved model bundle. It does not yet reconstruct full
  real-time order-flow, feed-staleness, spread, Coinbase, Bybit, Polymarket, Kronos, or latency
  conditions for every historical timestamp. That means replay is useful for model and target-error
  calibration, but live resolved predictions remain the source of truth for real trading behavior.

Threshold recommendations are intentionally read-only:

- Replay and forward-EV can say `raise_threshold`, `raise_or_skip`,
  `widen_target_tolerance`, or `keep_current_gate`.
- The app does **not** silently change model thresholds from these recommendations.
- Manual review is required before changing live trading gates.

## Recommended Next Build Order

1. Run `backend/historical_replay.py` on a truly unseen date range and review `/api/historical-replay`.
2. Add reason-by-reason DuckDB analytics.
3. Extend the forward-EV ledger to explicit P(hold) T2/T3 events, not just ensemble predictions.
4. Redesign the UI home screen as an abstention cockpit.
5. Calibrate thresholds from replay plus live resolved rows.
6. Only then retrain/add new models.

## Accuracy Principle

The path to higher win rate is:

```text
more honest state -> better measurement -> stricter gates -> fewer bad trades -> higher precision
```

Do not force the app to produce more BUY/SELL signals. Force it to explain why a signal
is worth acting on, and make it prove that exact setup has worked before.
