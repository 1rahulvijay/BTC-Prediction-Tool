# Validation sweep — 2026-08-04 (second pass)

Covers everything built after `VALIDATION_SWEEP_2026-08-04.md`: the crossing calibration study,
the side-specific vacuum study, the Bybit L2 grid, and the CI-job gap fix.

Head `15c770e`. **Implemented, tested, validated and wired — with two exceptions that are
governance decisions, listed in §5 and not suppressed.**

---

## 1. Gate results, re-run for this sweep

| gate | result |
|---|---|
| `run_ci_locally.py` (both jobs) | **144 OK / 2 FAIL** — the 2 are §5 |
| `verify_prereg_hashes.py` | **25/25 intact** |
| `run_all_sequence.py --selftest` | versioned=31 auxiliary=3 **frontier=32 uncovered=0** |
| repo-wide tautology scan | **0** live (3 hits are comments documenting past fixes) |
| **mutation suite, all three new modules** | **15/15 CAUGHT**, sources restored byte-identical |
| `test_artifact_serviceability.py` | PASS (at floor) |

Per-module selftests:

```
crossing_calibration_v1     22 checks
bybit_l2/grid               24 checks
side_specific_vacuum_v1     22 checks
recorder_health             18 checks
test_round_state_causal_contract  PASS
datastore_identity           7 checks
```

---

## 2. Mutation testing — the actual validation

A passing selftest proves nothing if the checks cannot fail. Every load-bearing property was
broken deliberately and the check required to fail.

```
crossing_calibration_v1
  tie-break follows row order again                        CAUGHT
  decomposition hides the base-rate shift                  CAUGHT
  decomposition claims the shift explains nothing          CAUGHT
  ECE always returns zero                                  CAUGHT
  verdict ignores an AUC shift                             CAUGHT
  cosmetic calibration promoted to material                CAUGHT

side_specific_vacuum_v1
  VACUUM_QUIET stops guarding                              CAUGHT
  collapse rule accepts any decline                        CAUGHT
  refractory removed (overlapping episodes)                CAUGHT
  ASYM drops the sign (wrong-way reads as a win)           CAUGHT
  biased null floor no longer voids                        CAUGHT
  sub-cost reported as cost-clearing                       CAUGHT
  excursion looks BACKWARD (leaks the answer)              CAUGHT

bybit_l2/grid
  depth_series truncates instead of refusing               CAUGHT
  ladder anchors on the row's own mid                      CAUGHT
```

`depth_series truncates instead of refusing` **SURVIVED on the first pass**. The agreement test
only used anchors within $4 of the mid, so the fast path never reached the out-of-cache branch —
while `depth_series` is what the study actually calls. A dedicated check was added and the mutant
is now caught. This is the second time in this line of work that a check covered the scalar
reference and not the vectorised path that runs in production.

---

## 3. Wiring — verified, not assumed

| artefact | seal | FRONTIER | invariants.yml | notes |
|---|---|---|---|---|
| `crossing_calibration_v1.py` | yes | yes | yes | — |
| `side_specific_vacuum_v1.py` | yes | yes | yes | — |
| `side_specific_vacuum_v1_run.py` | n/a | NON_STUDY | via the study | scoring half |
| `bybit_l2/grid.py` | n/a | n/a (subdir, infrastructure) | yes | shared cache |

Both studies pin their scored-once numbers in code (`FIRST_SCORING`) and **exit non-zero** if a
rerun moves them. "Scored once" is a control now, not a promise.

### The CI job that was gated by nothing

`invariants.yml` has two jobs. Actions has never executed a step (billing lock) and
`run_ci_locally.py` parsed `job="invariants"` only — so **27 commands unique to `startbat` had no
gate at all**, including `test_round_state_causal_contract` (which pins the P0-01 version contract
and the P0-02 same-minute feature leak). Fixed via `every_step()`; **123 → 152 gated commands**.
Details in `GAP_UNGATED_CI_JOB_2026-08-04.md`.

### Data dependence — checked, and consistent with convention

`data/bybit_l2`, `data/bybit_trades` and `data/bybit_l2_grid` are all gitignored. The vacuum study
exits 1 when the grid is absent — **identical to the existing `bybit_l2_maker_v1`**, and it does
not affect CI, which runs `run_all_sequence.py --selftest`, never the full suite. Not a new
defect; stated so it is not mistaken for one.

---

## 4. Defects found and fixed in this sweep

1. **My frozen calibration protocol specified the wrong weighting.** Round-equal ECE measured a
   base-rate shift (`1/mean(n)=0.370` vs `mean(1/n)=0.550`), not the head — 0.1563 of a 0.1563
   ECE. Not retro-fitted; verdicts stand as frozen, and the decomposition is now enforced.
2. **Equal-count binning used a stable `argsort`**, so tied scores binned along row order.
   LightGBM ties often; isotonic ties by construction. Fixed-seed random tie-break.
3. **Unsigned-hazard baseline compared unlike statistics** — max excursion after a vacuum against
   close-to-close unconditionally, inflating the lift to 6.1x. Corrected to **3.3x**.
4. **An `or True` I wrote in the grid selftest** — the same vacuous pattern flagged earlier in
   this work. Replaced with real assertions.
5. **`depth_in_band` refused an anchor above the mid**, which is the normal case after a price
   drop; it now clips at the mid and returns a true zero when the band is wholly past it.
6. **Two selftest fixtures were wrong, not the code** — a price move placed outside the lookback
   window, and a permanently-halved depth making later quiet windows legitimate candidates. Both
   corrected so the guard is genuinely exercised.

---

## 5. NOT done — the two failing gates, and why they are not mine to close

```
[invariants] Oracle release freeze    5 artifacts DRIFTED from ORACLE_2026_07_04
[startbat]   check_feature_contract   12 UNKNOWN artifacts, VWAP v1->v2 skew
```

- The freeze needs a **new release id**. Overwriting it in place would erase the only record of
  what produced the live sample. That is a governance decision.
- `check_feature_contract` needs a **challenger retrain bundle**. The script deliberately does not
  retrain; promotion stays a gated act.

Neither was caused by this work. The second was already true and is newly *visible* because the
gate that runs it had never run.

---

## 6. NOT done — scope explicitly left open

- **Ideas #2–#5 of the highest-priority five are not built.** Only
  `SIDE_SPECIFIC_VACUUM_AND_SIGNED_BURST_V1` is complete. The grid now makes #2, #3, #6, #12 and
  #13 cheap, but they do not exist.
- **6 days of Bybit archive, not "several weeks".** Declared in the protocol before scoring. It
  caps day-block bootstraps at 6 blocks. Extending it is a multi-GB download, not started.
- **0/5 recorders ADVANCING.** `binance_l2` NEVER_RAN; the other four STALLED — round recorders
  since 2026-07-04, `multi_venue` since 2026-07-29. Every sealed forward protocol still waits on
  collection. Starting them is an operator action.
- **0/25 legacy artifacts serviceable** (at floor, ratchet holds).
- **The full research suite was not executed** — 32 studies over multi-GB archives. Every study's
  selftest ran in CI; scored results stand on their original single runs.
- **The frontend was not examined.**

---

## 7. Standing state

```
local CI               144 OK / 2 FAIL (both governance, both listed above)
protocol seals         25/25
research coverage      frontier=32, uncovered=0
tautological checks    0
mutations caught       15/15
studies scored once    2 new, both pinned in code against re-scoring
recorders              0/5 ADVANCING
artifacts              0/25 serviceable
capital                real orders disabled, paper/shadow only, 0 promotable strategies
```

The binding constraint has not moved: **cost on the research side, collection on the
infrastructure side.** The vacuum study added a third data point to the first — direction exists
at $10 and is gone by $70 — and moved nothing on the second.
