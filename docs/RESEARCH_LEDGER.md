# Research Ledger — every idea: tested, retracted, untested, or blocked

`2026-08-02`, last extended `2026-08-07` (§14). The canonical answer to "what do we actually
know?" Machine-readable status lives in `research/research_status.py`; this is the reader's
version.

**Current evidence position:**

```
Promotable economic strategies : 0
Valid measured candidates      : 0
Real-money authority           : 0
Correct mode                   : shadow / paper
Lanes with untested headroom   : 0        <- every lane now has a measured answer
New information sources tried  : 1        <- Polymarket L2 archive (§14). Cannot supervise
                                             BTC rounds: 0 outcome labels, 0 trades on them.
Binding constraint             : forward data. FORWARD_UNTOUCHED = 0 rows.
```

**The one-paragraph summary.** Ceilings are real and large; nothing captures any of them. A
perfectly-timed Polymarket exit earns +0.1005/share and a perfect Binance 120m trade earns
+30 bps, but across every lane tested **no fixed rule beats standing aside**, and two
pre-declared heads plus an opportunity/direction stack all failed to convert. The recurring
mechanism is specific: *sign* is predictable (AUC 0.87) and *magnitude* is not (AUC 0.58), and
value needs magnitude. Separately, the market's own quoted price beats both model vintages on
every probability metric — measured twice, by two independent studies.

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
| **the market's ask beats both model vintages** on Brier, log loss, ECE and AUC | valid — §4.5, corroborated independently by §9 |
| **no fixed rule beats WAIT** on either venue, at any horizon tested | valid — §4.6, §4.7, §4.10 |
| **exit-timing ceiling is real**: +0.1005/share Polymarket, +19/+30 bps Binance 60/120m | valid — §4.6, §4.7 |
| **Binance 15m is dead on arithmetic**: ceiling median −2.63 bps vs a 12 bps round trip | valid — §4.7 |
| **sign is predictable, magnitude is not** (AUC 0.8731 vs 0.5831 on the same target) | valid — §4.9 |
| direction AUC **below 0.5** at 60m and 120m; conditioning on high opportunity does not lift it | valid — §4.10 |
| **0 of 25 artifacts are loadable by serving** | valid — §4.1 |
| Polymarket quote trajectories exist at 144–447 snapshots/round | valid — §4.2 |
| **`Φ(z)` is a strong late-round settlement probability** — AUC 0.877 (15m) / 0.914 (12m) at the last observation, ECE 0.0115, calibration slope 1.003, no parameters | valid — §13.1 |
| **ML does not beat that geometry** at either round length, as a feature or as a log-odds offset | valid — §13.1, §13.2 |
| the offset's correction is 3.7–4.7x its permuted control — real structure that does not generalise | valid — §13.2 |

`causal_decision_join.py` is classified **CAUSAL_BEST_EFFORT_HISTORICAL**, not gold standard:
`rule_paper_trades` has one generic `ts` and cannot prove it is exactly when the stored ask was
observed.

## 3. UNTESTED — ideas with no evidence either way

Not disproven. Listed so they are not mistaken for closed.

**Answered since this list was written** — do not re-open without new data:
exit-timing value (§4.6, §4.8, §4.9), MFE/MAE quantiles as ceilings (§4.7),
last-anchor-crossing and flip persistence (§4.4), conditional direction (§4.10).

| idea | why untested |
|---|---|
| alpha half-life / survival of an impulse | no forward dataset |
| mispricing half-life | needs the quote path joined to a decision, i.e. forward ledger rows |
| maker fill survival, queue position, markout | the Binance L2 recorder EXISTS and works; it has never been launched — §4.11 |
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
| queue/maker research | **not a code gap** — the recorder exists and works, it has never run; §4.11 |
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

**Independently corroborated.** The Phase 5B campaign (§9), a separate codebase run on a
different construction, measured model P(hold) Brier **0.1618** against the market's **0.1410**.
This study measured **0.1604** against **0.1453**. Two studies that share no code agree that the
market's quoted price is the better probability — which is why §6.3 names the market-prior
residual as the only modelling direction the evidence supports.

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

### 4.9 EV rule on predicted magnitude - `2026-08-02`  **FAILS, and closes the lane**

`research/ev_magnitude_rule_v1.py`. The classifier failed on threshold conversion, so this
replaces the threshold with the quantity that matters: regress the incremental value
`d = exit_value - hold_value` and exit when predicted `d > 0`. **No threshold, no grid, no free
parameter** — deliberately, because this is the SECOND look at this evaluation window.

Two looks is two chances at noise, so the gate uses a Bonferroni-corrected 2.5% day-block bound
and prints the uncorrected 5% beside it.

| policy | exits | mean/$1 | LCB 5% | LCB 2.5% |
|---|---:|---:|---:|---:|
| ALWAYS_HOLD | 0 | **−0.0103** | −0.0184 | −0.0199 |
| ALWAYS_EXIT | 10,888 | −0.0339 | −0.0365 | −0.0371 |
| CLASSIFIER@0.65 | 894 | −0.0107 | −0.0194 | −0.0213 |
| **EV_RULE (d > 0)** | 3,563 | **−0.0215** | −0.0285 | −0.0298 |
| RANDOM_MATCHED | 3,563 | −0.0200 | −0.0267 | −0.0279 |
| *PERFECT (hindsight)* | 2,628 | *+0.1005* | *+0.0941* | *+0.0923* |

**It loses to always-hold, to the classifier, and to random at matched count.**

**Why — the finding that matters more than the verdict.** The same features, same rows, same
target, two different questions:

```
SIGN      of d : AUC 0.8731    highly predictable
MAGNITUDE of d : AUC 0.5831    rank correlation -0.0165 with realised d
```

Realised mean `d` gets monotonically **worse** across predicted-`d` bands
(−0.0146 → −0.0334), so the regressor's ordering carries no usable information and is, if
anything, mildly inverted. The rank correlation is ~0, so that inversion is not leaned on.

**This closes the exit-timing lane on the current data.** The ceiling is real (+0.1005) and
capturing it requires knowing *how much* exiting gains. That signal is not in these 29 features:
you can predict *whether* to exit and not *how much it is worth*, and value needs the second.

**Deliberate stop.** Two pre-declared approaches have now been tested on this window. A third
would be multiple testing on data that has already answered twice. The next legitimate move is
forward data, not another rule — and there are still ZERO `FORWARD_UNTOUCHED` rows.

### 4.10 Binance 60/120m opportunity head - `2026-08-02`  **FAILS, last lane closed**

`research/binance_opportunity_head_v1.py` tests the blueprint's one remaining claim directly:
*"a 51% unconditional model may have better precision inside a small, high-opportunity
subset."* Two stages — an opportunity head `P(|return over H| > round trip)`, then direction
measured **overall and inside the top opportunity decile**.

All 13 features were verified backward-looking before use: each correlates more strongly with
the *past* absolute return than the future one. Training windows overlap; **evaluation windows
are disjoint** with a one-horizon purge gap at each split boundary.

| | 60m | 120m |
|---|---:|---:|
| opportunity head AUC | **0.6462** | **0.6405** |
| opportunity ECE | 0.0228 | 0.0345 |
| direction AUC, overall | 0.4853 | 0.4910 |
| direction AUC, top decile | 0.5632 ± 0.0429 (n=178) | 0.4752 ± 0.0416 (n=193) |
| lift vs 2 s.e. | +0.0779 vs 0.0859 | −0.0158 vs 0.0833 |

**Magnitude is predictable. Direction is not — and conditioning does not rescue it.** The
opportunity head genuinely works (AUC 0.65, well calibrated). Direction sits *below* 0.5 at both
horizons, and the top-decile lift falls short of its own two standard errors at 60m and is
negative at 120m. The hypothesis is not supported.

| policy | 60m mean bps | 60m LCB | 120m mean bps | 120m LCB |
|---|---:|---:|---:|---:|
| WAIT | 0.00 | 0.00 | 0.00 | 0.00 |
| ALWAYS_LONG | −12.88 | −14.44 | −13.80 | −16.90 |
| ALWAYS_SHORT | −11.12 | −12.60 | −10.20 | −13.21 |
| HEAD | −4.59 | −6.35 | −9.99 | −13.03 |
| RANDOM_MATCHED | −4.24 | −5.39 | −10.70 | −13.84 |
| *PERFECT (hindsight)* | *+19.45* | *+16.81* | *+30.23* | *+26.16* |

The head is indistinguishable from **random at matched trade count** at both horizons, and WAIT
beats everything. The hindsight ceilings (+19.45 / +30.23) independently reproduce the action
engine's earlier measurement (+18.46 / +31.37) from a separate implementation — a useful check
that the two agree.

**A verdict this file first got wrong.** It originally printed "conditioning LIFTS direction"
for the 60m result, because it compared point estimates with a fixed +0.02 margin and ignored
that n=178. Adding a Hanley-McNeil standard error flips it to "does NOT lift" — the honest
answer, and stricter than the one it replaced.

**This closes the last lane with untested headroom.** Every ceiling measured here is real and
large; nothing built so far captures any of it.

### 4.11 The Binance L2 recorder was never a code gap - `2026-08-02`

The ledger carried *"queue/maker research: no sequenced L2"* as though the capability were
missing. It is not. `backend/venues/binance_l2_recorder.py` is 804 lines implementing the full
USD-M diff-depth protocol — REST snapshot, `U`/`u`/`pu` buffering, gap detection that closes the
session and forces a resync — and it is **better than the `depth20` partial stream**, which
carries no resync obligation because it re-sends a truncated snapshot.

It is CI-gated, its selftest passes, and it is wired into `start_recorders_once.ps1` (enabled
unless `BTC_SKIP_BINANCE_L2_RECORDER=1`). **It has never recorded a byte.** No
`binance_l2.duckdb` exists anywhere, and no stdout/stderr log — because it was wired in after
`2026-07-04`, and the launcher has not run on this machine since.

**Verified working, for the first time**, with a bounded 45-second live run to a throwaway store:

```
430 diffs | 1 snapshot | 1 session | 0 gaps
first_update_id / final_update_id / previous_update_id     proper U/u/pu
received_ts_ms / event_ts_ms / transaction_ts_ms           three clocks
applied / disposition / payload_sha256 / book_top_sha256   integrity fields
```

**The gap this exposed is not L2 — it is that nothing asked whether a recorder had ever run.**
`backend/audit/recorder_evidence_check.py` now reports, per recorder wired in the launcher:
whether it ever ran, whether its store holds rows, and when it last wrote.

```
binance_l2_recorder.py         ever ran False            0 rows   NEVER_RAN
l2_recorder.py                 ever ran True    25,809,455 rows   HAS_DATA
live_btc_updown_recorder.py    ever ran True       106,854 rows   HAS_DATA
microstructure_recorder.py     ever ran True       198,037 rows   HAS_DATA
multi_venue_recorder.py        ever ran False   20,085,631 rows   HAS_DATA (synced from the box)
```

A passing selftest proves the code is correct. It says nothing about whether the process ever
started — the same family as the manifest gate in §4.1 that passed while 0 of 25 artifacts were
loadable. This is now the third instance of that defect class found here, and the second one
found in a check I wrote myself.

## 10. Phase 5C triage - `2026-08-02`

52 further tests were proposed against existing data, with 15 recommended next. Two of those 15
were **prefilters**, and the correct order was to run them first: they decide whether the other
13 can answer anything. Both are built, and they do decide it.

### 10.1 What each data source can actually detect

Day-clustered minimum detectable shift in win rate, 80% power — the inference level
`day_block_lcb` already uses:

| source | rows | **days** | MDE | supports |
|---|---:|---:|---:|---|
| Binance 1-minute bars | 518,400 | **360** | **7.4 pts** | hypothesis tests |
| Polymarket checkpoints | 50,272 | **21** | **25–30 pts** | descriptive work only |
| `multi_venue` (trades, bookTicker, OI, funding) | 20,085,631 | **2** | **99 pts** | nothing |

`multi_venue` holds 20 million rows across **two days**. 17.6M of them are bookTicker. Row count
and evidence are not the same quantity, and this is the clearest example in the repository.

Clustering also matters more than row count. On the checkpoint data the intra-cluster
correlation at round level is **0.347** — 6.5 checkpoints from one round carry about 2.2
independent observations, not 6.5.

### 10.2 Effect size against cost (`130`)

Every **realisable** effect measured so far scores below 0.75x the cost it must clear. Only the
hindsight ceilings clear 1.25x, and a ceiling is not a strategy:

| candidate | gross | cost | ratio | band |
|---|---:|---:|---:|---|
| direction @60m / @120m | 0.00 | 12 bps | 0.00 | IRRELEVANT |
| opportunity gate @60m | 7.41 | 12 bps | 0.62 | RESEARCH_ONLY |
| hold-vs-exit classifier | 0.0004 | 0.0499 | 0.01 | IRRELEVANT |
| complete-set lock | 0.00 | 0.0499 | 0.00 | IRRELEVANT |
| *perfect exit @120m* | *42.23* | *12 bps* | *3.52* | *ELIGIBLE (hindsight)* |
| *perfect Polymarket exit* | *0.1504* | *0.0499* | *3.01* | *ELIGIBLE (hindsight)* |

The prefilter's selftest pins the case that motivated it: a 0.419 bps microprice effect against
a 9 bps hurdle scores **0.05x** and is killed on sight.

### 10.3 Triage of the recommended 15

| test | verdict |
|---|---|
| **130** effect-size-to-cost, **136** effective sample size | **BUILT** |
| 101 jump vs diffusion, 103 volatility half-life, 106 MFE/MAE joint | viable — Binance 360-day window |
| 89 Brier decomposition, 94 last-crossing timing, 97 terminal margin, 123 monotonicity | viable **as description only** — no significance claim survives 21 days |
| 91 disagreement→magnitude, 93 anchor dwell, 96 crossing velocity | **dead** — inferential targets far below a 25-point MDE |
| 110 signed-flow saturation, 111 flow without price response, 117 OI quadrants | **dead** — the source holds 2 days |

Six of fifteen cannot answer their question. Six more of the fifty-two (sections C and D, tests
109–122) depend on the same 2-day source and are equally unrunnable.

### 10.4 What this means

The proposal's own closing rule — *establish that an effect is large enough, lasts long enough
and maps to a monetizing instrument before asking whether it is predictable* — is correct, and
applying it first removed 40% of the recommended package before any modelling.

**The binding constraint has not changed and is not a research question.** It is 21 days of
Polymarket and 2 days of cross-venue data. More tests on this window produce more confident
descriptions of a sample too small to support them.

### 10.5 The seven viable studies - all built, all run

**89 Brier decomposition.** P(hold) loses **+0.0144** of Brier to the market. **99.3% of that
gap is RESOLUTION** (+0.0143); calibration contributes +0.0001. The model is *well calibrated*
and simply carries less information — and the gap widens toward settlement:

| bucket | market resolution | P(hold) resolution |
|---|---:|---:|
| before T−5m | 0.0215 | 0.0174 |
| T−90s to T−30s | 0.0403 | 0.0176 |
| **T−30s and later** | **0.0549** | **0.0109** |

Early in a round the model is nearly as informative as the price. By the last 30 seconds the
market has 5x its resolution. **Recalibration cannot fix a resolution deficit** — this is why
the market-prior residual is the only supported modelling direction.

**94 Last-crossing timing.** P(the final crossing has already happened), by checkpoint:

| checkpoint | already final | mean crossings left | leader wins |
|---:|---:|---:|---:|
| 15 s | 89.7% | 0.12 | 82.1% |
| 60 s | 81.5% | 0.30 | 81.8% |
| 240 s | 50.8% | 1.40 | 68.1% |
| 720 s | 38.8% | 2.70 | 66.4% |

At 240 s the leader is final only half the time yet wins 68% — because 57% of crossings revert.

**97 Terminal margin.** Median terminal margin **$28.5**; **4.4%** of rounds finish within $2
of the anchor and **11.4%** within $5. Those are rounds the settlement *source* decides, not the
price path — and §4.4 measured path-vs-official disagreement at 10.7% at T−15s.

**123 Probability monotonicity.** The market's own ask is **perfectly monotone in win rate (0
violations)** and **non-monotone in net value (2 violations)**, negative in 8 of 10 deciles.
A higher probability costs a higher ask. This is the cleanest available statement of why AUC
does not license threshold trading.

**101 Jump vs diffusion.** Median jump share of hourly variation is **0.087**; only 1.9% of
windows are jump-dominated. And the share *falls* as moves grow — 0.117 in the smallest quintile
to **0.061** in the largest, 0.057 in the top 5%. **Large moves are predominantly continuous**,
which contradicts the usual assumption and means stops are meaningful in this lane.

**103 Volatility half-life.** AR(1) on log RV gives phi 0.7392, **half-life 34.4 minutes**.
P(still elevated) decays 63.2% → 47.7% at 120 m against a 20% baseline. A 120-minute hold spends
most of its life in a different regime from the one that justified entry.

**106 MFE/MAE geometry — the decisive one.** Across a frozen 4x4 target/stop grid on 8,639
disjoint 60m windows, **no cell clears costs**. Best is target 20 / stop 50 at **−9.70 bps**
against a 12.0 bps round trip. At 10/10 the barriers are near-symmetric (**48.4% target vs 48.7%
stop**) — exactly what a martingale predicts. A bar spanning both barriers is charged as a
*stop*, since the intrabar order is unknown and assuming the favourable one manufactures edge.

**Narrowed after review, `2026-08-02`.** This proves that *no unconditional fixed bracket in
the frozen grid has positive expectancy across the full tested 60m population*. It does **not**
prove that no future state selector could find a sparse subset where barrier ordering is
asymmetric — a sufficiently informative model could in principle select contexts where
`P(target before stop | state)` differs materially from the unconditional 48.4%/48.7%.

What the evidence does support: **current realisable models do not extract such a subset**
(§4.10, §4.8, §4.9). The lane is closed until a prefiltered effect clears §10.2, not closed
forever.

### 10.6 What the seven add up to

Nothing changes the evidence position, and two things sharpen it:

1. **The model's deficit is resolution, not calibration** (89), which rules out the entire class
   of "recalibrate P(hold)" remedies and points at the market-prior residual.
2. **The path forbids an unconditional fixed bracket** (106) and **forbids a RAW PROBABILITY
   threshold** (123), independently of any forecast. Narrowed after review: 123 rules out
   `buy when p > 0.70`. It does **not** rule out an economic residual rule of the form
   `fair value - executable ask - costs - uncertainty reserve`. P(hold) cannot supply that
   today because §10.5 shows it has *less* resolution than the market — but a genuinely
   orthogonal information source could in principle.

Three findings are genuinely new and reusable: large moves are continuous not jumpy (101), the
volatility half-life is 34 minutes (103), and half of all rounds at T−4m have not yet had their
final crossing (94).

## 11. Phase 5D - admission contract and the sufficiency boundary - `2026-08-02`

### 11.1 The contract, as code

`research/phase5d/admission.py` implements the Phase 5D admission contract verbatim and
adjudicates the declared 5D + 5D-B backlog. **0 of 19 may currently run as economic
experiments:**

```
COLLECT_MORE_DATA   12    design is fine, the evidence does not exist yet
DESCRIPTIVE_ONLY     7    diagnostic by design; informs a decision, not a trade
```

Check order is load-bearing: **a sub-cost effect is refused before power is considered**, so
more data can never rescue an effect too small to pay. Selftested with a 5,000-cluster
sub-cost declaration that still returns `REJECT_SUBCOST`.

A first version labelled 157 `REJECT_NO_EXECUTABLE_ACTION` — its own specification says *"this
test should not generate trades"*. Diagnostic intent is now declared, so the most important
test in the backlog no longer reads as refused.

### 11.2 Test 157 — the sufficiency boundary. **Preregistration A is retired.**

Three chronological folds, out-of-fold scoring, frozen feature families, judged on
**incremental resolution** because §10.5 established the deficit is resolution and not
calibration.

| family | resolution | Δ resolution |
|---|---:|---:|
| A market only | 0.0363 | 0.0000 |
| B + BTC state | 0.0363 | −0.0000 |
| C + volatility | 0.0361 | −0.0002 |
| D + model outputs | 0.0363 | −0.0000 |
| E + book state | 0.0363 | −0.0001 |
| F + everything | 0.0361 | −0.0003 |
| **Z null (noise)** | **0.0364** | **+0.0001** |

**No family beats a matched noise arm — the null has the largest gain of all.** Nothing in the
recorded features adds information beyond the executable market price.

**Verdict: `NO_INCREMENTAL_INFORMATION`.** Preregistration A is **retired for the current
feature set**. The protocol file is left byte-identical so its hash stays valid; retirement is
recorded in `research_status.py`, the same discipline used for the retracted studies.

**Narrow reading, deliberately.** This retires the residual lane *for these features*. If the
new recorders bring genuinely new inputs — model revisions, settlement-source basis, paired
L2 — that is a different feature set and requires a **new protocol with its own hash**. The
idea is not refuted; this feature set is.

**A bug this caught in itself.** The first run reported `RESIDUAL_LANE_SUPPORTED` on a
+0.00004 gain, because the verdict tested `> 0` with no materiality bar. Adding the matched
null flipped it to `NO_INCREMENTAL_INFORMATION` — the honest answer, and the opposite of the
convenient one.

### 11.3 Two corrections to the admission system - `2026-08-02`

**The MDE was dimensionally invalid.** `z * sqrt(p(1-p)/k) * 100` is in PERCENTAGE POINTS, and
most of the backlog is denominated in net bps, dollars per share, a Brier difference or a time
to event. The first version applied the binary formula to all of them — including its own
selftest, which declared `monetized_quantity="net bps"`.

Every declaration now names an `Endpoint`, and power is computed in that endpoint's units:

| endpoint | MDE |
|---|---|
| `BINARY_RATE` | `z * sqrt(p(1-p)/k) * 100` — percentage points |
| `CONTINUOUS_CLUSTER_MEAN` | `z * cluster_sd / sqrt(k)` — endpoint units |
| `PAIRED_CONTINUOUS` | same, on the SD of paired differences |
| `PROPER_SCORE_DIFFERENCE` | same, on the SD of score differences |
| `SURVIVAL_EVENT` | `2z / sqrt(qualifying events)` — events, not rows |

A declaration missing what its endpoint needs reports **`POWER_UNITS_UNRESOLVED`** rather than a
number that does not apply. The cluster SD must come from daily or weekly aggregates, never
from rows — row-level SD understates it by the design effect.

**157's verdict was overstated.** Renamed to
**`NO_DETECTABLE_INCREMENTAL_RESOLUTION`** — *for current features, under the tested learner, at
this sample size.* 21 independent days and one learner family cannot prove absence.

Day-block bootstrap CIs on the resolution difference now make that concrete:

| family | Δ resolution | 95% day-block CI |
|---|---:|---|
| B + BTC state | −0.0000 | [−0.0001, +0.0000] |
| C + volatility | −0.0002 | **[−0.0004, −0.0000]** |
| D + model outputs | −0.0000 | [−0.0002, +0.0001] |
| E + book state | −0.0001 | [−0.0002, +0.0001] |
| F + everything | −0.0003 | **[−0.0004, −0.0000]** |

Detectable positive gain: **none**. Detectable *harm*: **C and F** — adding features measurably
hurts. (My first write-up said "every CI spans zero"; two do not.)

Recorded status is now `RETIRED_NO_DETECTABLE_INCREMENTAL_RESOLUTION`. Any A2 must freeze a new
information source, exact learner families, transforms, a search budget, null controls and a
minimum material lift.

### 11.4 Test 164 — cost versus information. **A bounded maker study is justified.**

An accounting identity over 50,272 eligible settled checkpoints — nothing fitted:

```
gross informational edge (at mid)   +0.0044
  spread burden                     -0.0052
  fee burden                        -0.0097
= net, hold to settlement           -0.0105
```

**Corrected `2026-08-03`.** The `+0.0044` is `OBSERVED_PRE_COST_MIDPOINT_SURPLUS` — an observed
accounting quantity, not an established edge — and it is now reported under blocked weighting,
because checkpoints cluster inside rounds and rounds cluster inside days:

```
raw, opportunity-weighted   +0.0044   day-CI    [-0.0007, +0.0099]   SPANS ZERO
equal-DAY weighted          +0.0043   day-CI    [-0.0005, +0.0095]   SPANS ZERO
equal-ROUND weighted        +0.0114   round-CI  [+0.0063, +0.0161]   excludes zero
NON-OVERLAPPING (1/round)   +0.0023   day-CI    [-0.0063, +0.0107]   SPANS ZERO
```

**The clustering choice is declared rather than selected:** day clustering governs, because
volatility, regime and recorder health cluster within a day. Under it the surplus is **not
distinguishable from zero** before any cost is charged.

Note what the choice does and does not change. **All four point estimates are positive — the
sign is not in dispute.** What clustering changes is the magnitude and whether the surplus is
statistically distinguishable from zero at all. The round-clustered interval collapses
checkpoints to per-round means and resamples whole *rounds*, so its assumption is round-level
independence, not checkpoint-level. It is nonetheless weaker than the declared day-level
standard, because rounds inside one day share volatility, regime, market conditions and
recorder state. The non-overlapping arm (one checkpoint per round) spans zero as well.

**Verdict `CURRENT_TAKER_COST_STRUCTURE_NEGATIVE`**, decided on the CI **upper** bound rather
than the point estimate: even at +0.0099 the surplus sits below the 0.0149 cost floor.

**Corrected again `2026-08-03`.** The verdict was briefly `TAKER_EXECUTION_CANNOT_RESCUE`,
and the text claimed *"no reduction in taker cost clears it"*. That does not follow from these
numbers — a cost at or below 0.0099 **would** clear the optimistic bound:

```
required reduction, optimistic bound   0.0149 -> 0.0099   = 33.6%
required reduction, point estimate     0.0149 -> 0.0044   = 70.3%
```

Both break-even costs are now reported, because the estimator moves the requirement by a factor
of two. What the evidence supports is the narrower claim: **the current cost structure is
negative even on the favourable end of the interval.** It does not establish that cheaper taker
execution would become profitable — the surplus CI spans zero, so a cheaper channel would be
trading on a quantity not distinguishable from nothing.

The **maker** question is separate and stays open — it is the only route that removes the
spread and taker fee instead of paying them. It is preregistered as D and **may not be scored
yet**: its precondition is a day-clustered lower bound above zero, which currently FAILS.

This sits beside 157 without contradiction: 157 says nothing *we record* adds resolution beyond
the price; 164 says the price itself may sit slightly below settlement value at the mid, by an
amount the data cannot separate from zero. Whatever is there is in the market's own quote, not
in our features — and the spread is larger than it either way.

### 11.5 Survival endpoints split into four estimands - `2026-08-03`

`Endpoint.SURVIVAL_EVENT` carried one MDE formula, `2z/sqrt(events)`. That is the minimum
detectable **log hazard ratio** specifically, and it was being applied to any time-to-event
question — so a study asking about median time would have received a power number in the wrong
units and passed a gate it never met. Dimensional invalidity is not conservative; it is
arbitrary.

| estimand | MDE units | needs |
|---|---|---|
| `SURVIVAL_LOG_HAZARD_RATIO` | log hazard ratio, `2z/sqrt(events)` | event count |
| `SURVIVAL_PROBABILITY_DIFFERENCE` | probability at a fixed horizon | event count, binary formula |
| `RESTRICTED_MEAN_TIME_DIFFERENCE` | **time** | a time-unit `cluster_sd` |
| `MEDIAN_TIME_DIFFERENCE` | **time** | a time-unit `cluster_sd` |

The last two return `POWER_UNITS_UNRESOLVED` when no time-unit dispersion is declared. A
declaration that cannot be powered is refused rather than approximated. Admission selftest:
**17 checks**.

### 11.6 Preregistration D sealed, unscorable by construction - `2026-08-03`

`PREREG_FORWARD_D_BOUNDED_MAKER_STUDY_V1.md`, sha256
`e07fa7e03fb6ab6a8ae7f40733af3a461f4f7a5ba944de7e0a99d1f545da61e6`, registered in
`EXPECTED_PROTOCOLS`. **11/11 hashes intact.**

It is frozen *now*, while its own precondition fails, precisely so the design cannot be shaped
by the data that will decide it. Five fill bounds are all reported and none is selected; bound 1
(immediate fill) is tagged `requires_hindsight_or_unrealistic_fill` and is a **ceiling, never a
result**. The key quantity is **net value per order SUBMITTED** — a strategy can earn on its
fills and fill too rarely to matter. Maker fees are not assumed zero merely because taker fees
disappear.

Three kill rules close the lane immediately, the sharpest being *post-fill adverse selection >=
spread + taker-fee saving*: if that holds, liquidity provision merely swaps explicit costs for
informed-flow losses.

### 11.7 Protocol B/C readiness is performance-blind - `2026-08-03`

`backend/bc_forward_readiness_report.py` reports counts and coverage for the two sealed
protocols and **nothing else**. B and C are scored once; any interim view of PnL, AUC, model
ranking, threshold or action preference is a look at the evidence, and a protocol that has been
peeked at is no longer a protocol.

The blindness is enforced at runtime, not promised in prose: `assert_performance_blind()`
inspects emitted **keys** against a forbidden pattern and raises `PerformanceLeak`. A count
named `resolved_action_snapshots` passes; one named `mean_pnl` cannot be printed at all. The
selftest plants seven performance-shaped keys and requires each to be refused.

Exactly four statuses are emitted: `NOT_STARTED`, `COLLECTING`, `DATA_GATE_INCOMPLETE`,
`DATA_GATE_COMPLETE_UNSCORED`. The terminal one says **UNSCORED** on purpose — reaching a
complete gate does not trigger scoring, which stays a separate deliberate command.

**Both protocols currently report `NOT_STARTED`:** ledger 0 rows, open-position recorder 0
rows across all five tables. Schemas exist; evidence does not.

### 11.8 The vacuous-pass defect, caught in the readiness report itself - `2026-08-03`

The first version wrapped its reads in `except Exception: return zeros`. It printed
`NOT_STARTED` — which was, by luck, the true answer. That is the whole problem: a locked live
writer, an uninstalled duckdb or a renamed table would have printed the *same* `NOT_STARTED`,
and "collection has not begun" is indistinguishable from "I failed to look" in the one
direction that matters.

It also hardcoded every Protocol C field to `0` while `open_position_actions.duckdb` held five
real tables with the exact columns needed. Those numbers were correct and **not measured** — a
report that would have kept printing zeros after data arrived.

Both fixed: `_connect()` raises `SourceUnreadable` on a missing file, an unopenable database or
a drifted schema, and the runner exits **1** without emitting a status, because *"I could not
look"* is not one of the four. Every count now comes from a real query. Negative-tested against
a planted missing file and a planted renamed table. Selftest: **15 checks**.

This is the fourth instance of the same defect class in this repository (manifest gate,
recorder-evidence gate, artifact serviceability, now readiness). The pattern is always a check
that passes while the property it guarantees is false.

### 11.9 The Protocol C denominator, and three more readiness defects - `2026-08-03`

Four further corrections, the first of which could have announced a complete gate for a
comparison that did not exist.

**The gate counted the wrong thing.** `resolved_action_snapshots` was
`count(DISTINCT position_snapshot_id) WHERE any arm is complete`. Protocol C is *defined* by
the five-arm comparison from one state — so 1,000 snapshots carrying a complete `HOLD` and
nothing else would have satisfied the count and printed `DATA_GATE_COMPLETE_UNSCORED` while
`EXIT`, `REDUCE_50`, `SWITCH` and `LOCK` were absent entirely.

The gate is now `full_resolved_five_arm_rounds`: distinct **rounds** (Protocol C's frozen unit
is independent resolved opportunities; many snapshots of one round are one opportunity observed
repeatedly) in which all five arms are complete **from the same causal snapshot**, settlement is
resolved, the paired book carries a fee rule, its two sides are within the skew bound, and the
book was received *before* the snapshot it informed. `any_arm_complete_snapshots` survives as a
diagnostic, explicitly labelled *NOT the gate*.

Negative-tested against five planted fixtures — the HOLD-only case, a skewed paired book, a book
received after its snapshot, arms without resolved settlement, and a genuine five-arm database
that **does** complete, so the gate is not merely always zero.

**Protocol B's zeros were still fabricated.** Four crossing fields were hardcoded, which is the
same defect in delayed form: correct today, and frozen at 0 forever after the evidence arrived.
They now require a `post_entry_crossing_outcomes` table (12 named columns); absent it,
`MeasurementNotWired` fires and the B section reports `NOT_WIRED` with **no status at all** —
"not yet built" must not print as "measured and found zero".

**`COLLECTING` was unreachable.** Every numeric state returned before it. `NOT_STARTED` and
`COLLECTING` are both zero-evidence states, and counts cannot separate them, so `gate_status()`
now *requires* a `writer_active` argument, read from `open_position_capture_attempts` —
attempts, not successes, because a recorder that runs and rejects everything is alive and
collecting nothing. Dead writer + zero evidence → `NOT_STARTED`; live writer + zero evidence →
`COLLECTING`.

### 11.10 Two protocol registries disagreed - `2026-08-03`

Protocol D was sealed into `EXPECTED_PROTOCOLS` (the hash gate) and **not** added to
`FORWARD_PROTOCOLS` in `research/research_status.py`. Nothing noticed, because that dict had
**zero consumers and no test** — a registry that cannot be wrong is not a registry, it is a
comment.

Fixed: D added, and `check_protocol_registries()` now requires the two to agree, wired into CI
with a negative test that drops a sealed protocol and confirms the check fails. Also added
`PROTOCOL_D_STATUS = "SEALED_PRECONDITION_UNMET"` with its precondition as a string, because
*frozen* and *runnable* are different states and a sealed protocol sitting in a registry
otherwise reads as a green light.

### 11.11 Blindness scope and the matcher bug - `2026-08-03`

**Blindness was top-level only.** It now walks the whole assembled payload recursively,
including nested lists, and runs after statuses are attached. That immediately caught a bug in
the matcher itself: substring matching rejected `ledger`, for containing *"edge"*. Whole-token
matching on snake_case fixed it, and the refusal now names the offending token. A blindness rule
that fires on honest field names gets worked around rather than obeyed. Selftest: **40 checks**.

## 12. External audit of `83652ce` — verified, and three defects fixed - `2026-08-03`

An external audit raised twelve P0/P1 defects. I verified the load-bearing ones in source rather
than accepting them. **The audit was substantially correct**, including about code committed
hours earlier in this same ledger.

### 12.1 P0-06 — my own Protocol C gate was vacuous. Fixed by the parallel session.

§11.9 recorded the C gate as fixed and negative-tested. It was not. The condition
`count(*) FILTER (WHERE settlement_floor_net IS NULL) = 0` **can never be false**:

```
settlement_floor      = min(up_after, down_after)          # available at snapshot time
settlement_floor_net  = floor + cash_flow - net_cost_basis
schema                = settlement_floor_net DOUBLE NOT NULL
```

It is a guaranteed worst-case floor computed from share counts, not a realized settlement, and
it is declared `NOT NULL`. The fixture that "proved" the check worked inserted a NULL the real
recorder cannot produce — so it demonstrated that NULLs are rejected, never that settlement was
resolved. **1,000 five-arm rounds could have completed the gate with zero realized outcomes for
any arm.**

This is the fifth instance of the vacuous-pass class, and the first one written *by the tooling
built to prevent it*. The lesson is narrow and worth stating: a fixture must be constructed from
the same schema the writer uses, or it tests a state that cannot occur.

Fixed in the parallel session's `open_position_action_outcomes` work — the gate now requires all
five arms to carry an outcome row with `settlement_source LIKE 'official:%'`. Verified in source.

### 12.2 P0-02 — the round-state trainer trained on its own future

`_join_keepers` keyed features as `snapshot_ts // 60_000 * 60_000` — the bar **containing** the
decision. Research-matrix rows are keyed by bar OPEN time (the convention
`_official_ohlc_parity` validates against official Binance klines) while their OHLC and keeper
values span the whole minute. So a decision at `12:30:15` was trained on high/low/close/volume
running through `12:30:59`, on every row.

Fixed: `causal_feature_ts_ms()` returns the last bar that had **closed** by the snapshot, with
`bar_available_from_ms()` naming the availability rule. Pinned by
`backend/test_round_state_causal_contract.py`, which reconstructs the old rule and requires it
to fail the same assertion.

**All v1 round-state metrics are non-causal.** The schema is bumped to
`2026-08-03-round-state-shadow-v2` so v1 artifacts are *refused*, not merely regenerated —
otherwise a leaked model keeps serving quietly under a corrected trainer.

### 12.3 P0-01 — a successful retrain produced an artifact serving always rejected

The trainer stamped `<schema>-1000d`; the loader required equality with `<schema>`. Every
freshly trained round-state artifact was refused, and the failure surfaced as "artifact missing"
rather than as a version contract — indistinguishable from the retrain not having run.

Fixed by separating `ARTIFACT_SCHEMA_VERSION` (compatibility) from `TRAINING_WINDOW_DAYS` (run
metadata). The window is already policed by artifact identity and the feature contract; encoding
it in a string equality made those gates unreachable.

The new test immediately caught a **second live variant**: with `BTC_HISTORICAL_DAYS` unset the
tag is the literal `"na"`, stamping `-nad`, which a digits-only suffix rule still refused.

### 12.4 Database identity — the default store is not the live archive

`backend/datastore_identity.py` resolves one store, records its identity, and **refuses to guess
in strict mode**. Measured:

| store | tables | span |
|---|---|---|
| `data/analytics.duckdb` (the default) | 35 | **2026-07-02 → 07-04** |
| `data/btc_duckdbs/analytics.duckdb` | 30 | **2026-07-05 → 07-25 14:59** |
| `btc_full_project/btc-tool/data/…` | 30 | identical copy |

**The two spans are disjoint.** The default path — which every module resolves to when
`BTC_DB_PATH` is unset — ends where the live archive begins. The 21-day Polymarket window that
every conclusion in this ledger rests on lives in `btc_duckdbs`, and its last row is
`2026-07-25 14:59`, the exact minute the recorders went dark.

A correct query against the wrong store is still wrong, and nothing in the result says so.
Strict mode now names both candidates and raises instead of defaulting.

### 12.5 One more fail-open, mine

The five-arm gate used `abs(coalesce(p.pair_skew_ms, 0)) <= 2000`, scoring an *unmeasured* skew
as a perfect zero — fail-open on the exact axis the check exists to enforce, while its
neighbouring conditions fail closed. Now `IS NOT NULL` and compared directly, in both places.

## 13. External audit of `a68c6fb`, conditional-path studies, and the geometry head - `2026-08-05`

A third external scan raised 19 defects and proposed a rebuild. Every claim was checked against
source before anything was changed; **one was false** (it asserted the remote carried newer
commits requiring a rebase — `origin/master` was exactly the last push, 2 ahead / 0 behind, and
acting on it would have been destructive). Two more were correct observations with the wrong
diagnosis, recorded below.

The engineering half is documented in
[`docs/active/TRUTH_LAYER_REMEDIATION_2026-08-05.md`](active/TRUTH_LAYER_REMEDIATION_2026-08-05.md)
(grading/decision layer) and
[`docs/active/TRAINING_PIPELINE_REPAIRS_2026-08-05.md`](active/TRAINING_PIPELINE_REPAIRS_2026-08-05.md)
(training, execution, provenance). This section records only what it means for the **evidence**.

### 13.1 Conditional path forecast V1 — the lattice adds nothing to geometry

`research/conditional_path_forecast_v1.py`. 200,000 1m bars, **13,317** 15m rounds and **16,646**
12m rounds, temporal 70/30 split with a one-round purge.
Detail: [`CONDITIONAL_PATH_FORECAST_V1_RESULT_2026-08-05.md`](active/CONDITIONAL_PATH_FORECAST_V1_RESULT_2026-08-05.md).

The structural baseline is a driftless random walk, no training and no features:

```text
z = (price_now - anchor) / (sigma_1m * sqrt(minutes_remaining))     P_base = Phi(z)
```

| 15m settlement target | obs=0 | obs=1 | obs=3 | obs=5 | obs=7 | obs=10 |
|---|---:|---:|---:|---:|---:|---:|
| structural | 0.500 | 0.613 | 0.683 | 0.736 | 0.797 | **0.877** |
| Full ML | 0.500 | 0.592 | 0.674 | 0.723 | 0.789 | 0.873 |
| **ML − structural** | +0.000 | **−0.020** | −0.010 | −0.014 | −0.009 | −0.004 |

Overall: 15m AUC **−0.0071** and Brier **−1.37%** versus the baseline; 12m **−0.0060** and
**−1.03%**. Both negative at every checkpoint that matters.

**The finding is the near-miss.** Read alone, "Full ML reaches 0.873 AUC on BTC direction" is a
spectacular result. Printed beside the baseline it is arithmetic: being far above the anchor
with one minute left means you settle up. **Every point of the rise belongs to the baseline**,
and the revision table originally printed only the ML row. The baseline row and the
`ML − structural` gap were added precisely because the rise is real and worthless at once.

The model is ahead only at `obs=0`, where the baseline is exactly 0.500 by construction
(`z = 0`). Best cell **0.527** — consistent with the established 0.50–0.535 ceiling across 13
model families, and it decays to nothing by the settlement checkpoint, the only one a round pays
out on.

**A defect in the study itself, found by re-reading it.** The arm labelled "ML residual" was not
a residual — `np.column_stack([Xtr, p_base_train])` makes the baseline a *feature column*, which
is why both arms printed identical numbers. That is what motivated §13.2.

### 13.2 Conditional offset V2 — a true log-odds offset. **FAILS its Brier gate**

`research/conditional_offset_v2.py`. Four arms — baseline, offset, offset_permuted,
zero_correction — 42 day-blocks per round length, paired on identical rows.
Detail: [`CONDITIONAL_OFFSET_V2_RESULT_2026-08-05.md`](active/CONDITIONAL_OFFSET_V2_RESULT_2026-08-05.md).

```text
                          15m                              12m
d_brier     +0.00125  [+0.00030, +0.00217]  WORSE   +0.00131  [+0.00051, +0.00214]  WORSE
d_log_loss  +0.00137  [-0.00085, +0.00344]  incon.  +0.00179  [-0.00016, +0.00372]  incon.
d_auc       -0.00298  [-0.00636, +0.00065]  incon.  -0.00409  [-0.00689, -0.00123]  WORSE
```

**Verdict: not promotable — and deliberately not "closed".** It fails on Brier at both lengths
and is *inconclusive* on log loss, which is the metric a log-odds additive model is fitted
through. An earlier draft of this write-up called it a clean loss from point estimates alone;
with intervals that overstates the evidence.

**The sharper finding is the permuted control.** Mean |correction| against the same model on
shuffled features:

```text
15m  real 0.2545   permuted 0.0685   ratio 3.7x
12m  real 0.2596   permuted 0.0547   ratio 4.7x
```

The correction is **not noise** — it responds to genuine structure — **and acting on that
structure makes the forecast worse.** "The features contain structure that does not generalise"
is a different and more useful statement than "the model learned nothing". Note also that even
the *permuted* arm is significantly worse on 15m Brier, which is a statement about how little
miscalibration `Φ(z)` leaves to correct: ECE 0.0115, calibration slope 1.003.

**Three of my own claims were wrong and are corrected here rather than quietly dropped:**

| claim | why it was wrong |
|---|---|
| "the offset structurally cannot damage the baseline" | it cannot *omit* the geometry; a large `f(X)` overturns it, and does |
| "mean \|correction\| 0.25 shows the model doing real work" | with labels drawn **from `p_base`**, where the true correction is identically zero, this config still emits ≈0.175. Magnitude measures flexibility, not signal |
| a guard asserting `num_iteration=0` | in LightGBM that means **all** iterations, not none — the guard could never fire. Replaced with an arithmetic reconstruction assertion |

**The `init_score` trap, guarded by construction.** `predict_proba` has no per-row init for new
rows, so the offset must be re-added by hand or each test row's geometry is silently dropped.
The `zero_correction` arm reproduces the baseline **to the last digit** at both round lengths,
which proves the reconstruction rather than asserting it.

**Not tested, and it is the comparison that decides tradeability:** nothing here is measured
against the **Polymarket price**, which can compute this same `z`. Beating geometry is necessary
and not sufficient.

### 13.3 The geometry endpoint head — wired with `AUTHORITY = "NONE"`

Geometry won, so geometry is what was wired. `backend/geometry_endpoint_head.py`, surfaced in
the app as display and record only.

Renamed from `conditional_path_head` before wiring: it estimates `P(settlement > anchor)`, an
**endpoint** question, in a repository whose central defect class is first-touch-versus-endpoint
confusion. Emitting several checkpoint probabilities does not make it a path forecast, and the
old name invited exactly the misreading that retracted five studies.

Pinned in the module and asserted in tests: `TARGET_CONTRACT = ENDPOINT_ABOVE_ANCHOR`,
`MODEL_ASSUMPTION = ZERO_DRIFT_RELATIVE_PRICE_DIFFUSION`, `SIGMA_UNITS =
PER_MINUTE_LOG_RETURN_STDDEV`, `TIME_UNITS = MINUTES`. Units are checked numerically, not just
declared — feeding seconds where minutes are expected shifts `z` by exactly `7.746` (`√60`).
Every invalid input returns `None`; there is no `0.5` fallback, because a fabricated coin flip
is indistinguishable in the output from a computed one.

**Authority is enforced by an AST import test**, not a keyword grep: no decision, sizing or order
module may import it. A grep would have missed `score`, `edge`, `rank` and `eligible`.

**A causality bug the audit surfaced.** The forming-bar guard was `if closed and closed is kl:`
— dead code, since a list comprehension always allocates a new object. On a feed that omits
`is_closed`, the forming bar entered the sigma estimate.

### 13.4 The pre-retrain gate

[`docs/active/PRE_RETRAIN_GATE_2026-08-05.md`](active/PRE_RETRAIN_GATE_2026-08-05.md), completed
and documented **before** any challenger bundle was created.

**Superseded twice since, in both directions — corrected here `2026-08-06`.** The table below is
the current state; the two rows that moved are the point of the section.

| # | gate | opened | now | by |
|---|---|---|---|---|
| 4.1 | OOF / serving parity | FAIL | **PARTIAL** | `e9a394f`, `376ba87`, `3989e87` |
| 4.2 | historical snapshot broadcasting | FAIL | **PASS** | `6b0bb1a` |
| 4.3 | VWAP / feature contract | FAIL | **PASS (clearable)** | `154cccf` |
| 4.4 | settlement head exists and is trained | FAIL | **PASS (clearable)** | `cb1f4cf` |

**4.1 was marked PASS prematurely, and that was my call.** An external audit disputed it and was
right on all three residuals. Two were fail-open defects of exactly the class this ledger keeps
recording: a seat whose wrapper rejected `sample_weight` was **logged and then fitted anyway** —
the warning said its OOF probabilities do not match its served pipeline, and then those
probabilities reached the stacker regardless, because a warning is not a safety boundary. And
folds wrapped seats in `CalibratedClassifierCV(cv=2)`, an integer `cv` that proves neither
chronology, purge, embargo nor absence of label overlap, so OOF calibration came from a
different protocol than the served one. Both now drop the seat instead of substituting a weaker
fit.

The third — fold-local **regime similarity** — is open and was deliberately not attempted:
production weights are recency x similarity x class x ambiguity, and the fold rebuilds all but
similarity. Slicing the global weights is *not* the repair, for the reason recorded above in
§13.2's spirit: they reference the global `split_idx`, which lies in an early fold's future.
**Consequence, stated rather than buried: stacker-derived numbers carry that caveat until 4.1
fully closes.**

**4.3 is the instructive one.** The failure was never "the models are stale":
`check_feature_contract` reported *0 STALE, 12 UNKNOWN* because the two halves of the provenance
contract had never been introduced — the reader demanded nine keys the writer had never written,
four of which did not exist in any form. **A retrain could not have fixed it.** Every prior
instruction to "retrain to clear the VWAP failure", including one in an earlier draft of the
gate document itself, was unachievable as written.

The retrain must run **from a committed clean tree**, or `code_dirty` makes the resulting bundle
refuse itself.

**4.4 is no longer the hard stop.** It read FAIL for one session because no settlement head
existed; `cb1f4cf` built one and wired the trainer to call it, and it now clears in the same
sense as 4.3 — the code exists, and the gate goes green on the first clean-tree retrain.

**What still blocks a retrain is not on this table.** It is the round-aligned label: `build_sequences`
compares the horizon end to the DECISION-time price while the venue compares the round end to
the round ANCHOR, and the two disagree on **35.3%** of rounds at 3 minutes left — worst exactly
where late-round information is worth most. No retraining or recalibration repairs a backwards
label. See [`OPEN_DEFECTS.md`](active/OPEN_DEFECTS.md); the label logic is fixed, the
capture/backfill path is not.

### 13.5 What this changes, and what it does not

| question | answer |
|---|---|
| what was tested | ML lattice vs structural geometry (15m, 12m); a true log-odds offset with permuted and zero controls |
| what passed | the structural baseline `Φ(z)` — well calibrated, no parameters, hard to beat |
| what failed | the lattice (both lengths), the offset on Brier (both lengths), the offset AUC at 12m |
| what remains unknown | **`Φ(z)` versus the Polymarket ask.** Untested — needs recorded book snapshots, not a new model |
| any economic candidate? | **no** |
| any real-money authority? | **NONE.** The one wired head declares `AUTHORITY = "NONE"` and an import test enforces it |

**The binding constraint is unchanged.** Everything in §13 is measured on historical bars and is
elimination-grade by construction; `FORWARD_UNTOUCHED` still contains **0 rows**. §6.3 item 3
already named the market-prior residual as the only supported modelling direction — §13.2 is the
*outcome-relative* version of that experiment, and it fails before the market-relative version
was ever reached.

## 14. Polymarket L2 archive and the arbitrage post-mortems - `2026-08-07`

The first genuinely NEW information source examined since the direction work closed. Both items
are measured, and both are negative for the plan they were meant to support.

### 14.1 The Kaggle Polymarket archive cannot supervise BTC rounds

`docs/active/KAGGLE_POLYMARKET_ARCHIVE_INVENTORY_2026-08-07.md`. 39.7 GiB, 21 days
(`2026-03-06` -> `03-26`), 270,998,156 rows, read from the archive's own validation reports
without extracting it. The archive self-audits cleanly - 21/21 partitions, 0 missing dates,
0 outcome conflicts, per-file sha256 - which is not the same as being fit for purpose.

**Coverage, across all 24k+ daily markets:**

```text
rows with DEPTH (L2)          2.89%      per-day 2.10% - 4.90%
rows with TRADES              0.242%     per-day 0.032% - 0.596%
rows with a market outcome    13.21%
rows with an exact 15m target 73.7%
```

**BTC rounds located** by question text - 6,418 "Bitcoin Up or Down" markets (4,461 5m,
1,483 15m, 93 240m, 381 unparsed), 5,944 usable. Depth on those rounds is ~3x the archive
average and still thin:

| | 2026-03-12 | 2026-03-19 |
|---|---:|---:|
| rows with depth, BTC rounds | **7.64%** | **7.72%** |
| rows with depth, all markets | 2.60% | 4.48% |

15m rounds fare better than 5m (10.34% vs 6.73%), and ~99% of rounds carry at least one depth
observation - so event-conditioned book features are viable and a minute-by-minute depth panel
is not (~92% would be imputed).

**The two findings that decide it:**

```text
target over ALL markets        None 100,749  |  0: 17,177  |  1: 5,969
target over BITCOIN UP/DOWN    None   6,418  |  0:      0  |  1:     0
market_outcome in features_v3  0.00% of BTC rows

trade_count non-null          100.00%
trade_count > 0                 0.00%
```

23,146 other markets carry a resolved target. **Every one of the 6,418 BTC markets is NULL**,
despite `uma_status = "resolved"` on 6,247. And `trade_count` is present on every BTC row and
zero on every BTC row - a coverage check counting non-nulls reports 100% and means nothing,
which is this ledger's recurring defect class appearing in someone else's dataset. Corroborated
by the metadata: 5,703 of 6,418 BTC markets have zero volume, 6,247 have zero liquidity.

| head | verdict on this data |
|---|---|
| settlement residual alpha | **BLOCKED** - no outcome labels for BTC rounds |
| VPIN / CVD / trade intensity | **DEAD** - zero trades |
| depth and book shape, event-conditioned | viable |
| minute-by-minute depth panel | not viable |
| Binance -> Polymarket repricing lag | **viable, and the strongest remaining use** |

**It is a YES-side quote-and-book dataset, not a settlement dataset.** Labels would have to come
from `round_truth.py` against the Polymarket API. Licence is CC BY-NC 4.0 (non-commercial), and
the window is five months stale - fine for microstructure, useless as forward evidence.

### 14.2 Two arbitrage post-mortems: the failure was execution, not forecasting

`docs/active/POLYMARKET_ARB_EVIDENCE_2026-08-07.md`, retrieved from source.

**Correction first:** the strategy is **sportsbook odds against Polymarket** in **esports**
markets with 20-30c spreads - not `1 - (Ask_YES + Ask_NO)` package arbitrage within one market,
which is what a proposal drafted against these articles assumed. None of the magnitudes transfer
to BTC Up/Down.

The mechanisms do:

```text
arb legs (1,075 matched pairs)   +$8,293
unhedged directional residual    -$3,185     38% of arb profit destroyed by LEGGING RISK
fill rate                        37.4% -> 1.0% over four months
```

Every leg carried >=7% theoretical edge at detection and the arb legs **did** make money. The
author's own #1 loss cause was **adverse selection from stale quotes** - odds 7-30 minutes old
while lines moved >=5pp in 31-60% of games. That is the same defect class as scan-4 items
4.1/4.2/4.3, and it is why the book-freshness repair (source age vs receipt age) was prioritised
over the rest of the execution backlog.

He also records that he did not log orders from day one and was "flying blind" - the same
argument as the evidence-durability work here.

**Nothing in either item changes the evidence position.** No new economic candidate. What they
change is the ORDER: finish execution integrity before opening a new alpha lane.

### 14.3 What remains untested

| question | why it is still open |
|---|---|
| `Phi(z)` versus the Polymarket ask | the only comparison that decides tradeability, and still not run - it needs recorded book snapshots, not a new model |
| Binance -> Polymarket repricing lag | viable on this archive; needs a synchronised Binance join, and 21 days bounds it to discovery |
| everything gated on forward data | `FORWARD_UNTOUCHED` is still **0 rows** |

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
| `backend/audit/recorder_evidence_check.py` | a recorder that is wired and selftests but has never run |
| `backend/audit/freeze_oracle_release.py --verify` | a frozen champion artifact changing underneath the benchmark |
| `research/phase5d/admission.py` | a study powered in the wrong units passing a gate it never met |
| `backend/research/verify_prereg_hashes.py` | a preregistration edited after freezing, or one never registered |
| `backend/bc_forward_readiness_report.py` | a sealed protocol being peeked at while it collects |
| `SourceUnreadable` in the readiness report | an unreadable source printing as an honest empty one |
| `backend/test_round_state_causal_contract.py` | a training join that reads the bar containing its own decision |
| `round_state_panel.version_is_compatible` | a retrain producing artifacts serving silently refuses |
| `backend/datastore_identity.py --strict` | a correct query answered by the wrong database |
| `backend/test_backtest_ohlc_honesty.py` | a hit rate graded against a neutral band derived from fabricated highs and lows |
| `backend/test_no_snapshot_backcast.py` | today's order-flow value painted across every historical training row |
| `backend/test_artifact_manifest_contract.py` | a writer and a reader that both pass their own tests and cannot read each other |
| `backend/test_geometry_endpoint_wiring.py` | a zero-authority head acquiring authority through an import, checked by AST rather than grep |
| `backend/test_oof_serving_parity.py` | the stacker being trained on seats that differ from the ones it is served |

Each is negative-tested: it has been shown to *catch* a planted offender, not merely to pass.

**The uncomfortable point:** every retracted study passed CI, passed its own preregistered gates,
carried matched controls and day-block lower bounds — and was wrong anyway, because no check asked
whether the inputs existed when the decision was made.

## 6. Plan — rewritten `2026-08-02` against what is now measured

**The situation in one line:** every research lane the current data can answer has been
answered, all negatively. The binding constraint is no longer ideas — it is forward data.

### 6.1 What is actually blocking, in order

| # | blocker | who unblocks it | what it gates |
|---|---|---|---|
| 1 | **round recorders dark since `2026-07-25 15:00`** | operator: `start.bat` | *everything*. `FORWARD_UNTOUCHED` = 0 rows, so nothing can be promoted, ever, until this changes |
| 2 | **0 of 25 artifacts loadable** | a retrain via `train_heads.py` | every model-backed strategy is `UNAVAILABLE`, not unprofitable |
| 3 | **`post_entry_crossing_outcomes` does not exist** | a recorder nobody has written | **Protocol B specifically.** Its readiness reports `NOT_WIRED`, and it will keep doing so through eight weeks of otherwise-healthy collection |
| 4 | 8 forward weeks + 1,000 resolved rounds | time, once (1) is fixed | the promotion gate |

Nothing below matters until (1). A study run today spends evaluation data on a window that has
already answered twice, which is how the retracted results were produced.

**(3) is new and easy to miss.** Positions and action arms have recorders; post-entry crossings
do not. Restarting collection fixes (1) and does nothing for (3) — B would accrue eight weeks of
open positions and still have zero crossing labels to score. Required table, in
`open_position_actions.duckdb`:

```
post_entry_crossing_outcomes(
  position_id, round_id, position_snapshot_id, crossing_ts, crossing_direction,
  is_final_crossing, reverted_5s, reverted_15s, reverted_30s, reverted_60s,
  settlement_resolved, label_version)
```

`backend/bc_forward_readiness_report.py` names this exact schema in its refusal, so the gap
announces itself on every run rather than waiting to be discovered at scoring time.

### 6.1a Open ideas, explicitly NOT built - `2026-08-03`

Recorded so they are a backlog rather than a rediscovery. None may be run while
`HISTORICAL_EXPANSION_FROZEN` holds; each needs its own preregistration and admission check.

| idea | why it is not built | what would admit it |
|---|---|---|
| Phase 5C's other 8 of 15 recommended tests | triaged out in §10.3 on power, not on interest | forward days lifting the ~25-point MDE |
| Phase 5D-B tests 166–172 | the stopping rule fired: 157 and 164 both returned negative, so the chain halts | a positive forward result from B, C or D |
| Protocol A2 (new feature families) | A is retired for *current* features only | genuinely new inputs — model revisions, settlement-source basis, paired L2 |
| Binance maker conversion | the Polymarket maker question (D) is sealed and unmet; running the Binance analogue first would answer the easier venue and generalise it | D scored, or an independent Binance surplus measurement |
| Depth20 L2 microstructure studies | `binance_l2_recorder.py` has NEVER_RAN — zero rows | the recorder running for enough independent days |

The pattern in every row: the blocker is evidence, not ideas. That has been true since
`2026-08-02` and nothing since has changed it.

### 6.2 The retrain must write PROVENANCE manifests

Measured (§4.1): the integrity sidecar satisfies the manifest gate and does **not** unblock
serving. `backend/train_heads.py` is the only place that writes provenance correctly, and it
does so in the *orchestrator*. **Launch the retrain through `train_heads.py`** — running
trainers individually produces artifacts that still cannot be served, and the run will look
successful.

Acceptance: `python backend/test_artifact_serviceability.py` reports a serviceable count above
zero. It ratchets, so the number can rise and never silently fall.

### 6.3 What to collect, and what NOT to build

Once recorders are up, the ledger already records every decision causally. Collect. Do not
model.

**Do not build, and why — each is closed by measurement, not opinion:**

| do not build | closed by |
|---|---|
| another direction classifier | §4.5, §4.10 — direction is below 0.5 at 60/120m and the market beats every vintage |
| a Binance 15m strategy | §4.7 — the ceiling's median is −2.63 bps against a 12 bps round trip |
| a third exit rule on the July window | §4.8, §4.9 — two pre-declared rules already failed there |
| a magnitude regressor on these features | §4.9 — rank correlation with realised value is −0.02 |
| complete-set lock scanning as a strategy | §4.6 — best action on 3 of 50,272 checkpoints |

**Worth building once forward rows exist**, in this order:

1. **Automatic outcome append** to the ledger — settlement, fills, markouts. Without it the
   forward rows are decisions with no results attached.
2. **Ledger coverage for every strategy**, not just the fair-value benchmark.
3. **The market-prior residual**, framed as `logit(p) = logit(ask) + f(x)`. This is the only
   modelling direction the evidence supports: the market is the incumbent and a model must
   predict its *error*, not the outcome.
4. Re-run the exit-value question on forward data. The ceiling is real; the failure was
   conversion on a spent window.

### 6.4 The gate that has not moved

```
>=8 uninterrupted forward weeks     >=1,000 independently resolved rounds
positive day/week lower bound       positive under 2x cost stress
beats a matched-count control       beats the market baseline
no timestamp violations             no artifact-identity failures
no single day >35% of profit        FORWARD_UNTOUCHED evidence only
```

## 7. Capital

```
$100,000 live : HARD NO
$5,000 live   : HARD NO
$500 canary   : not yet
$0 shadow     : correct mode
```

The next milestone is not profit, and it is not a model. It is **one forward economic result
from `FORWARD_UNTOUCHED` rows** — a class that currently contains zero observations and will
contain zero until the recorders restart.

Everything measured on the existing 21-day window is elimination-grade by construction. It has
eliminated a great deal, which is worth something: the map is now specific rather than vague.
It cannot promote anything, and no amount of further analysis on it will.

## 8. Reproduce

```bash
python backend/run_ci_locally.py                              # 96 gating steps, the only real CI
python research/run_all_sequence.py --selftest                # every study is covered
python backend/test_artifact_serviceability.py                # can serving load anything yet?
python backend/audit/freeze_oracle_release.py --verify        # has the frozen champion drifted?
python backend/audit/build_oracle_data_manifest.py            # what did the recorders capture?
```

Rebuild the research substrate, in dependency order:

```bash
python backend/research_data/checkpoint_builder.py            # 56,467 causal checkpoints
python backend/research_data/path_label_builder.py            # 32 labels per checkpoint
python backend/research_data/action_value_builder.py          # Polymarket action ceilings
python backend/research_data/binance_action_value_builder.py  # Binance action ceilings
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
