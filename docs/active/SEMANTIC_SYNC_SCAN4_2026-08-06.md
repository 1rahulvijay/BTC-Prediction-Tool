# Scan 4 — semantic synchronization defects — `2026-08-06`

A fourth scan, 24 new claims, on a different theme from the first three: **the app has the right
safety concept, but time, horizon, event identity or evidence population differs between the
producer and the consumer.**

Same discipline as [`OPEN_CLAIMS_INVESTIGATION_2026-08-06.md`](OPEN_CLAIMS_INVESTIGATION_2026-08-06.md):
verified from source before anything was changed.

---

## Fixed

### 1. A model lean could queue several entries before the first one filled — P0

`_evaluate()` asked only whether a position was already **OPEN**
(`portfolio.position_for(strategy_id)`). It never asked whether an entry for that strategy was
already **PENDING**. Evaluation runs on every `bookTicker` tick while the configured arrival
latency is hundreds of milliseconds, so several distinct OPEN signals could enter `_pending`
before the first filled — and `_process_pending()` re-checked the governor and price limits but
never whether an earlier intent had already taken the strategy's one allowed slot. The second
fill then collided with `portfolio.open()`.

Fixed at **both** ends of the window, because the window is where the collision lived:

- **queue time** — `_pending_entry_for(strategy_id)` suppresses a second intent;
- **arrival time** — an ENTRY whose strategy now holds a position is cancelled
  `position_opened_before_arrival`, rather than filling into a collision.

### 2. Historical replay graded the wrong target — P0, and the most damaging research bug

Replay computed truth as `_actual_direction(entry, endpoint_close, band)` — **endpoint sign** —
while the main ensemble trains on `FIRST_TOUCH_TRIPLE_BARRIER_V1`. It never dispatched through
`target_contract.grade()` and never looked at the intrabar highs and lows that decide which
barrier was touched first. **`klines` was already in scope**; replay simply never used it.

This is the same substitution `backtester.py` was repaired for, in a different subsystem — and
it matters more, because the server turns replay output into recommendations ("raise threshold",
"keep current gate", "mostly AVOID"). A threshold tuned against the wrong target is worse than
no recommendation.

Now routed through the canonical grader. Measured on a synthetic path:

```text
real intrabar window   -> GRADED_FIRST_TOUCH
empty path             -> None / GRADE_UNAVAILABLE:no_intrabar_path
```

A refusal is counted as `ungraded` and **skipped**, not folded in as NEUTRAL — counting a
refusal as a flat outcome would feed the threshold recommender rows that were never measured.

### 3. The 15m cascade was rewarded for the 5m model abstaining — P0/P1

The cascade read `lower_acc_stats["accuracy"]`. The verifier deliberately publishes two
different numbers: `accuracy` is **dual-semantic** (it credits correct abstentions through
`avoid_success`), while `lean_accuracy` counts only rows where a directional lean was actually
committed. So an abstention-heavy 5m lane could earn enough apparent skill to bias 15m raw
probabilities.

Now reads `lean_accuracy` with `lean_total`. The sample floor counts **directional calls**, not
every row — pairing a lean rate with a total row count would clear `CASCADE_MIN_PREDICTIONS` on
a lane that made almost no calls. A missing `lean_accuracy` scores 0 and 0 samples, because
absent must not read as eligible.

---

## Verified, confirmed, NOT fixed

Each is real and read in source. None is a small edit, and half-doing them is how this backlog
was built.

| # | defect | evidence | why not now |
|---|---|---|---|
| 4.5 | replay routes every row through RANGE | `historical_replay.py:59` hardcodes `{"regime": "RANGE", "confidence": 0.5}` | needs the bundle's frozen HMM replayed causally — the same work as 2.9 |
| 4.6 | replay emits `finalAction` without the production policy | replay calls `generate_ensemble_prediction` + `compute_no_trade_reasons`; the live meta gate, quality filter and expectancy neutralizer are not reproduced | needs one shared `decide(snapshot, forecast, policy_release)` used by live and replay |
| 4.8 | disabling the paper engine freezes inventory | `service.py:168` returns before marking, funding and exits when `hard_enabled` is false; `close_position()` refuses too | "disabled" must mean CLOSE_ONLY, which is a state-machine change, not a flag flip |
| 4.9 | forecast probabilities and `agreement` come from different populations | `_predict_model_probs` blends regime experts on ambiguous HMM confidence; `_model_directions` hard-routes to one regime | fixing it changes what every seat vote means; needs its own evidence run |
| 4.10 | live regime weights pool 5m and 15m | `regime_model_stats[regime][model]` — **horizon absent**; one map is handed to both horizons, and 5m resolves ~3x as often | a data-structure change (`stats[horizon][regime][model]`) plus per-horizon sample floors |
| 4.1 | delayed/out-of-order books can become the executable quote | exchange-event age is computed and recorded but not used as a rejection condition; `last_book` is overwritten without monotonic `event_ts_ms` | part of the canonical-event-schema work (2.4) |
| 4.2 / 4.3 | Pyth freshness uses receipt time, and a stale feed can still settle an open round | `pyth_price_ts = time.time()`; `publish_time` is discarded. `feed_fresh` blocks opening a round but `_resolve()` never receives it | same root: no source-observation identity |
| 4.11–4.13 | smoothing, hysteresis and maintenance cadences are measured in **calls**, not time | `MAIN_LOOP_SEC × tick_count`; alpha per invocation; opposing-tick counts | needs event-time plumbing throughout |
| 4.14–4.19 | funding replay, websocket-thread DB work, Coinbase/Bybit/KuCoin staleness, cost-model split | each confirmed | feed-provenance and execution-isolation workstreams |
| 4.20 | `decision_id` hashes `time.time()`, so identical market state gets two identities | contradicts the snapshot's own stated intent | part of the `DecisionEnvelope` work |
| 4.21 | learned regime weights are bypassed when a stacker exists | `_predict_from_regime` uses the stacker directly; the dynamic map drives only the naive fallback | either declare them diagnostics-only or redesign; a decision, not a bug fix |

---

## What this scan changes about the picture

The first three scans were about **contracts** — which question a number answers. This one is
about **synchronization**: same moment, same expert population, same horizon, same target, same
freshness definition. Those are different failure modes and the second class is harder to see,
because every individual number is correct.

The two most dangerous items were the **stale/out-of-order executable book** and the
**pending-entry race**. The race is fixed. The book is not, and it belongs with 2.4 — there is
still no canonical event schema carrying source, event, receive and close timestamps, and at
least six confirmed defects across all four scans trace to its absence.

**That is now the single highest-leverage item in the entire backlog**, ahead of the
executed-training snapshot: 2.3, 2.4, 2.5, 4.1, 4.2, 4.3 and P0-4 are one defect wearing seven
numbers.

---

# Scan 5 — first verdict, and a REFUSAL — `2026-08-07`

A fifth scan arrived with 31 further claims. Its own top-priority item is verified below. The
remaining 30 are **not yet investigated** and must not be quoted as findings.

## 5.1 — "TRADE is unreachable" — CONFIRMED-ADJUSTED. **Do not implement the proposed fix.**

The mechanism is exactly as described:

```python
# decision_gate.py:55
if cfl.get("grade") in ("A", "B", "C") and not cfl.get("grade_validated"):
    reasons.append("grade_unproven")
```

`_confluence()` sets `grade_validated` **zero times**, and the selftest injects it by hand. So
every directional setup carrying an A/B/C grade collects `grade_unproven`, which makes
`no_trade_reasons` non-empty and yields `WEAK_LEAN` rather than `TRADE`.

**But that is deliberate, and the gate says so in two places:**

```text
line 29  "grade_unproven": "setup grade not a trust signal yet (currently inverted, §5br)"
line 51  # 4. Grade not yet a trust signal (inverted, §5br) - only flag if a grade is shown
```

The setup grade was **measured to be inverted** — A grades underperform. Blocking on a shown
grade is the evidence-based response, and `grade_validated` is an intentional escape hatch that
nothing sets *because no validation artifact exists yet*.

The scan's proposed remedy — *"`grade_unproven` must be diagnostic-only; it cannot automatically
block the final verdict"* — **removes a deliberate safety block, and arrives framed as a P0 bug
fix.** It is the most dangerous single change available in this repository right now.

**Correct remedy if `TRADE` is wanted:** produce the missing validation artifact — a versioned,
preregistered measurement showing the grade is no longer inverted — and have `_confluence()` set
`grade_validated` from it. That is: earn the escape hatch, do not delete the lock.

### Why this is recorded rather than just skipped

Four scans in, this is the first claim whose *fix* would have caused real harm. The verdict is
written down so a later session reading "P0: TRADE is unreachable" does not implement the
obvious repair. A blocker with no producer for its clearing condition **looks** like a bug and
here is a control.

It also explains a result from the previous commit: the legacy simulator opening nothing is
**over-determined**. It fails the target-contract check *and* would never see `finalAction ==
"TRADE"` anyway.

## 5.2–5.31 — NOT INVESTIGATED

Thirty claims remain unread against source, including two the scan ranks alongside 5.1: the live
first-touch interval being shorter than the declared horizon, and post-inference re-anchoring of
`predicted_price` / `verify_at`. Both are plausible and both touch the same event-time root as
2.4. Neither has been verified, so neither is a finding yet.
