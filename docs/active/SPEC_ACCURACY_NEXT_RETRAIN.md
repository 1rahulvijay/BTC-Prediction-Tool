# Spec — Accuracy: the real bottleneck and the next-retrain plan

**Status:** DESIGN (no code yet). Written 2026-06-13 after a code-level diagnosis. Gated on the
§10 24h number in [MEASUREMENT_WINDOW_2026-06-13.md](MEASUREMENT_WINDOW_2026-06-13.md).
**Companion to** [V5.md](V5.md) (the THREE precisions) — this is the *information* lever (Path B).

---

## 0. TL;DR

The model is at ~0.51 sign-truth not because it's broken and not because features are missing, but
because the **highest-edge features are present in serving yet effectively ABSENT in training**.
The trees learn from the features that *vary across history* (kline- and aggTrade-derived = the
priced-in, low-edge ones) and ignore the live-only microstructure features (L2 depth, walls, queue
dynamics, liquidations) because those are **constant across the training matrix**. Fixing accuracy =
closing that train/serve gap, not adding more indicators.

---

## 1. The diagnosis, proven from the code

1. **All five base learners converge to ~0.51 OOF** (xgb/lgb/cat/dl/lr). Independent model families
   agreeing at coin-flip ⇒ the ceiling is in the *data*, not model capacity. Tuning/bigger models
   cannot move it.
2. **The training matrix is built with a single live snapshot broadcast over 50 days of history.**
   `server.py:1160` — `build_features_from_klines(kl_snapshot, data_state["order_flow"],
   data_state["derivatives"], data_state["sentiment"], signal_history=sig_hist)`. `kl_snapshot`
   is full history; `order_flow`/`derivatives`/`sentiment` are the **current** snapshot;
   `signal_history` only covers the recently-accumulated live window. So for the bulk of history,
   the live-only features do not vary → a constant column → **the trees cannot split on it** →
   zero learned influence.
3. **The high-edge features are exactly the live-only ones** (feature slots, `features.py`
   `FEATURE_NAMES`): liquidations (42–45), liquidity walls/vacuum (52–56), deep microstructure
   (61–67), advanced microstructure / absorption / queue (68–72), deep order flow incl.
   `cross_exchange_lead_lag` (97–100). These are precisely where sub-15m edge lives — and precisely
   what's missing from training.
4. **The features that DO vary across history are low-edge by construction.** The V3+ batch
   (109–129) was deliberately chosen to be backfillable — "All DERIVED from the already-consistent
   cvd_1m series … train/serve consistent" (`features.py:508-510`). Good discipline, but it means
   training leans on kline/aggTrade-derived signal, which is largely priced in at 5m.

**Conclusion:** the model is honestly reporting the edge available in its *trainable* features —
which is ~none at 5m. The untapped edge is sitting in the live microstructure feed, untrained.

---

## 2. Three tracks (do all; they're complementary)

### Track A — Selectivity / precision gating  *(fastest; raises EFFECTIVE precision; already partway)*
You don't need 55% everywhere, only on the calls you act on. Harvest the proven cells
(3m LOW_VOL 56.5%, 10m LOW_VOL 54.4%), stay silent elsewhere. This is the gate + conviction work
(Options A/B in the measurement doc). **No retrain needed.** It lifts committed-lean precision now
without touching base accuracy. Decision deferred to the 24h re-check (operator chose Option C).

### Track B — Activate the live microstructure features in training  *(highest ceiling; the real unlock)*
The only way the model can learn from L2 depth / walls / queue / liquidation features is to train on
data where those features **actually vary with the outcome**. Two sub-options:

- **B1 — Live feature+outcome logging → train on accumulated rows (RECOMMENDED).**
  Persist the *full* `NUM_FEATURES`-vector at prediction time, paired with the realized horizon
  outcome, to a dedicated store. After accumulating ≥3–4 weeks (ideally 6–8) of live rows spanning
  multiple regimes, train/fine-tune on this set where the microstructure columns are real.
  - **Why it works:** these rows have genuine per-bar L2/liquidation values, so the trees can
    finally split on them. This is the single highest-impact move in this doc.
  - **Cost:** time-to-data (weeks). Start logging immediately so the clock runs in the background.
- **B2 — Self-recorded microstructure backfill (parallel, optional).**
  Record raw `depth20@100ms` + `forceOrder` to disk continuously now; once enough history exists it
  becomes a backfill source like `trade_features.py`. Heavier storage; B1 subsumes most of its value
  because B1 logs the *already-computed* features. Prefer B1 unless we need to recompute features
  with new definitions over the recorded raw stream.

### Track C — Add backfillable multi-venue FLOW features  *(medium speed; additive edge that IS train/serve consistent today)*
Among features we *can* backfill (so they help the very next retrain, no waiting), the ones with
genuine 5m edge not yet present are **cross-venue aggressive flow**. Binance-only flow is partly
priced in; *divergence* between venues leads price. Coinbase and Bybit both publish historical
trades, so these are train/serve consistent immediately (mirror `backfill_trade_features.py`).

---

## 3. Track C feature spec (the concrete, buildable-now additions)

Append only — new slots after 129 (never reorder 0–129; saved models index by position). Bump
`MODEL_ARCH_VERSION`. Each must have BOTH a live recorder and a `data.binance.vision`/exchange
historical backfill computing the identical value (the keystone pattern in `trade_features.py`).

> **⚠ POST-V7 UPDATE (2026-06-13) — this table's slot numbers AND venue choice are SUPERSEDED.**
> - **Slots 130–135 are now taken** by v7's kline/time bundle: `variance_ratio` (130),
>   `rv_term_structure` (131), `session_asia/eu/us` (132–134), `is_weekend` (135). See
>   [IMPLEMENTATION_QUEUE.md](IMPLEMENTATION_QUEUE.md) / change-audit §5bl. So any cross-venue
>   feature appends at **136+**, not 130.
> - **The Coinbase/Bybit approach below was replaced by Binance spot-vs-perp** in the actual
>   builder `build_crossvenue_flow.py` (it produces `cvd_divergence`, `perp_spot_basis_bps`):
>   Coinbase publishes no bulk trade history, so a Coinbase feature could never be backfilled and
>   would re-create the train/serve gap. The current, parity-correct A4 spec lives in
>   [DATA_COLLECTORS.md](DATA_COLLECTORS.md) + [V8_ROADMAP.md](V8_ROADMAP.md); it is DEFERRED until
>   the live perp-CVD recorder is bridged into the per-bar buffer (see V9 ledger PART 2).
> - `rv_term_structure` already SHIPPED at slot **131** in v7 (not 135 as row 6 below proposes).
>
> The table is kept verbatim for historical design context only — do not implement it as written.

| slot | name | definition | live source | backfill source |
|---|---|---|---|---|
| 130 | `cvd_cb_binance_div` | Coinbase CVD minus Binance CVD, per 1m bar, z-scored | existing `CoinbaseWebSocketClient` aggTrades + Binance aggTrade | data.binance.vision SPOT + Coinbase `/products/BTC-USD/trades` history |
| 131 | `cvd_bybit_binance_div` | Bybit CVD minus Binance CVD, per 1m bar, z-scored | add Bybit `publicTrade` WS | Bybit `/v5/market/recent-trade` + kline history |
| 132 | `flow_lead_lag_cb` | signed lagged cross-corr of Coinbase vs Binance 1s returns (who leads) | both WS | aligned historical trades |
| 133 | `aggressive_buy_ratio_multi` | (multi-venue taker-buy vol) / (total taker vol), 1m | all aggTrade feeds | all historical trades |
| 134 | `large_print_venue_skew` | net large-print (≥EWMA threshold) direction, Coinbase+Bybit vs Binance | aggTrades w/ size filter | historical trades w/ same EWMA threshold |
| 135 | `rv_term_structure` | rv_1m / rv_15m (short-vs-long realized-vol ratio) — kline-derived, free history | klines | klines |

Notes:
- 130–134 ride the **SAME EWMA thresholds and 1m-bucketing** as the existing CVD/large-trade path
  so live == backfill exactly (the train/serve invariant that 109–129 already honor).
- 135 is kline-derived ⇒ full history immediately, zero recording wait; cheapest win, add first.
- Validate each new column on a backfill day with the existing `--validate` harness before retrain.

---

## 4. Track B1 logging spec (start NOW; it's the long-pole)

- **New store:** `feature_outcome_log` (DuckDB table or daily parquet). Columns: `timestamp`,
  `horizon`, `regime`, `raw_direction`, `feature_vector` (BLOB/array of `NUM_FEATURES` floats at
  prediction time), `schema_hash` (from `features.get_feature_schema()` — so a later schema change
  is detectable), then on resolution `actual_move`, `sign_truth_label`.
- **Hook:** at prediction emit (where `modelDirs`/`rawDirection` are set), persist the live
  feature vector keyed by a prediction id; on resolution (the verifier path) write the outcome.
  Reuse the existing pending→resolve plumbing (`prediction_verifier`).
- **Schema-hash guard:** only rows whose `schema_hash` matches the current `FEATURE_NAMES` are
  trainable together; on a schema bump, old rows are still usable for the overlapping columns or
  archived. Document this so a future feature add doesn't silently corrupt the set.
- **Training path:** a new mode that builds the training set from `feature_outcome_log` (real
  microstructure) and optionally *unions* it with the historical-backfill matrix for the
  backfillable columns. Class-balanced loss + per-candle ATR labels (V5 §1/§2.5a) carry over.
- **Acceptance:** SHAP/gain on the live-trained model shows the microstructure slots (52–72) now
  among the top splitters — direct proof the gap closed. Then check 5m sign-truth.

---

## 5. What will NOT help (stop spending retrains here)
- More TA indicators (RSI/MACD/BB/ADX variants) — derived from the same public candle, ~0 marginal
  edge at 5m.
- Bigger/deeper models or more estimators — you're at the *data* ceiling, not capacity. The five-way
  0.51 convergence is the proof.
- Re-running the retrain on the **same** 130 features — reshuffles noise; 0.51 won't move.
- Adding more live-only features without Track B — they'll be constant-in-training too, i.e. dead
  weight, exactly like 52–72 are today.

---

## 6. Realistic targets (so "better" is measurable)
- 5m sustained **55%** committed-lean sign-truth = genuinely good and bettable with the gate.
- **58–60%** on a high-grade selective subset = excellent.
- 3m LOW_VOL is already **56.5%** — a real, usable edge today; Track A harvests it immediately.
- No method sustains ~70% at 5m; calibrate expectations to "thin edge, harvested selectively."

---

## 7. Sequencing (gated on the 24h number)

1. **Now, regardless:** start **Track B1 logging** (long-pole; the clock should already be running).
   (`rv_term_structure` — once proposed here as a slot-135 add — already SHIPPED at slot **131** in
   the v7 bundle, along with `variance_ratio` and the session flags.)
2. **At the 24h re-check:**
   - If 5m committed-lean **≥56% & balanced** → direction edge is real → prioritize **Track A**
     (harvest) + ship **Track C** multi-venue flow in the next retrain; magnitude/path (V5 #2/#3)
     follow.
   - If still **~50%** (expected) → it's the information gap → ship **Track C** now AND lean on
     **Track B1** as the structural fix; do **not** add more live-only features until B1 can train
     them.
3. **When B1 has ≥3–4 weeks of rows** → the milestone retrain on live-logged data; this is the one
   most likely to actually break 0.51 at 5m.

---

## 8. Open items to verify at implementation time
- Confirm whether any of slots 40–72 are *partially* populated historically via `signal_history`
  aligned series (some recent-window coverage) vs fully constant — adjusts B1 urgency per feature.
- Confirm Coinbase/Bybit historical-trade endpoints' rate limits & granularity for the Track C
  backfill (mirror the `backfill_trade_features.py` caching pattern).
- Decide store format for B1 (DuckDB table vs daily parquet) given the existing ~1GB analytics.duckdb
  and OneDrive-off constraint.
