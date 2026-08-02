# Research Ledger — every idea: tested, retracted, untested, or blocked

`2026-08-01`. The canonical answer to "what do we actually know?" Machine-readable status lives in
`research/research_status.py`; this is the reader's version.

**Current evidence position:**

```
Promotable economic strategies : 0
Valid measured candidates      : 0
Real-money authority           : 0
Correct mode                   : shadow / paper
```

---

## 1. RETRACTED — five studies, one root cause

Every economic study that joined a market **state** to an executable **quote** selected the two
independently:

```
state:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) = 1   -- latest in window
quote:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts)           = 1   -- earliest quote
join:   ON round_id AND side
```

Nothing required `state.ts <= quote.ts`, and the two orderings pull in opposite directions.

| | |
|---|---:|
| joined rows | 3,709 |
| **state observed AFTER the decision** | **3,467 (93.5%)** |
| median look-ahead | **+8.1 s** |
| maximum | +17.8 s |

Eight seconds in a window with 20–32 seconds left is a quarter of the remaining time.

| study | retracted claim |
|---|---|
| `phold_auc_and_expectancy.py` | 0.97–0.99 bucket, +0.0371/$1, LCB +0.0069 |
| `phold_calibrated_fair_value.py` | **the candidate edge**: +0.0430/$1, LCB +0.0164, 2/3 splits |
| `meta_label_head_test.py` | META vs CALIBRATED expectancy across 3 splits |
| `settlement_fragility_test.py` | fragility AUC +0.0699/+0.0820/+0.0595 and its expectancy arms |
| `policy_threshold_size_test.py` | "0 of 5 beat the pre-declared control" |

They **refuse to run** without `--run-retracted-study`, and print why.

### What survives from them

- **AUC 0.7762** for live `p_hold` — that section joins state to *outcome* with no quote, so it
  has no decision timestamp to violate.
- **The meta-label identity**: `net > 0` ⟺ `held == 1` on 100% of rows, because `ask + fee < 1`
  always. Arithmetic, not a measurement.
- **The qualitative lesson** from the policy study: selection on 21 days overfits badly
  (train-selected +0.3363 → test +0.0062). The *counts* are retracted; the shrinkage is real.

### Additional defects, independent of the join

- **Fragility attribution is unsupported.** The FRAGILITY arm carried 13 features *including
  `ask`* — the market's own probability — and was compared against calibrated `p_hold` **alone**.
  No BASE arm. The AUC delta was never attributable to fragility.
- **Its calibrator was fitted in-sample** (`model.fit(train)` → `predict_proba(train)` →
  `iso.fit(...)`), so "calibrating makes it worse" is not established.
- **FLAT and KELLY were never comparable**: FLAT weights 1.00, KELLY caps at 0.05 — 20× the risk
  per trade. My claim that reporting "per $1 of bankroll" made them comparable was wrong.
- **Nested splits**: test windows overlap heavily. Not independent replications.

## 2. VALID — what is actually established

| finding | status |
|---|---|
| `causal_decision_join.py`: candidate is **0 of 3** causally, negative LCB in all windows | **valid diagnostic negative** |
| 39 research scripts, **0 positive out-of-sample** | valid |
| direction dead at settlement *and* along the path | valid |
| magnitude (`rv_term_inversion`) real, survives Bonferroni | valid |
| breakout bracket loses structurally — control loses equally | valid |
| complete-set arbitrage ~$200 / 2.17 days at ten-share size | valid |
| option surface coherent: 0 of 2,079 no-arb violations | valid |
| Polymarket books coherent; cross-market monotonicity holds | valid |
| paper strategies had a 6 bps target vs 12 bps round trip | valid, **fixed** |
| 21 of 31 trainers wrote artifacts with no integrity manifest | valid; writer gate fixed, full serving provenance still awaits retrain |
| pytest never ran in CI (86 tests unexercised) | valid, **fixed** |

`causal_decision_join.py` is classified **CAUSAL_BEST_EFFORT_HISTORICAL**, not gold standard:
`rule_paper_trades` has one generic `ts` and cannot prove it is exactly when the stored ask was
observed.

## 3. UNTESTED — ideas with no evidence either way

Not disproven. Listed so they are not mistaken for closed.

| idea | why untested |
|---|---|
| alpha half-life / survival of an impulse | no forward dataset |
| last-anchor-crossing distribution | genuinely new; needs causal ledger |
| Polymarket probability excursion (MFPE/MAPE) | **unblocked** — trajectories exist; see §4.2 |
| mispricing half-life | same |
| maker fill survival, queue position, markout | needs sequenced L2 |
| liquidity-vacuum / price-response kernel | needs L2 |
| flow-origin classification | needs cross-venue synchronized feeds |
| multi-expiry outcome geometry | needs simultaneous market capture |
| oracle-basis settlement risk | needs settlement-source recording |
| latent market-maker inventory | needs Polymarket L2 |
| leverage pressure field | partially buildable from free OI/funding |
| causal-invariance feature compiler | buildable now, untried |
| strategy exchange / capital bandit | premature — nothing to allocate between |
| self-impact market twin | premature |

**Reversal hazard, barrier ordering and MFE/MAE quantiles are *not* closed.** The earlier tests
killed *specific instances* — a fixed-clock exit, one `+10/−20` structure, oracle-labelled
excursion shifts. A state-conditioned hazard model is a different question.

## 4. BLOCKED — and by what

| item | blocker |
|---|---|
| `PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1` activation | **0 of 25 artifacts are loadable by serving** — §4.1 |
| all forward evidence | recorders down; **not all at once** - see §4.2 |
| queue/maker research | no sequenced L2 |
| ~~Polymarket dynamic exit~~ | **NOT BLOCKED** - the 1.00 rows/round figure was wrong; see §4.2 |
| options ↔ Polymarket lead-lag | archives 27 days apart |
| hosted CI | billing; local `run_ci_locally.py` is the only gate |

### 4.1 The manifest gate was passing vacuously — measured `2026-08-02`

There are **two** different things called a manifest, and only one of them unblocks serving:

| writer | file | contents | unblocks serving? |
|---|---|---|---|
| `verified_io.write_manifest` | `NAME.pkl.integrity.json` | sha256, size, `integrity_only: true` | **no** |
| `artifact_identity.write_artifact_manifest` | `NAME.pkl.manifest.json` | full provenance | yes, if complete |

`check_feature_contract._manifest()` **explicitly skips** any file carrying `integrity_only:
true`. So `test_trainers_write_manifests.py` could report *"0 offenders"* — and did — while every
artifact stayed refused. That is the same defect class as the non-causal join: a check that passes
while the thing it was meant to guarantee is false.

```
artifacts (.pkl)                : 25
  with a PROVENANCE manifest    :  3   (all 3 missing 'feature_semantics_version')
  with ONLY an integrity manifest:  5   passes the manifest gate, still refused
  with neither                  : 17
  SERVICEABLE                   :  0
```

Consequences, none of which were visible before measuring:

- Every model-backed strategy is **`UNAVAILABLE`, not unprofitable**. `p_hold` itself does not
  load, so `p_leader_holds` is absent and the benchmark cannot even reach its own decision rule.
- **A retrain that writes only integrity sidecars changes nothing.** `backend/train_heads.py` is
  the only place that writes provenance correctly, and it does so in the *orchestrator* after each
  per-head trainer returns — so a retrain launched by running trainers individually produces
  artifacts that still cannot be served.
- Serviceability additionally requires `artifact_matches_current_training`: `requested_days`,
  `matrix_requested_days`, `matrix_coverage_ok`, `matrix_monthly_quality_passed`,
  `source_manifest_hash`, `runtime_dependency_hash` and the artifact hash must all match the
  *current* training identity. A complete manifest with stale values is still refused.

`backend/test_artifact_serviceability.py` now measures this on every CI run and ratchets: the
count may rise, never silently fall.

### 4.2 The data was never measured - `2026-08-02`

`backend/audit/build_oracle_data_manifest.py` profiles every table in every store: span, rows,
distinct days, hours with **zero** rows inside the span, inter-arrival quantiles, max gap,
duplicate/out-of-order rates, venue-vs-local clock skew, sequence integrity, recorder sessions.
It found three things nobody had checked.

**1. "The database" is not one object.** Three files named `analytics.duckdb` exist with
different spans. Serving resolves its path from `BTC_DB_PATH`/`BTC_DATA_DIR`; some research
modules hardcode a different copy.

| path | span |
|---|---|
| `data/analytics.duckdb` | `2026-06-12` -> `2026-07-04` (stale, pre-Oracle) |
| `data/btc_duckdbs/analytics.duckdb` | `2026-07-05` -> `2026-07-25` (**the live Oracle archive**) |

`research/phold_auc_and_expectancy.py` hardcodes `btc_duckdbs`; `backend/database.py` defaults
to the stale copy. Every study must now state which path it read.

**2. The recorders did not stop together.**

| recorder | last row |
|---|---|
| cross-venue collector (`multi_venue.venue_events`) | `2026-07-29 19:18:08` |
| **round / Polymarket recorders** (`btc_duckdbs/analytics`) | **`2026-07-25 15:00`** |

The remembered "down since 2026-07-29" is true only of the cross-venue feed. Round-level
forward evidence has been dark since **2026-07-25** - an 8-day hole, not 4.

Continuity *within* the live archive is good: 21 distinct days with **0-2 hours** of zero rows
and a max gap of ~2.1 h.

**3. The quote-trajectory blocker was wrong.** `btc_duckdbs/execution_layer.pm_round_snapshots`
holds **1,713,160 rows over 7,787 markets** - 144 snapshots per 5m round, 447 per 15m round,
roughly one every two seconds - each carrying `up/down_bid`, `ask`, `mid`, `spread`,
`top_ask_size` and cumulative depth `d1/d2/d5`, **100% non-null**, alongside `p_hold_*` and
`anchor_price`.

The "1.00 rows/round, 2 markets" figure came from `rule_paper_trades` - which stores one
*decision* row per round by design - and from the stale copy. It was never a statement about
the recorder. This unblocks, on data already on disk:

- probability excursion (MFPE/MAPE) and mispricing half-life
- `OPPOSITE_TOKEN_EXECUTABLE_EXCURSION_V1` - the blueprint said mark it
  `BLOCKED_BY_QUOTE_TRAJECTORY` if only one quote per round existed. It does not apply.
- hold-vs-exit counterfactual labels from the actual future executable **bid** path

Caveat: `pm_round_snapshots` has one generic `ts`, so it is the same
`CAUSAL_BEST_EFFORT_HISTORICAL` class as `rule_paper_trades`. That is acceptable for building
*labels* (which look forward from a decision point by design) and **not** for features.

### 4.3 The canonical causal checkpoint dataset - `2026-08-02`

`backend/research_data/checkpoint_builder.py` emits one admissible row per round per grid point
from the live archive. **56,467 rows over 7,787 rounds**, 99.9% settlement coverage, median
checkpoint age **1.10 s**.

**It performs no state-to-quote join.** Every feature comes from a single atomically-written
`pm_round_snapshots` row - BTC price, both books, the depth ladder and `p_hold` observed in one
instant by one process. The defect that retracted five studies is structurally impossible here,
not merely guarded against. Two joins remain and both are explicit: the grid takes the last
snapshot **at or before** each checkpoint, and settlement is segregated as a LABEL
(`OUTCOME_COLUMNS`) so it can never be offered as a feature.

Grid points at the round start (300 s for 5m, 900 s for 15m) are **absent**: recording begins
microseconds later, so no snapshot precedes them. Absent is the honest answer; reaching forward
for the next row is the defect.

| evidence class | rows | may promote? |
|---|---:|---|
| `PRE_ORACLE` | 2,413 | no - diagnostic only |
| `LIVE_RESEARCH` (07-06 → 07-20) | 41,458 | no - shaped the research |
| `RETROSPECTIVE_VALIDATION` (07-21 → 08-01) | 12,596 | no - elimination only |
| **`FORWARD_UNTOUCHED` (08-02 →)** | **0** | the only class that can |

**Zero promotable rows exist**, and will stay zero until the round recorders restart.

**A bug this caught in itself.** `pm_round_snapshots.ts` is in SECONDS; `rule_paper_trades.ts`
is in MILLISECONDS. Both are called `ts`. A hardcoded `/1000` sent all 56,467 rows to 1970 and
classified the entire dataset `PRE_ORACLE` - a result plausible enough to ship. The epoch unit
is now inferred, and the selftest asserts a known date lands in the right class, so the mistake
fails loudly. Verified by planting it: the check does fail.

### 4.4 Remaining-move and crossing labels - `2026-08-02`

`backend/research_data/path_label_builder.py` computes 19 labels per checkpoint from the same
`pm_round_snapshots` path the checkpoints came from - so a label can never describe a different
round, anchor or price series. **49,307 of 56,467** checkpoints have a forward path (median 63
samples); the other **7,160 get NULL, never zero**. Zero and unknown are different, and a model
trained on the difference learns the recorder's downtime.

Labels are allowed to see the future - that is what makes them labels. The protection is that
they cannot be mistaken for inputs: every column is prefixed `label_`, and
`causal_validation.feature_columns()` excludes the prefix, so a label invented tomorrow is
excluded **without being registered anywhere**.

**Remaining move and crossing risk, measured:**

| seconds left | median range | p90 range | any further crossing | leader wins settlement |
|---:|---:|---:|---:|---:|
| 15 s | $1.4 | $12.2 | 10.0% | 82.5% |
| 30 s | $4.3 | $20.5 | 12.8% | 83.5% |
| 60 s | $11.2 | $34.4 | 17.8% | 82.5% |
| 120 s | $23.5 | $57.6 | 26.3% | 79.5% |
| 240 s | $46.2 | $101.1 | 48.9% | 68.3% |
| 720 s (15m) | $98.6 | $209.3 | 61.2% | 66.4% |

This is the answer to "is a $20-30 lead safe?" — it depends entirely on the clock. At 15 s a $20
lead sits above the p90 remaining range; at 240 s the p90 is **$101**, so the same lead is
fragile. A fixed dollar threshold cannot express that, which is what the fragility work was
reaching for.

**The path is NOT the settlement source.** `label_current_side_survives` (leader holds to the
end of the *recorded path*) and `label_checkpoint_side_wins` (leader wins the *contract*)
disagree materially:

```
15s checkpoint:   survives path 91.0%   wins settlement 82.5%   gap 8.5 points
```

The recorded path ends before expiry and the oracle is a different feed. Using the path as a
settlement proxy would have inflated leader survival by 8.5 points at exactly the horizon the
retracted `p_hold` work cared about. Both labels ship, plus
`label_path_agrees_with_settlement`, so the divergence is measurable rather than absorbed.

**Burst rates**, P(move >= X USD within W seconds), averaged over both directions:

| window | >=$10 | >=$25 | >=$50 | >=$100 | >=$200 |
|---:|---:|---:|---:|---:|---:|
| 5 s | 4.03% | 0.47% | 0.06% | 0.01% | 0.00% |
| 15 s | 14.48% | 3.11% | 0.43% | 0.04% | 0.01% |
| 30 s | 23.35% | 7.07% | 1.28% | 0.12% | 0.01% |
| 60 s | 32.14% | 12.83% | 3.36% | 0.41% | 0.04% |

The "sudden $300 pump" is real but sits at **0.04% per 60 s** — roughly 1 in 2,500 checkpoints.
A head predicting it is a rare-event problem, so ROC-AUC will be uninformative; precision at
the top 1%/5% and false alarms per day are the metrics that mean anything.

Bursts are stored as the **continuous max excursion per window**, not as 40 pre-baked threshold
booleans. Every boolean is a pure function of the float, so storing them would cache a
computation that goes stale the moment the grid is edited while the parquet keeps serving the
old answer under the same name. `burst_indicator(frame, direction, usd, window_s)` derives any
threshold, and the published rates above come through that same helper — so the number in this
document and the training target cannot diverge.

**Flip persistence** over the 15,329 checkpoints whose path crosses the anchor:

```
first crossing is the FINAL one : 43.0%
reverts within  5s              : 10.0%
reverts within 15s              : 25.5%
reverts within 30s              : 34.9%
reverts within 60s              : 43.9%
```

**57% of flips revert.** That is the measured version of the temporary-burst-vs-durable-flip
distinction: a crossing is close to a coin toss, tilted slightly toward reversion. Reversion is
timed from the **first crossing**, not from the checkpoint — otherwise the same flip would look
more or less durable purely by how early in the round it happened.

**A bug this found in the labels shipped one commit earlier.** DuckDB's `greatest()` ignores
NULL, so `greatest(NULL, 0)` is `0`. All **7,160** empty-path rows had recorded
`label_remaining_max_up_usd = 0.0` — "no observation" written as "no move", the exact failure
the module's own docstring claims to prevent. Every excursion column is now explicitly
NULL-guarded, and the selftest asserts it.

Sigma-normalised labels (`0.5/1.0/1.5/2.0`) use a **declared proxy**: `vol_60s_pct` from the
checkpoint snapshot, scaled by sqrt(time). Causal, but not an implied vol and not claimed as one.

### 4.5 Oracle versus current repository - `2026-08-02`

`research/model_vintage_comparison_v1.py`. Both vintages scored on the **identical** 47,864
eligible settled checkpoints, same entry rule, canonical fees.

| arm | Brier | log loss | ECE | AUC | trades | net/$1 | day LCB |
|---|---:|---:|---:|---:|---:|---:|---:|
| **MARKET** (the ask) | **0.1453** | **0.4410** | **0.0036** | **0.7918** | 0 | - | - |
| ORACLE (live, out of sample) | 0.1604 | 0.4878 | 0.0094 | 0.7271 | 12,762 | −0.0159 | −0.0265 |
| CHALLENGER (in sample) | 0.1617 | 0.4931 | 0.0232 | 0.7227 | 15,039 | −0.0141 | −0.0237 |
| RANDOM (matched count) | - | - | - | 0.5000 | 12,762 | −0.0125 | −0.0170 |

**Three findings, in order of importance.**

**1. The market beats both models on every metric — including AUC.** Brier 0.1453 vs 0.1604,
log loss 0.4410 vs 0.4878, ECE 0.0036 vs 0.0094, AUC 0.7918 vs 0.7271. The Polymarket ask is a
better probability than either model vintage produced. MARKET takes 0 trades by construction
(`p == ask` can never clear `ask + fee + margin`); it is the calibration bar, not a trading arm.

**2. Both models lose money, and both lose to RANDOM.** Oracle −0.0159/$1, challenger −0.0141,
matched-count random −0.0125. Model *selection* is worse than picking the same number of rounds
with no information at all.

**3. The challenger loses despite being in sample.** Its training set spans `2023-01-16 →
2026-07-30` with `refit_on_all=True`, including 335,060 July 2026 rows — the exact window
scored. The reading rule was declared before the run: **a challenger loss is informative, a
challenger win proves nothing.** It lost on Brier, log loss, ECE and AUC, on rounds it had
memorised. It trades 18% more and its extra trades are the bad ones:

```
oracle enters, challenger waits :    381   win 0.7717 +/- 0.0215
challenger enters, oracle waits :  2,658   win 0.7216 +/- 0.0087
break-even win rate               0.7328
```

The Oracle's unique picks sit **above** break-even; the challenger's sit **below**. Directionally
clean, though ±1.8 and ±1.3 standard errors respectively — suggestive, not decisive alone.

**Caveat that cuts against the conclusion:** the Oracle's live `p_hold` could use the 11-feature
`keeper` variant when volatility keepers were present; those inputs were never recorded, so the
replay uses the 5-feature base model. Part of the challenger's gap may be missing features
rather than a worse model.

**This is elimination-grade, never promotion-grade** — zero `FORWARD_UNTOUCHED` rows exist. What
it does support: **do not promote the newer artifact**, and treat the market price as the
incumbent any Polymarket model must beat.

### 4.6 Polymarket action-value engine - `2026-08-02`

`backend/polymarket_policy/` prices every action from the **recorded ladder**: you buy at the
ask, sell at the bid, cross the spread once each way, and pay canonical fees. No probability, no
model, no forecast — the vintage comparison had just shown the market's own ask beating both
model vintages, so an engine resting on those models would rest on something already measured as
worse than the price it is trying to beat.

Applied to 50,272 eligible settled checkpoints:

| action | n | mean | median | p10 | p90 | win% |
|---|---:|---:|---:|---:|---:|---:|
| EXIT_AT_HORIZON 15s | 48,308 | −0.0313 | −0.0121 | −0.1531 | 0.0681 | 33.2% |
| EXIT_AT_HORIZON 30s | 48,378 | −0.0309 | −0.0054 | −0.2034 | 0.1102 | 41.7% |
| EXIT_AT_HORIZON 60s | 48,427 | −0.0322 | −0.0012 | −0.2919 | 0.1653 | 48.9% |
| HOLD_TO_SETTLEMENT | 50,272 | **−0.0105** | 0.0561 | −0.6561 | 0.3832 | 77.4% |
| LOCK_COMPLETE_SET | 50,272 | −0.0302 | −0.0328 | −0.0447 | −0.0121 | **0.1%** |
| *ORACLE_BEST_EXIT* (hindsight) | 48,460 | *+0.1313* | *0.0902* | −0.0121 | 0.3539 | 80.6% |
| *ORACLE_PICK_AMONG_TRADEABLE* | 50,272 | *+0.1368* | *0.0748* | 0.0000 | 0.3832 | 66.5% |
| WAIT | 50,272 | 0.0000 | — | — | — | — |

```
perfect exit timing (untradeable) : +0.1313
perfect choice among tradeable    : +0.1368
best FIXED rule, no foresight     : -0.0105   (HOLD_TO_SETTLEMENT)
standing aside (WAIT)             : +0.0000
headroom an action head could win : +0.1473
```

**Three things follow.**

1. **Every fixed rule loses; WAIT beats all of them.** On this sample the only non-negative
   policy is to not trade. Hold-to-settlement is the least-bad at −0.0105/share.
2. **The ceiling is strongly positive (+0.1313).** Unlike direction — which was dead at
   settlement *and* along the path — exit timing has real headroom. A perfectly-timed exit is
   positive on 80.6% of checkpoints. This is the first lane measured here with a ceiling worth
   modelling.
3. **Locks are effectively unavailable.** Mean −0.0302, positive on **0.1%** of snapshots, and
   the best action on only 3 of 50,272 checkpoints. Consistent with the raw scan: 515 of
   1,713,160 snapshots have `up_ask + down_ask + fees < 1`.

Both oracle arms are labelled `requires_hindsight` and `select()` **excludes them before**
comparing, so a value nobody could realise cannot be returned as a recommendation. They are
bounds on what a head could win, never strategies.

**Constraint the data imposes:** `pm_round_snapshots` records ask-side depth
(`top_ask_size`, `d1/d2/d5`) but **no bid-side size**. Entry capacity is measurable; exit
capacity is not. `execution_cost.exit_fill()` returns `capacity_known = False` rather than
assuming one share always fills — the assumption that turns an unexitable position into a
backtest profit.

### 4.7 Binance action-value engine - `2026-08-02`

`backend/binance_alpha/action_value.py` plus a builder over the 1-minute archive (518,400 bars,
`2025-08-05 → 2026-07-30`). The perpetual has no settlement cliff, so **the horizon is the
action**. Round trip is `2 x (fee + slippage) = 12.0 bps`, read from `binance_paper.config` —
this module carries no cost constant of its own.

Windows are **disjoint** (stride = horizon). Overlapping windows once let this repository report
a +1230 bps result across "11 expiries" carrying roughly one independent observation.

| horizon | windows | LONG_HOLD | SHORT_HOLD | *ceiling (best exit)* | *perfect pick* |
|---|---:|---:|---:|---:|---:|
| 15m | 34,559 | −12.14 | −11.86 | *+2.75* (median **−2.63**) | *+7.10* |
| 30m | 17,279 | −12.28 | −11.72 | *+9.26* | *+12.14* |
| 60m | 8,639 | −12.56 | −11.44 | *+18.46* | *+20.28* |
| 120m | 4,319 | −13.12 | −10.88 | *+31.37* (median +15.97) | *+32.68* |

All figures bps, net of costs. Starred arms **require hindsight** and are bounds, never
strategies — `select()` excludes them before comparing.

**1. Every fixed rule loses almost exactly the round trip, at every horizon.** LONG_HOLD −12.14
and SHORT_HOLD −11.86 at 15m against a 12.0 bps cost: the gross mean return is ≈ 0, as a
near-martingale implies, and you simply pay the spread. Direction is worth nothing — the same
conclusion the earlier work reached, now at four horizons on disjoint windows.

**2. The 15-minute lane is dead even with perfect foresight.** The ceiling's *median* is
**−2.63 bps** — a perfectly-timed exit loses money in more than half of all 15m windows, and the
+2.75 mean is carried entirely by the right tail. A realistic head captures some fraction of a
ceiling; a fraction of this one is nothing.

**3. Headroom scales with horizon and only becomes interesting past an hour.** The ceiling goes
+2.75 → +9.26 → +18.46 → +31.37 bps as the window grows, and at 120m it is 2.6x the round trip
with a positive median. This is the measured version of the intuition that the 15m lane should
be pushed out to 1–2h: at 15m there is nothing to capture, at 2h there is.

Short ceilings edge out long ones at every horizon (+35.05 vs +31.37 at 120m), consistent with
a fatter downside tail. Small, and not leaned on.

**Declared omission:** funding is not modelled. Horizons reach 2h and funding settles every 8h,
so roughly a quarter of 2h windows cross a stamp. In a positive-funding regime that flatters
LONG arms — stated because it biases toward the conclusion that would otherwise pass unnoticed.

### 4.8 Hold-vs-exit head - `2026-08-02`  **FAILS its declared gate**

`research/hold_vs_exit_head_v1.py`. The action-value engines said the only headroom was exit
timing, so this asks whether anything captures it. Target: at a checkpoint, would exiting at
30s have beaten holding to settlement? Train on the earlier 70% of `LIVE_RESEARCH` days,
calibrate the isotonic map and pick the threshold on the later 30%, evaluate on
`RETROSPECTIVE_VALIDATION` — strictly later than both.

```
AUC 0.8731   ECE 0.0126        <- the head ranks the decision very well
```

| policy | exits | mean/$1 | day LCB |
|---|---:|---:|---:|
| ALWAYS_HOLD | 0 | **−0.0103** | −0.0186 |
| ALWAYS_EXIT | 10,888 | −0.0339 | −0.0362 |
| HEAD@0.65 | 894 | −0.0107 | −0.0196 |
| RANDOM_MATCHED | 894 | −0.0132 | −0.0208 |
| *PERFECT (hindsight)* | 2,628 | *+0.1005* | *+0.0945* |

**A head with 0.87 AUC and 0.013 ECE does not beat doing nothing.** It loses to always-hold by
0.0004/share while a perfect selector earns +0.1005. Good ranking, no conversion.

**The diagnosis, measured not guessed.** The payoff is *favourable* to exiting when right:

```
exit beats hold : 23.4% of rows, mean gain +0.4758
hold beats exit : 76.6% of rows, mean loss -0.1713   (gain is 2.8x the loss)
blind exit EV   : 0.234 x 0.4758 - 0.766 x 0.1713 = -0.0199
```

So exiting indiscriminately loses because wrong exits are 3.3x more common, and the head must
select. It ranks well enough to do so — and the *threshold* selected on calibration days
(0.65) fires on only 894 of 10,888 rows, 8.2% against a 23.4% base rate, and does not transfer.

This reproduces, under a causal construction, the one qualitative lesson that survived the
retraction of `settlement_fragility_test`: **a better ordering does not transfer through a
fixed threshold on a level.** That study's numbers are retracted; this is the same phenomenon
measured cleanly, and it is now the second time it has decided an outcome here.

**What is NOT being done:** no further threshold search. The grid, the selection rule and the
evaluation set were declared before the run, the verdict is FAIL, and sweeping until something
passes is exactly how the retracted studies were produced. The legitimate next step is a
different DECISION RULE — an expected-value rule using predicted *magnitude* rather than a
probability threshold on the sign — declared in advance and evaluated on data not yet spent.

Elimination-grade regardless: zero `FORWARD_UNTOUCHED` rows exist.

## 5. Governance added because of the retraction

| gate | what it prevents |
|---|---|
| `research/research_status.py` | retracted numbers being rediscovered and quoted as evidence |
| `backend/test_causal_join_guard.py` | a **new** quote↔state join without a causal timestamp rule |
| `backend/test_trainers_write_manifests.py` | a trainer that dumps an artifact and writes no sidecar |
| `backend/test_artifact_serviceability.py` | the gate above passing while **0 of 25** artifacts load |
| `backend/test_naming_honesty.py` | `test_*.py` files that cannot fail |
| `run_all_sequence.py` coverage check | studies silently excluded from the runner |
| pytest step | 86 tests that CI never ran |
| `backend/research_data/causal_validation.py` | a dataset row using its own future |
| `backend/research_data/path_label_builder.py` | a label being offered as a model feature |
| `backend/polymarket_policy/action_value.py` | a hindsight-only action being returned as a recommendation |
| `backend/audit/freeze_oracle_release.py --verify` | a frozen champion artifact changing underneath the benchmark |

Each is negative-tested: it has been shown to *catch* a planted offender, not merely to pass.

**The uncomfortable point:** every retracted study passed CI, passed its own preregistered gates,
carried matched controls and day-block lower bounds — and was wrong anyway, because no check asked
whether the inputs existed when the decision was made.

## 6. Plan

**Phase 0 — restore the substrate** *(nothing below it is worth doing first)*
1. Restart recorders; manifest the gap since 2026-07-29
2. Full artifact retrain — trainers now write manifests
3. Verify all manifests; refit calibrators from verified sources
4. Confirm `PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1` starts logging real decisions instead of `CAL_UNAVAILABLE`

**Phase 1 — atomic causal decision ledger: implemented for the fair-value forward benchmark.**
One immutable row carries exact local quote receive time, venue timestamp, persisted state
snapshot id, context payload, feature/artifact/calibrator/policy hashes, action, and distinct
`WAIT` / `UNAVAILABLE` / `NO_QUOTE` / `BLOCKED` reasons. ENTER and WAIT are refused if their
context is missing or its hash does not match. Automatic official-outcome append and coverage of
every other strategy remain open before this becomes the platform-wide promotion authority.

**Phase 2 — freeze one benchmark and collect.** Calibrated `p_hold`, `> ask + fee + 0.02`, fixed
small notional, hold to settlement, every opportunity logged including `WAIT`. No retuning during
the window.

**Phase 3 — only then** evaluate challengers, with separate train / calibration / policy-selection
/ untouched-test partitions.

**Data gate before Phase 3:** ≥8 uninterrupted forward weeks, ≥1,000 independently resolved
rounds, high causal-join coverage, multiple regimes, no unresolved recorder gaps.

## 7. Capital

```
$100,000 live : HARD NO
$5,000 live   : HARD NO
$500 canary   : not yet
$0 shadow     : correct mode
```

The next milestone is not profit. It is **one forward economic result that never required a
retrospective state-to-quote join.**

## 8. Reproduce

```bash
python research/research_status.py
python research/causal_decision_join.py
python backend/test_causal_join_guard.py
python backend/run_ci_locally.py
```

## 9. Phase 5B standalone campaign - `2026-08-02`

Experiments 43-88 were implemented as 46 isolated, frozen-protocol research scripts in
`research/phase5b_standalone/`. The audited 100,000-row-per-source campaign completed every
process and found **zero promotion candidates**:

| result | count |
|---|---:|
| `FAIL_UNSTABLE` | 21 |
| `BLOCKED_DATA` | 10 |
| `FAIL_NO_EDGE` | 8 |
| `INSUFFICIENT_SAMPLE` | 4 |
| `FAIL_AFTER_COSTS` | 3 |

The final audit caught a numeric-side encoding defect: the recorder's `0` means DOWN, but an
early normalizer treated falsey zero as missing. The corrected v4 campaign shows model P(Hold)
Brier 0.1618 versus the market's better 0.1410, and the sequential wait policy loses 0.9795
versus act-now. Earlier Phase 5B outputs are superseded.

Canonical detail and all 46 conclusions:
`docs/active/PHASE5B_STANDALONE_RESEARCH_RESULTS_2026-08-02.md`.
