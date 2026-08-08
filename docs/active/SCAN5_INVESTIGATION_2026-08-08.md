# Scan 5 investigation — the remaining claims — `2026-08-08`

Closes the "24 uninvestigated" gap. Each claim read against source. Nothing fixed here — this is
the investigation, so that fixes aim at the real mechanism.

Verdict vocabulary as before: `CONFIRMED`, `CONFIRMED-ADJUSTED` (real, stated mechanism needed
correction), `NOT COMPLETED` (not read yet — do not quote).

```text
CONFIRMED             19
CONFIRMED-ADJUSTED     2   (5.1 already refused; 5.21 below)
verified elsewhere     2   (5.3, 5.11)
NOT COMPLETED          5
```

---

## The largest cluster: the first-touch barrier price is being used as an economic price

**5.4, 5.5, 5.6, 5.7 — all CONFIRMED, and the codebase already documents the trap.**

`target_contract.py:651-656` says it outright:

> *the barrier price is the observation that DEFINED the outcome … under first touch, |move| is
> always the barrier distance, so magnitude error on these rows measures the barrier, not a
> magnitude forecast … `endpoint_price`, which is carried for exactly that purpose.*

`GradeResult` carries **both** `resolution_price` (the barrier) and `endpoint_price`. The
verifier takes the barrier:

```text
prediction_verifier.py:324   resolution_price = float(result.resolution_price)
prediction_verifier.py:350   "actual_price": resolution_price
```

and `actual_price` then flows into the forward-EV ledger (`server.py:5060-5062`), into
`actual_move_usd`, target error and move error, and from there into the live gate's
`expectancy_usd` — displayed as "historical EV" — and into the magnitude-head retrain trigger.

**So four separate consumers compute trading economics from a classification barrier.** The
remedy already exists in the object: use `endpoint_price` for magnitude and P&L, keep
`resolution_price` for the classification outcome.

**5.4** is the same family: `PerModelVerifier.__init__(neutral_band=0.0008)` is a constant, its
`_direction` grades on endpoint change, and it never receives the parent prediction's adaptive
`neutralBand`. Every seat vote is graded at a fixed 8bps floor, so seat complementarity research
is contaminated whenever the real band is wider.

---

## Promotion and A/B: five fail-open gates

| # | claim | evidence |
|---|---|---|
| 5.16 | `valid_mask=None` fails open | `evaluate_candidate` records `ambiguous_rows_excluded` and adds **no failure reason** when the mask is absent |
| 5.17 | missing regime history fails open | `model_promotion.py:186` records `"regime_routing": "RANGE_DEFAULT"` and adds no failure reason — it reports the degradation and passes anyway |
| 5.19 | incumbent non-regression is optional | `:292` `if fair_comparison:` — both regression checks are simply **skipped** when a fair paired comparison cannot be formed. A challenger can clear absolute gates without ever being compared to the incumbent |
| 5.14 | A/B evidence clock resets on restart | `started_at = float(started_at or time.time())`, and `simulated_live_days` is measured from it. `restore_from_db` restores counts, not the start timestamp |
| 5.15 | pending A/B attribution is memory-only | `self.pending: dict = {}` — a prediction made before a restart resolves in DuckDB while the in-memory variant counters never see the outcome |

**5.13 — the challenger is not independent. CONFIRMED.**

`ab_testing.py:116` and `:135` pass the **same** `cascade_data` object to primary and challenger,
and the server builds it from the primary's *finalised* 5m result. The challenger's 15m forecast
is therefore partly conditioned on the incumbent's post-policy 5m output.

**5.30 — the paper promotion gate never checks the control. CONFIRMED, and self-contradicting.**

`strategy_registry.py:5` states the requirement in its own module docstring:

> *A strategy that does not beat `random_control` over the [same period] has established
> nothing.*

`_promotion_gate` in `metrics.py` contains **zero** references to `random_control` or
`CONTROL_STRATEGY_ID`. The invariant is written down and never enforced — the defect shape this
repository has now hit more than twenty times.

---

## Horizon and namespace confusion

**5.10 — per-regime confidence calibration pools 5m and 15m. CONFIRMED.**
`get_regime_calibration` loops `for h in ALL_HORIZONS` and produces **one factor per regime**, so
5-minute outcomes recalibrate 15-minute confidence.

**5.20 — auto-learning writes global scalars inside a per-horizon loop. CONFIRMED.**

```python
for h, data in feedback.items():
    ...
    self.smoothing_alpha = min(0.20, self.smoothing_alpha + 0.005)
```

One global `smoothing_alpha` mutated inside a loop over horizons: 5m evidence changes 15m
behaviour, and when the horizons disagree the final value depends on **iteration order**.

**5.23 — regime skill is looked up in the wrong namespace. CONFIRMED.**

```text
regime.py:49-53          TRENDING_UP, TRENDING_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY
server.py:4403           data_state["regime_info"] = regime      <- RAW namespace
model.py:2118            self.model_accuracies.get(regime, {})   <- RAW lookup
model.py:2179-2184       _get_regime_from_state maps TREND* -> TREND, etc.
```

`model_accuracies` is keyed by the **coarse** names the training buckets use, so a live
`TRENDING_UP` lookup returns `{}`. Per-regime historical skill is silently empty for every
trending and volatility state.

**5.22 — the live gate declares evidence "usable" on the wrong denominator. CONFIRMED.**
`total` counts every verified prediction including final abstentions; once `total >= 100` the
gate begins acting on `expectancy_usd` regardless of how few **directional** decisions exist.

---

## Fail-open in the gate that promises not to fail

**5.26 — CONFIRMED.** `decision_gate.py:64` is a single `except Exception: pass` wrapping the
reason assembly. Execution then continues to verdict construction with a **possibly incomplete**
reason list — so a malformed setup object produces a verdict computed from fewer blockers than
actually applied, in a function whose docstring promises it "never raises".

---

## Paper-engine accounting

**5.27 — `maximum_trades_per_hour` does not count entries. CONFIRMED.**

```sql
SELECT COUNT(*) FROM binance_paper_trades WHERE strategy_id = ? AND exit_time_ms >= ?
```

It counts **closed** trades by **exit** time. A strategy can open several positions within an
hour before any close, and a long-held trade is attributed to the hour it exits.

**5.28 — daily/weekly loss limits use the same exit-time attribution. CONFIRMED**
(`persistence.py:1197`, `:1209`). A position spanning midnight puts its entire P&L on its exit
day.

---

## One correction

**5.21 — CONFIRMED-ADJUSTED. The cap is deliberate; the namespace mixing is the defect.**

The scan calls `threshold = min(threshold, 0.50)` wrong because a learned 0.61 becomes 0.50. The
code explains why the cap exists:

> *A 3-class head with class priors tops out ~0.50-0.55 … the DB showed bars of 0.61-0.63, which
> are mathematically unpassable → guaranteed 100% NEUTRAL until the rolling window refills.*

Capping prevents a permanently closed gate. **But the scan's second sentence is the real
finding:** the cap is justified by the *raw* three-class structural range and then applied to
`eff_conf`, which is the **calibrated** value when a calibrator is active. A bound derived from
one quantity is enforced against another. Raw-score and calibrated-P(correct) thresholds need
separate namespaces — that part stands, and it is not what the proposed fix would have changed.

That makes **four** claims across all scans whose stated remedy would have been wrong: 5.1, 5.21,
2.20's `groups` half, and 4.16's.

---

## NOT COMPLETED — 5 claims

| # | claim | closed |
|---|---|---|
| 5.2 | the live first-touch interval is shorter than the declared horizon | **CONFIRMED-ADJUSTED, FIXED.** Not shorter - SHIFTED forward by up to 60s. Always five bars |
| 5.12 | promotion inherits the incumbent's adaptive feedback state | CONFIRMED, FIXED (`_reset_adaptive_state_for_release`) |
| 5.18 | regime-aware promotion does not reproduce production's posterior blending | CONFIRMED, OPEN - same replay work as 4.5 / 2.9 |
| 5.29 | funding uses observation-time mark price for an event-time cashflow | CONFIRMED, FIXED (basis + lag recorded; the charge still applies) |
| 5.31 | cross-venue "accuracy" measures a different question and moment | CONFIRMED, FIXED (both contracts and the horizon overlap on every round) |

All five read against source and closed out on 2026-08-08:
`SCAN5_CLOSEOUT_AND_OPEN_DEFECTS_2026-08-08.md`.

Two were verified under other numbers: **5.3** is scan-2 2.7 (post-inference global re-reads,
SHAPE-CONFIRMED) and **5.11** is scan-1 1.6 (restart reconstructs the label from `actual_move`,
CONFIRMED).

---

## What to fix first, on this evidence

1. **The barrier-price cluster (5.5/5.6/5.7).** Four consumers computing economics from a
   classification barrier, and `GradeResult.endpoint_price` already exists. Highest value per
   line on this list.
2. **5.30** — the control-relative check the registry already demands in prose.
3. **5.16/5.17/5.19** — three fail-open promotion gates, each a few lines.
4. **5.23** — one namespace mapping; the lookup is silently empty today.
5. **5.26** — a decision-gate exception must force NO_TRADE, not a partial verdict.

Everything above is bounded. 5.13, 5.14, 5.15 and 5.10 are the A/B-isolation and
horizon-scoping work already tracked as architecture.
