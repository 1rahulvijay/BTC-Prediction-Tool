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
