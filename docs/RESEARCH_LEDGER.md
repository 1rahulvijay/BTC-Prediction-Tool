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
