# Investigation of every open audit claim — `2026-08-06`

Closes the UNVERIFIED list in
[`CORE_AUDIT_VALIDATION_2026-08-06.md`](CORE_AUDIT_VALIDATION_2026-08-06.md). Every remaining
claim from scans 2 and 3 has now been read against source and given a verdict.

**Nothing here is fixed.** This is the investigation, so that fixes are aimed at the real
mechanism. Where a claim is right about the symptom and wrong about the cause, that is recorded
— a wrong mechanism produces a wrong fix, which is how two earlier repairs in this repository
had to be reverted.

Verdict vocabulary:

```text
CONFIRMED          read in source, mechanism matches the claim
CONFIRMED-ADJUSTED read in source, real, but the stated mechanism needed correction
SHAPE-CONFIRMED    the structural claim holds; the specific failure was not reproduced
NOT ESTABLISHED    could not be confirmed from static reading; do not act on it yet
```

---

## Scan 2

### CONFIRMED — 17 of 21

| # | claim | the evidence |
|---|---|---|
| 2.3 | historical cache never re-fetches its last candle | `server.py:1351` requests `start_time_ms = last_time*1000 + interval_ms` — exactly one interval past the last cached bar, so **zero overlap**. An incomplete final row can never be corrected. REST rows carry `close_time` (`data_ingestion.py:532`) and nothing filters on it. |
| 2.5 | signal-history rollover race | `server.py:1629` `signal_buffer.record(last_kline["time"], data_state)` stamps the **previous** candle's timestamp onto the **current** global state. Trades/depth from the new minute that arrive before the new kline message are attributed to the closed candle. |
| 2.9 | full-refit shadow loses its HMM | `grep -c "full_model.hmm_state"` = **0**. `full_model` is trained with the candidate's `regime_labels` (`server.py:2408`) and never receives the corresponding state. `_save_models` writes `getattr(self, "hmm_state", None)` → the shadow saves `None`. |
| 2.10 | incumbent evaluated under the candidate's regimes | `model_promotion.py:243` and `:249` pass the **same** `sampled_regimes` to candidate and incumbent, and those labels came from the candidate's HMM. |
| 2.12 | "raw model A/B" is not raw | `ab_testing.py:34` calls `generate_ensemble_prediction()`, which applies smoothing, direction locking and calibration; the result is stored under `RAW_MODEL_COMPARISON = "raw_model_output"`. |
| 2.13 | frozen evidence still adapts | `server.py:4118` states it outright: *"NOT gated by MODEL_FROZEN"*. `MODEL_FROZEN` gates retraining only. |
| 2.15 | meta-model trains across eras, and mixes cost units | `meta_model.py:68` filters on `resolved / hit / confidence / binance_price` and **no** release, schema or contract column. `:84` adds `binance_price * 0.0010` (per-1-BTC) to `expected_slippage_usd` (scaled to the simulator's position size). |
| 2.18 | A/B memory is unbounded | `ab_testing.py:26,27,84` — `self.predictions`, `self.verified`, `self.comparison_log` are plain lists with no `maxlen`. |
| 2.19 | ambiguous-mask mismatch fails open | `model.py:1080` logs *"valid_mask length … != labels … - NOT applying it"* and continues; ambiguous rows then train as NEUTRAL. |
| 2.20 | settlement-head purge controls unwired | `settlement_head.py:175` accepts `groups=None, lookback: int = 0`. The only production caller (`server.py:2293`) passes neither → purge is `horizon` alone and independence validation is off. |
| 2.22 | split timestamps are interpolated | `model.py:3399` `split_ts = start_ts + (end_ts - start_ts) * train_split_frac`, and the code **flags itself**: `"estimated_from_matrix_span": True`. Worse than stated — it interpolates over the *matrix* span, which per 2.1 is not the training data. |
| 2.23 | GPU fits happen at import | Module-level `try` at `model.py:307` fits an `XGBClassifier(device="cuda")`, and `:320` fits `CatBoostClassifier(task_type="GPU")`. Confirmed module-scope by AST. Runs before lifespan and preflight; a native driver fault is not containable by `except`. |
| 2.24 | preflight is too late to detect missing storage | `server.py:146` `os.makedirs(DATA_DIR, exist_ok=True)` at module scope. The directory exists by the time the lifespan preflight claims it can reject a missing one. |
| 2.25 | migration failures read as "column exists" | 37 `except Exception:` handlers in `database.py`; the `ALTER TABLE` loop swallows permission, lock, version and corruption failures identically. |
| 2.26 | order-flow features use wall clock | `order_flow.py:365` `now = time.time() * 1000`; the same trade buffer yields different features depending on **when** it is called. |
| 2.27 | "local" extremes are process-lifetime extremes | `order_flow.py:38` initialises once and `:152–156` only ratchets. Never windowed, never reset — so sweep detection drifts toward "extreme since restart". |
| 2.28 | coverage counts rows, not usable features | `signal_history.py:298` `sum(1 for ts in candle_timestamps if ts in self.by_ts)` — pure key existence. A row with every source missing and defaulted counts as covered. |

### SHAPE-CONFIRMED — 3

| # | claim | what holds, and what does not |
|---|---|---|
| 2.4 | REST/WS kline schemas are incompatible | Holds: REST builds `close_time` (`data_ingestion.py:532`) and no `is_closed`; WS builds `is_closed` (`:226`, `:971`) and no `close_time`. Neither carries `event_ts_ms`/`receive_ts_ms`. This is the shared root of 2.3, 2.11 and P0-4. |
| 2.6 | live inference overlays current microstructure onto the last closed candle | The overlay is **real and deliberate** — `features.py:946` documents it as the P0-3 workaround. The claim's force is not that it is a bug but that it is an *undeclared* asynchronous decision-time contract wearing the name of train/serve parity. That framing is correct. |
| 2.7 | the decision snapshot is abandoned during recording | Holds structurally: only 3 `decision_state[...]` reads in `server.py`, against many post-inference `data_state["klines"][-1]["close"]` reads (`:1704`, `:1990`, `:1995`, `:2010`). I did not reproduce a specific t0/t1 divergence. |

### NOT ESTABLISHED — 1

| # | claim | why |
|---|---|---|
| 2.16 | the revision ledger stamps an old price with a later timestamp | The schema does carry three distinct columns — `snapshot_ts` (`:103`), `prediction_ts` (`:119`), `observed_ts` (`:141`). Whether the write path assigns the wrong one to a markout's admissibility test needs a runtime trace, not a read. **Do not act on this yet.** |

---

## Scan 3

| # | claim | verdict |
|---|---|---|
| 3.6 | Binance paper is a multi-strategy portfolio, not "the main model" | **CONFIRMED.** Several strategies run independently and only `model_consensus` consumes the main decision. The claim is about labelling, and it is right: combined paper P&L must not be reported as main-model performance. |
| 3.7 | Polymarket advice reuses the main model across a target mismatch | **CONFIRMED.** A first-touch, rolling-horizon model is presented as a fixed-anchor settlement lean. Same defect family as 1.4/3.2, which is now refused on the Binance side and **still open here**. |
| 3.8 | the settlement source is not unified | **CONFIRMED.** `price_to_beat.py:1580` carries `self.source` with Pyth as the anchor and a converted Binance fallback, while the venue settles on Chainlink. Four distinct quantities, as claimed — compounded by 2.30, where CoinGecko is stored under `chainlink_price`. |
| 3.9 | head permissions use two namespaces and two authorities | **CONFIRMED.** Registry name `"persistence"` (`model_registry.py:76`) vs health name `"p_hold"` (`head_permissions.py:172`). The registry asserts `authority("persistence")["may_price"] is False` while the dynamic report can grant `may_price`. |
| 3.10 | head health is not bound to the loaded artifact SHA | **SHAPE-CONFIRMED.** The permissions reader keys on head name and state, not on the SHA in memory. I did not construct the swap race, so the *possibility* is established and the *occurrence* is not. |

---

## What this changes about priority

Three findings are more consequential than their original ranking suggested.

**2.23 (GPU fits at import) is a startup-safety issue, not a P1.** It fits two real models before
preflight runs, and a native CUDA fault is not catchable by `except`. It is also the cheapest
fix on this list: move the probe behind a subprocess or an env flag.

**2.22 compounds 2.1.** The declared train/holdout boundary is interpolated across the *matrix*
span — a dataset the model did not train on. So the split timestamps are an estimate over the
wrong series. Any "untouched period" claim inherits both errors.

**2.4 is the shared root of three separate P0s.** 2.3 (no cache overlap), 2.11/P0-4 (bar-open
resolution timestamps) and part of 2.5 all trace to there being no canonical kline schema
carrying `open_ts_ms`, `close_ts_ms`, `event_ts_ms`, `receive_ts_ms` and `is_closed`. Fixing
those three separately would be three partial repairs of one defect.

### Suggested order, revised by this investigation

```text
1. 2.23  GPU probe out of import        cheap, and it precedes every safety check
2. 2.4   one canonical kline schema     unblocks 2.3, 2.5 and P0-4 together
3. 2.1   executed-training snapshot     the keystone; 2.22 rides along with it
4. 1.7   prediction contract columns    unblocks 1.6, 1.4, 3.7 and calibration
5. 2.9   bind the shadow's HMM          one assignment; today the shadow saves None
6. 2.19  mask mismatch must terminate   fail-open in the label path
7. 2.20  wire lookback + groups         two arguments at one call site
8. 2.13  freeze the whole decision release, not just model files
9. 2.10 / 2.12 / 2.18  A/B isolation and bounded memory
10. everything else
```

Items 5, 6 and 7 are each a handful of lines and are the best value-per-risk on the list.

---

## Standing status

```text
claims received across three scans   56
investigated                         56   (0 remaining unverified)
CONFIRMED                            40
CONFIRMED-ADJUSTED                    3
SHAPE-CONFIRMED                       4
NOT ESTABLISHED                       1   (2.16)
fixed so far                         15
local CI                             161 steps, 1 FAIL (check_feature_contract, documented)
real-money authority                 NONE
```
