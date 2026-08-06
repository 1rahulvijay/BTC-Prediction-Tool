# Core-app audit validation — `2026-08-06`

Three external scans of the core application, 56 claims total. Every claim was **checked against
source before anything was changed**, because this repository's history is that roughly one
audit claim in eight is wrong or right-for-the-wrong-reason.

This document records **what was validated**, not what was asserted. A claim listed as OPEN has
been confirmed real and deliberately not started; a claim listed as UNVERIFIED has not been
checked and must not be quoted as a finding.

---

## Scoreboard

```text
claims received            56
confirmed from source      23
corrected (real, wrong mechanism)   3
not verifiable statically   1
unverified                 29
fixed                      15
```

**No claim was outright false this time** — a first for an audit of this repository. Three
needed their *mechanism* corrected, which is recorded below rather than quietly absorbed.

---

## Fixed, with a CI gate

| # | defect | gate |
|---|---|---|
| 1.1 | production preflight passed `mode="production"` — not a valid `--mode`, so startup was impossible | `test_core_decision_contracts.py` |
| 1.2 | `bind_release()` cleared three attributes the class does not have | `test_calibration_release_binding.py` |
| 1.3 | `is_admissible_for()` had **zero** production callers | `test_core_decision_contracts.py` |
| 1.5 | training and live grading used different neutral bands (2.4x) | `test_core_decision_contracts.py` |
| 1.9 | promotion floors admitted worse than a coin flip and worse than uniform | `test_core_decision_contracts.py` |
| 1.10 | a surrogate RandomForest's score gated the real ensemble | `test_core_decision_contracts.py` |
| 2.8 | the promotion holdout was not purged | `test_core_decision_contracts.py` |
| 2.14 | the server bypassed `bind_release()` entirely | `test_core_decision_contracts.py` |
| 2.17 | a terminal verification state was never persisted | `test_evidence_durability_and_provenance.py` |
| 2.21 | the artifact code hash omitted `target_contract.py`, `regime.py`, `calibration.py` | `test_evidence_durability_and_provenance.py` |
| 2.29 | specialist loaders pinned their flag before the load, so one transient failure disabled a head for the process lifetime | `test_evidence_durability_and_provenance.py` |
| 3.1 | the simulator opened positions on a `WEAK_LEAN` the decision layer refused | `test_simulator_entry_authority.py` |
| 3.2 | a first-touch probability drove an endpoint EV formula | `test_simulator_entry_authority.py` |
| 3.3 | rejection left `actionable=True`, `positionSize`, `stopLoss` behind | `test_simulator_entry_authority.py` |
| 3.5B | the server dropped `targetContract` before the paper engine | `test_simulator_entry_authority.py` |

Plus, from earlier the same day: the training-window resolver
(`test_history_days_single_resolver.py`) and the backtest OHLC honesty regression.

---

## The pattern, stated once

Fourteen of the fifteen are the same shape:

> **A safety invariant exists, is correct, and the executed path does not consult it.**

`is_admissible_for` was right and unused. `bind_release` was right and uncalled. The decision
verdict was computed *before* the simulator ran and never read. `compute_adaptive_threshold`
already returned the training-consistent band and serving recomputed its own.

The corollary is the useful part: **finding the guard is not the fix.** Three of these had a
passing test beside them — one verified an attribute the subject does not have.

---

## Corrections to the audits

Recorded because a wrong mechanism produces a wrong fix.

| claim | as stated | as measured |
|---|---|---|
| 1.4 | implies `prob_win = confidence` may be a units bug | Not a units bug. Stored confidence is 0–1 (min 0.018, max 0.734). The defect is purely the contract mismatch. |
| 1.13 | "the execution router receives it but does not use it" | `fill_prob` **is** passed to `simulate_execution_cost` and does affect slippage. What it does not do is gate or size the position. |
| 2.14 | framed as a consequence of the `bind_release` bug | Independent and worse: `bind_release` had **no production caller at all**, so fixing it alone would have changed nothing. |

---

## Confirmed real, deliberately OPEN

These are not oversights. Each is architecture rather than an edit, and half-doing them is how
this backlog was created.

| # | defect | why not started |
|---|---|---|
| **2.1** | **the artifact hashes a dataset the model did not train on** | **The keystone.** `current_training_identity` reads the research-matrix manifest (86,400 rows, hash `281657b2…`, parquet written 04:05) while `train()` receives in-memory `X`/`Y` from freshly fetched klines. Same row *count*, different data. Needs an executed-input snapshot hashed before feature construction and passed **into** `train()`. |
| 1.7 | `predictions_{5,15}m` lack `target_contract`, `release_id`, `resolution_basis` | Gates 1.4, 1.6 and calibration restoration. Confirmed by querying the schema. |
| 1.6 | restart rebuilds expert weights from endpoint sign | Cannot be fixed before 1.7: there is no persisted contract outcome to restore *from*. |
| 1.4 | first-touch probability drives endpoint EV | Now *refused* (3.2) rather than substituted. Restoring the capability needs an admissible endpoint head — downstream of gate 4.4. |
| 1.11 / 2.11 | bar-open resolution timestamps | Tracked as P0-4; the remedy is an ingestion-schema change, already documented. |
| 2.2 | training snapshots candles but not order flow / derivatives / sentiment | Part of the same executed-snapshot work as 2.1. |
| 3.11 / 3.12 | multiple action vocabularies; four sizing systems | One enum and one risk engine. Cross-cutting. |
| 3.13 / 3.14 / 3.15 | A/B isolation, atomic release, `DecisionEnvelope` threading | Depend on 2.1 landing first. |

### Consequences that were accepted, not hidden

Two fixes **removed capability on purpose**. Both are logged and counted rather than silent:

- **The legacy simulator now opens nothing.** The only probability available is first-touch, and
  it is inadmissible for directional EV. Refusals are counted by reason in `blocked_entries`,
  because "stopped trading" and "refusing every lean for a named reason" look identical
  otherwise.
- **Live calibration falls back to raw confidence** while `contract_provenance` is UNRECORDED.
  That is the honest state and *not* a free win — raw confidence was measured anti-correlated
  with success at 5m+. What restores it is 1.7, not removing the check.

---

## UNVERIFIED — none remain

**Closed `2026-08-06`.** Every claim listed here has since been read against source; the verdicts
are in [`OPEN_CLAIMS_INVESTIGATION_2026-08-06.md`](OPEN_CLAIMS_INVESTIGATION_2026-08-06.md).

```text
investigated        56 of 56
CONFIRMED           40
CONFIRMED-ADJUSTED   3
SHAPE-CONFIRMED      4
NOT ESTABLISHED      1   (2.16 revision-ledger timestamps - needs a runtime trace)
```

That investigation also revised the fix order: 2.23 (GPU fits at import) turns out to precede
every safety check, and 2.4 (no canonical kline schema) is the shared root of 2.3, 2.5 and P0-4,
which were being tracked as three separate defects.

## Status

```text
local CI                   161 steps, 1 FAIL
the one failure            check_feature_contract - no artifact trained under the repaired
                           provenance contract; clears on the first clean-tree retrain
promotable strategies      0
real-money authority       NONE
retrain                    NOT YET - see the order below
```

**Retrain order, unchanged from the scans' own recommendation and now evidence-backed:**

```text
2.1 executed-training snapshot   ->  nothing a manifest says is trustworthy until this lands
1.7 prediction contract columns  ->  unblocks 1.6, 1.4 and calibration
1.6 restart parity
2.14/3.14 atomic release
then retrain, then baseline-relative evaluation
```
