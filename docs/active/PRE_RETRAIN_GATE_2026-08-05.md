# PRE-RETRAIN GATE — 2026-08-05

## Verdict

```text
RETRAIN: BLOCKED
CHALLENGER BUNDLE CREATED: NO
PROMOTION VERDICT: REJECTED (no challenger exists to evaluate)
REAL-MONEY AUTHORITY: NONE
```

Four of the specification's own hard-stop conditions are present. Per its rule — *"Do not
retrain if any of these fail"* — no challenger was trained. Training now would bake the
unfixed pipeline into a fresh artifact and require a second retrain to undo.

## Phase 1 — repository state

```text
branch            master
HEAD              e3ced8445645d2a9acb243d5ab0957067c50ea84  (at start)
upstream          origin/master, 0 ahead / 0 behind
dirty files       0
python            3.13.2 Windows
numpy 2.1.0   pandas 2.2.3   sklearn 1.5.2   lightgbm 4.6.0
xgboost 2.1.1 catboost 1.2.10 torch 2.12.0+cpu duckdb 1.4.4
local CI          169 OK / 142 checks, 1 FAIL (check_feature_contract, VWAP)
hosted CI         NOT VERIFIED - no hosted status check was inspected in this session
```

`local CI` and `hosted GitHub Actions` are different things and only the first was run.

## Phase 5 — gate table

| # | Check | Status | Evidence |
|---|---|---|---|
| 4.3 | VWAP / feature-contract parity | **FAIL** | `check_feature_contract.py` exits non-zero; 12 artifacts UNKNOWN |
| 4.2 | Historical snapshot broadcasting | **FAIL** | `features.py` still contains `np.full(n, float(snapshot_val or 0.0))` |
| 4.4 | Settlement head exists and is trained | **FAIL** | no settlement artifact; `return_settlement_labels=True` appears 0 times in `server.py` |
| 4.1 | OOF/serving parity — class-presence augmentation | **FAIL** | production augments thin classes; OOF folds do not |
| 4.1 | OOF/serving parity — TCN epoch budget | **FAIL** | OOF fits the deep seat at `epochs*0.5` |
| 4.1 | OOF/serving parity — dynamic-weight skill metric | **FAIL** | raw multiclass accuracy still feeds the blend |
| 4.1 | OOF/serving parity — calibration wrapper | PASS | fold wraps in the served calibrator; drops the seat if too small |
| 4.1 | OOF/serving parity — sample weights | PASS | fold-local recency/class/ambiguity, not sliced globals |
| 4.1 | OOF/serving parity — purge gap | PASS | required gap enforced; refuses rather than shrinking |
| 4.6 | Model-bundle isolation (regime engine) | PASS | `_install_hmm_state`; training fits a candidate engine |
| 4.5 | Event-time / 1s label integrity | **BLOCKED** | HF recorder has ~2.5 min of data; not enough to build 1s labels |
| 4.7 | Exact artifact identity per prediction | PARTIAL | `model_version` recorded; per-head hashes are not |
| 4.8 | Canonical datastore | **NOT RUN** | multiple `analytics.duckdb` copies exist; not resolved this session |
| 2.1 | Geometry head target contract | PASS | renamed `geometry_endpoint_head`, `ENDPOINT_ABOVE_ANCHOR` |
| 2.2 | Sigma units / stochastic assumption pinned | PASS | asserted numerically (`sqrt(60)` test) |
| 2.3 | Sigma causality | PASS | forming-bar guard repaired; regression test added |
| 2.5 | Wiring seam | PASS | `test_geometry_endpoint_wiring.py` |
| 2.6 | No-authority boundary | PASS | frozen dataclass + AST import test |
| 3 | Offset experiment reproducible | PASS | `conditional_offset_v2.py` + JSON artifact + result doc |

## Phase 2 — geometry head, corrections applied

**Renamed** `conditional_path_head` → `geometry_endpoint_head`. The audit is right that the old
name was hazardous in a repository whose central defect class is first-touch-vs-endpoint
confusion. It emits `TARGET_CONTRACT = "ENDPOINT_ABOVE_ANCHOR"`; several checkpoint
probabilities do not make it a path forecast, and the cells are not jointly consistent as a
trajectory.

**Units pinned and asserted numerically.** A per-minute sigma multiplied by `sqrt(seconds)`
would be a silent `sqrt(60)` error making every probability ~7.7× too confident. The test
measures that ratio at 7.746 rather than trusting the pairing.

**A real causality bug was found and fixed.** The forming-bar guard was:

```python
if closed and closed is kl:      # a comprehension ALWAYS allocates -> never True
    closed = kl[:-1]
```

Dead code. With a feed that omits `is_closed`, the forming bar entered sigma. The rule is now
positive — a bar counts only if it says it is closed, or a later bar proves it closed.

**The grep boundary was replaced.** A frozen `GeometryEndpointEstimate` plus an AST import test
proving no decision, sizing or order module imports the head. The audit is right that `score`,
`edge`, `rank`, `eligible` would have walked straight past a keyword search.

**Checkpoints now carry absolute `checkpoint_at_ms`** — "the 4m checkpoint" was ambiguous
between minute four of the round, four minutes remaining, and four minutes from now.

## One audit statement corrected

> "The true log-odds offset structurally cannot damage the baseline."

That was mine, and it is wrong. `logit(p_base) + f(X)` cannot *omit* the baseline, but a large
enough `f(X)` can overturn it entirely — and the measured Brier deterioration is evidence that
it did. The defensible statement is: *the constrained offset retained geometry as an input and
learned out-of-sample corrections that were net harmful.* Corrected in the module docstring.

## What was NOT done

Phases 6–10 were not started, because Phase 5 blocks them:

```text
6  freeze the retrain specification      NOT STARTED (gate failed)
7  clean challenger retrain              NOT STARTED (gate failed)
8  challenger evaluation                 NOT STARTED (no challenger)
9  promotion gates                       REJECTED by default
10 challenger deliverables               N/A
```

The geometry-vs-market recorder (their "correct next commit") is also not built. It needs
contemporaneous book snapshots, and the HF recorder has ~2.5 minutes of lifetime data.

## Exact next code action, ranked

1. **`features.py` snapshot broadcasting** — replace the backward broadcast with explicit
   missingness. Highest severity: it is historical leakage, and it changes feature semantics,
   so it must land *before* the retrain, not after.
2. **Settlement head** — train on endpoint labels and expose the contract. Without it the
   economic consumers stay correctly refused, but permanently.
3. **Remaining OOF parity** — class-presence augmentation, TCN epoch budget, and replacing raw
   accuracy in the dynamic-weight blend.
4. **VWAP contract** — resolve the semantic difference, then retrain. Not the reverse.
5. **Canonical datastore** — resolve the competing stores before any evaluation joins them.

Only after 1–5 does the retrain specification get frozen.
