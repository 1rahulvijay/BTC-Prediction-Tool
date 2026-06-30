# DuckDB Metrics Analysis — 2026-06-21

Window **2026-06-12 → 2026-06-21**. Horizons: **5m & 15m**. Accuracy = correct/all directional calls; Precision(UP/DOWN) = per-class hit; Actionable accuracy = accuracy on app-flagged actionable rows; NEUTRAL/ABSTAIN counted separately. Ensemble directional correctness is from signed `actual_move`; the tracker and individual models use their pure-directional `hit`.

## A. Ensemble action log — `predictions_5m` / `predictions_15m`

### A1 — Overall by horizon
| hz | total | resolved | dir calls | dir acc % | neutral | actionable | act acc % | avg conv |
|---|---|---|---|---|---|---|---|---|
| 5m | 1381 | 1380 | 714 | 49.0 | 667 | 310 | 50.2 | 72.4 |
| 15m | 471 | 470 | 352 | 47.2 | 119 | 182 | 44.5 | 72.7 |

### A2 — Directional precision per class (UP vs DOWN)
| hz | predicted | support | precision % |
|---|---|---|---|
| 5m | UP | 425 | 48.0 |
| 5m | DOWN | 289 | 50.5 |
| 15m | UP | 173 | 46.8 |
| 15m | DOWN | 179 | 47.5 |

### A3 — By day × horizon (directional accuracy)
| hz | day | n | dir calls | dir acc % | actionable |
|---|---|---|---|---|---|
| 5m | 2026-06-12 | 190 | 185 | 50.3 | 76 |
| 5m | 2026-06-13 | 164 | 76 | 50.0 | 39 |
| 5m | 2026-06-14 | 198 | 108 | 38.9 | 43 |
| 5m | 2026-06-15 | 178 | 127 | 51.2 | 70 |
| 5m | 2026-06-16 | 176 | 121 | 49.6 | 49 |
| 5m | 2026-06-18 | 28 | 13 | 69.2 | 10 |
| 5m | 2026-06-19 | 231 | 62 | 50.0 | 15 |
| 5m | 2026-06-20 | 126 | 21 | 52.4 | 8 |
| 5m | 2026-06-21 | 90 | 1 | 100.0 | 0 |
| 15m | 2026-06-12 | 64 | 63 | 41.3 | 47 |
| 15m | 2026-06-13 | 56 | 25 | 64.0 | 13 |
| 15m | 2026-06-14 | 68 | 55 | 34.5 | 28 |
| 15m | 2026-06-15 | 60 | 58 | 46.6 | 39 |
| 15m | 2026-06-16 | 60 | 60 | 41.7 | 29 |
| 15m | 2026-06-18 | 11 | 10 | 40.0 | 4 |
| 15m | 2026-06-19 | 79 | 61 | 57.4 | 15 |
| 15m | 2026-06-20 | 43 | 17 | 76.5 | 5 |
| 15m | 2026-06-21 | 30 | 3 | 33.3 | 2 |

### A4 — By model_version × horizon (≥20 resolved directional calls)
| hz | version | bundled | n | dir calls | dir acc % | act acc % |
|---|---|---|---|---|---|---|
| 5m | 1781242921 | 06-12 07:42 | 73 | 73 | 54.8 | 48.3 |
| 5m | 1781954192 | 06-20 13:16 | 216 | 22 | 54.5 | 75.0 |
| 5m | 1781244709 | 06-12 08:11 | 58 | 57 | 52.6 | 45.2 |
| 5m | 1781846207 | 06-19 07:16 | 140 | 50 | 52.0 | 64.3 |
| 5m | 1781512558 | 06-15 10:35 | 65 | 54 | 51.9 | 62.9 |
| 5m | 1781476488 | 06-15 00:34 | 107 | 67 | 50.7 | 58.8 |
| 5m | 1781599217 | 06-16 10:40 | 143 | 113 | 48.7 | 47.7 |
| 5m | 1781316534 | 06-13 04:08 | 44 | 20 | 45.0 | 27.3 |
| 5m | 1781282389 | 06-12 18:39 | 55 | 52 | 44.2 | 46.7 |
| 5m | 1781368226 | 06-13 18:30 | 102 | 60 | 43.3 | 42.2 |
| 5m | 1781452434 | 06-14 17:53 | 79 | 33 | 36.4 | 37.5 |
| 15m | 1781954192 | 06-20 13:16 | 73 | 20 | 70.0 | 71.4 |
| 15m | 1781512558 | 06-15 10:35 | 22 | 21 | 52.4 | 56.2 |
| 15m | 1781846207 | 06-19 07:16 | 48 | 44 | 52.3 | 58.3 |
| 15m | 1781452434 | 06-14 17:53 | 27 | 21 | 47.6 | 41.7 |
| 15m | 1781599217 | 06-16 10:40 | 48 | 48 | 43.8 | 37.5 |
| 15m | 1781476488 | 06-15 00:34 | 36 | 35 | 42.9 | 42.9 |
| 15m | 1781242921 | 06-12 07:42 | 24 | 24 | 41.7 | 30.0 |
| 15m | 1781244709 | 06-12 08:11 | 20 | 20 | 40.0 | 33.3 |
| 15m | 1781368226 | 06-13 18:30 | 35 | 29 | 34.5 | 35.3 |

_Per-version samples are small (frequent retrains) — read as spread, not ranking._

## B. Price-to-Beat tracker — `price_to_beat` (pure UP/DOWN hit)

### B1 — Overall by horizon
| hz | total | resolved | dir calls | acc % | UP prec % | DOWN prec % | neutral |
|---|---|---|---|---|---|---|---|
| 5m | 1825 | 1824 | 1358 | 50.2 | 51.7 | 48.3 | 466 |
| 15m | 612 | 611 | 440 | 50.5 | 48.9 | 52.2 | 171 |

### B2 — By day × horizon
| hz | day | n | dir calls | acc % |
|---|---|---|---|---|
| 5m | 2026-06-12 | 253 | 186 | 57.0 |
| 5m | 2026-06-13 | 259 | 157 | 52.2 |
| 5m | 2026-06-14 | 271 | 186 | 48.4 |
| 5m | 2026-06-15 | 256 | 177 | 45.2 |
| 5m | 2026-06-16 | 176 | 174 | 46.0 |
| 5m | 2026-06-18 | 153 | 28 | 57.1 |
| 5m | 2026-06-19 | 236 | 232 | 52.6 |
| 5m | 2026-06-20 | 129 | 127 | 45.7 |
| 5m | 2026-06-21 | 92 | 91 | 52.7 |
| 15m | 2026-06-12 | 86 | 61 | 45.9 |
| 15m | 2026-06-13 | 87 | 49 | 36.7 |
| 15m | 2026-06-14 | 90 | 58 | 58.6 |
| 15m | 2026-06-15 | 86 | 58 | 48.3 |
| 15m | 2026-06-16 | 59 | 57 | 42.1 |
| 15m | 2026-06-18 | 52 | 9 | 55.6 |
| 15m | 2026-06-19 | 78 | 76 | 53.9 |
| 15m | 2026-06-20 | 43 | 42 | 66.7 |
| 15m | 2026-06-21 | 31 | 30 | 53.3 |

### B3 — By regime & confluence grade (5m+15m pooled, directional)

**by regime:**
| regime | dir calls | acc % |
|---|---|---|
| LOW_VOLATILITY | 38 | 63.2 |
| RANGE | 201 | 59.2 |
| TRENDING_DOWN | 162 | 50.6 |
| UNKNOWN | 1163 | 49.0 |
| TRENDING_UP | 191 | 46.6 |
| HIGH_VOLATILITY | 43 | 46.5 |

**by confluence_grade:**
| confluence_grade | dir calls | acc % |
|---|---|---|
| B | 278 | 56.1 |
| C | 1081 | 50.0 |
| A | 439 | 47.4 |

**by lean_source:**
| lean_source | dir calls | acc % |
|---|---|---|
| model | 1038 | 50.6 |
| fallback | 760 | 49.9 |

## C. Individual models — `model_predictions` (8 base models)

### C1 — Per-model accuracy & precision at 5m / 15m
| hz | model | dir calls | acc % | UP prec % | DOWN prec % | abstain % |
|---|---|---|---|---|---|---|
| 5m | rf | 279 | 52.3 | 52.1 | 52.6 | 60.5 |
| 5m | lgb | 729 | 44.3 | 50.0 | 40.2 | 46.1 |
| 5m | xgb | 739 | 44.1 | 49.4 | 41.1 | 45.3 |
| 5m | cat | 777 | 43.2 | 49.1 | 40.9 | 42.7 |
| 5m | histgb | 599 | 43.2 | 44.7 | 41.8 | 55.7 |
| 5m | lr | 756 | 43.1 | 43.4 | 42.9 | 44.1 |
| 5m | dl | 764 | 43.1 | 43.4 | 42.7 | 43.5 |
| 5m | sgd | 70 | 17.1 | 11.4 | 22.9 | 62.6 |
| 15m | rf | 115 | 55.7 | 48.9 | 60.0 | 51.3 |
| 15m | cat | 336 | 50.3 | 55.9 | 47.6 | 24.5 |
| 15m | dl | 338 | 49.4 | 49.2 | 49.5 | 23.9 |
| 15m | histgb | 412 | 47.8 | 47.4 | 48.1 | 7.2 |
| 15m | xgb | 320 | 45.6 | 48.3 | 43.4 | 27.9 |
| 15m | lr | 286 | 44.4 | 44.3 | 44.5 | 35.6 |
| 15m | lgb | 325 | 43.1 | 44.4 | 41.8 | 26.8 |
| 15m | sgd | 60 | 41.7 | 100.0 | 38.6 | 1.6 |

### C2 — Model ranking (directional accuracy, 5m & 15m side by side)
| model | 5m acc % | 5m n | 15m acc % | 15m n |
|---|---|---|---|---|
| rf | 52.3 | 279 | 55.7 | 115 |
| lgb | 44.3 | 729 | 43.1 | 325 |
| xgb | 44.1 | 739 | 45.6 | 320 |
| cat | 43.2 | 777 | 50.3 | 336 |
| histgb | 43.2 | 599 | 47.8 | 412 |
| lr | 43.1 | 756 | 44.4 | 286 |
| dl | 43.1 | 764 | 49.4 | 338 |
| sgd | 17.1 | 70 | 41.7 | 60 |

### C3 — By day (all models pooled, 5m & 15m)
| hz | day | dir calls | acc % |
|---|---|---|---|
| 5m | 2026-06-12 | 1010 | 25.2 |
| 5m | 2026-06-13 | 330 | 22.1 |
| 5m | 2026-06-14 | 439 | 49.0 |
| 5m | 2026-06-15 | 893 | 51.3 |
| 5m | 2026-06-16 | 977 | 51.5 |
| 5m | 2026-06-18 | 121 | 54.5 |
| 5m | 2026-06-19 | 660 | 50.6 |
| 5m | 2026-06-20 | 231 | 51.5 |
| 5m | 2026-06-21 | 52 | 65.4 |
| 15m | 2026-06-12 | 406 | 38.7 |
| 15m | 2026-06-13 | 189 | 27.5 |
| 15m | 2026-06-14 | 259 | 53.3 |
| 15m | 2026-06-15 | 339 | 52.8 |
| 15m | 2026-06-16 | 394 | 50.8 |
| 15m | 2026-06-18 | 62 | 43.5 |
| 15m | 2026-06-19 | 362 | 53.3 |
| 15m | 2026-06-20 | 146 | 50.0 |
| 15m | 2026-06-21 | 35 | 45.7 |

## D. Cross-cutting read
- **Price-to-Beat tracker (clean coin-flip baseline):** 5m **50.2%**, 15m **50.5%** directional.
- **Ensemble raw_direction:** 5m **49.0%**, 15m **47.2%** (from signed move).
- **Best individual model:** 5m rf **52.3%** (n=279), 15m rf **55.7%** (n=115).
- Individual models call UP/DOWN **selectively** (high abstain %); their directional accuracy on the calls they DO make is the number above — compare against the ~50% tracker baseline and treat any lift as selection, not a generalizable direction edge, until it survives more samples.

---

## E. How to read this (verdict + caveats)
**Headline: everything converges on a coin-flip.** The clean baseline (price-to-beat, strict UP/DOWN
resolution) is **50.2% @5m / 50.5% @15m**; the ensemble's `raw_direction` is **49.0% / 47.2%**; the best
individual model on its selective calls tops out ~52–56% on small n. This is the established truth, now
re-confirmed on 10 days of *live* logs across every layer — direction is not an edge at 5m or 15m.

**Why the individual models read ~43% at 5m (NOT "worse than a coin-flip").** `model_predictions` grades
against a **3-way** outcome (UP / DOWN / **NEUTRAL** band — 13.5k of resolved actuals are NEUTRAL). A model
that calls UP and the bar lands inside the NEUTRAL band is scored a miss. So ~43% on a 3-way label ≈ a
coin-flip on the 2-way the tracker measures — the gap is the NEUTRAL band, not negative skill. The strict
price-to-beat tracker (no neutral band) is the apples-to-apples direction number, and it's ~50%.

**`rf` looks best (52.3%/55.7%) but it abstains ~60% — that's selection, not alpha.** It only calls when
confident; on a coin-flip, a selective caller drifts a few points above 50% by picking its spots. Needs far
more samples (and an EV/cost test) before reading anything into it. `sgd` is degenerate (17% @5m, 100% UP
precision on tiny n) — effectively broken; treat it as noise / a candidate to drop.

**Discount 06-12 and 06-13.** Pooled individual-model accuracy is 22–25% (5m) / 27–39% (15m) those two days
— implausibly low even for 3-way grading. That window overlaps the sign-truth grading fixes; treat those
days as a **grading artifact**, not real performance. From 06-14 onward the pooled numbers settle to ~49–52%
(coin-flip), which is the trustworthy range.

**The two places with a faint, sample-limited signal — worth a *look*, not a bet:**
- **Regime:** directional accuracy is higher in `LOW_VOLATILITY` (63%, n=38) and `RANGE` (59%, n=201) than
  in `TRENDING`/`HIGH_VOL` (~46–50%). Small n, but consistent with "quiet/mean-reverting regimes are more
  predictable." This is a *selection/abstention* lever (act less in trend/high-vol), not a new direction model.
- **Confluence grade is inverted/uninformative:** grade **B beats A** (56% vs 47%). The confluence grade is
  not tracking realized accuracy — a flag to recheck how it's computed, not to trust it as-is.

**Model-version (A4): no signal.** No bundle is reliably above ~55% with enough resolved calls; the spread
across 27 retrains is noise. Frequent retraining is not moving directional accuracy — consistent with the
ceiling being in the data, not the model.

**Bottom line for the app:** keep direction as confirmation only (as designed); the value is in **abstention
+ regime selection + the calibrated heads + the Polymarket edge gate**, exactly as the master strategy says.
The cheap, evidence-backed actions that fall out of this data: (1) **lean harder on regime-based abstention**
(trade fewer TRENDING/HIGH_VOL setups), and (2) **investigate the confluence-grade inversion** (B>A).

---

## F. Deep-dive: confluence-grade inversion + the regime lever

### F1 — The confluence grade is genuinely miscalibrated (root cause found)
Directional accuracy by grade (5m+15m, 95% Wilson CI):

| grade | n | acc % | 95% CI | intended |
|---|---:|---:|---|---|
| A | 439 | **47.4** | [42.8, 52.1] | should be *best* |
| B | 278 | **56.1** | [50.2, 61.8] | middle |
| C | 1081 | **50.0** | [47.0, 52.9] | should be *worst* (skip) |

Order is **A < C < B** — non-monotonic and inverted. (The project's own guardrail,
`composed_decision_scorecard.py::summarize` → `grade_ok`, asserts exactly this A<C<B shape must **fail**
the stratification gate — the live grade is currently tripping its own designed check.)

**Root cause — two real bugs in `_confluence()` (server.py:2156):**
1. **Stale `regime_ok` (line 2167):** `regime_ok = regime not in ("LOW_VOLATILITY","UNKNOWN")`. The code
   comment literally says *"LOW_VOLATILITY was the weakest cell in live evidence"* — but the current data
   shows **LOW_VOLATILITY is the *strongest* regime (63%)** and RANGE second (59%). So the grade actively
   **penalizes the most-predictable regime**, dumping it into grade C. The grade×regime cross-tab confirms
   it: LOW_VOLATILITY is **36 of 38 calls in grade C** (61% accurate), and RANGE's best cell is grade C
   (161 calls @ 58%) — the predictive regimes are graded *low*.
2. **Order-flow agreement is 3 of the 5 checks** (cvd / large-trade / book imbalance). At 5m/15m these are
   not directionally predictive, so requiring more of them to reach grade A just selects flow-aligned
   coin-flips. Grade A is **81% UNKNOWN-regime (355/439 @ 47%)** — i.e., A mostly fires where there is no
   regime edge and the flow checks dominate. That's why A underperforms.

**Proposed fix (gated — not yet wired):** (a) flip the regime component to *reward* LOW_VOLATILITY+RANGE
instead of penalizing low-vol; (b) down-weight or drop the order-flow checks from the grade; or (c) replace
the heuristic grade entirely with the data-driven regime lever below. Any change must re-pass the
`grade_ok` monotonicity gate on fresh data before it's trusted.

### F2 — Regime-abstention lever (the one statistically-supported edge in the dataset)
Directional accuracy by regime, with coverage and 95% Wilson lower bound:

| regime | n | coverage | acc % | Wilson-LB |
|---|---:|---:|---:|---:|
| LOW_VOLATILITY | 38 | 2.1% | 63.2 | 47.3 |
| RANGE | 201 | 11.2% | 59.2 | **52.3** |
| TRENDING_DOWN | 162 | 9.0% | 50.6 | 43.0 |
| UNKNOWN | 1163 | **64.7%** | 49.0 | 46.1 |
| TRENDING_UP | 191 | 10.6% | 46.6 | 39.7 |
| HIGH_VOLATILITY | 43 | 2.4% | 46.5 | 32.5 |

Filter simulations vs the 50.3% baseline:

| filter | n | coverage | acc % | Wilson-LB |
|---|---:|---:|---:|---:|
| **act ONLY on RANGE+LOW_VOLATILITY** | 239 | 13.3% | **59.8** | **53.5** |
| act on non-UNKNOWN regimes | 635 | 35.3% | 52.6 | 48.7 |
| abstain on TRENDING+HIGH_VOL | 1402 | 78.0% | 50.9 | 48.2 |

**Only one filter clears significance: "act only in RANGE or LOW_VOLATILITY" → 59.8%, Wilson-LB 53.5%**
(>50% at 95%), but at just **13% coverage**. This is a genuine *selection/abstention* edge — consistent with
"quiet/mean-reverting regimes are more forecastable" — not a new direction model. **Two hard caveats:**
(1) **64.7% of calls are UNKNOWN regime** — the regime classifier mostly isn't labeling, which both caps the
lever's coverage and is worth fixing on its own; (2) n=239 over 10 days — promising, but it needs forward
confirmation before being wired as a gate.

### F3 — Recommended actions (both gated on approval before touching the live pipeline)
1. **Fix `_confluence()`** so the grade stops penalizing LOW_VOLATILITY and stops over-weighting non-predictive
   order-flow — re-verify against `grade_ok` on fresh data.
2. **Investigate why regime is UNKNOWN 65% of the time** (classifier coverage) — this is the bottleneck on
   the only working lever.
3. **Prototype a regime-selection gate** (prefer RANGE/LOW_VOLATILITY) in *shadow* first; promote only if the
   53.5% Wilson-LB holds on more samples.

### F4 — CORRECTION: UNKNOWN is historical, and the clean-window numbers revise F1–F3
Investigating "why regime is UNKNOWN 65%" resolved it: **UNKNOWN is purely pre-wiring history, not a live
classifier gap.** Regime by day in `price_to_beat` is **100% UNKNOWN on 06-12→06-16 and 0% UNKNOWN from
06-18 on** — the regime classifier (`regime.py`) was wired into the tracker on ~06-18. `champion_snapshots`
confirms the classifier is healthy (only ~20% UNKNOWN there). So **action #2 is closed — nothing to fix in
the classifier**; just exclude the pre-06-18 backlog from regime analysis. Re-running on the clean
**regime-era window (06-18+, n=635, baseline 52.6%)**:

**The regime lever is stronger than F2 implied (real coverage 37.6%, not 13%):**
| filter (regime-era) | coverage | acc % | Wilson-LB |
|---|---:|---:|---:|
| **act ONLY on RANGE+LOW_VOLATILITY** | 37.6% | **59.8** | **53.5** |
| abstain on TRENDING_UP only | 69.9% | 55.2 | **50.5** |
| RANGE alone | 31.7% | 59.2 | **52.3** |
| (worst cell) TRENDING_UP | 30.1% | 46.6 | 39.7 |

RANGE alone clears significance at 32% coverage; adding LOW_VOLATILITY lifts to 59.8% @ 37.6%. Even just
**dropping TRENDING_UP** (the worst regime) keeps 70% coverage at 55.2% (LB 50.5%). This is a genuine,
usable selection edge.

**The grade "inversion" was mostly UNKNOWN-era contamination.** On the clean regime-era window the grades
are statistically **flat, not inverted**: A 50.0%, B 51.9%, C 53.1% — wide overlapping CIs, indistinguishable.
So the honest verdict softens: the confluence grade is **non-discriminating** (adds no directional signal),
not catastrophically backwards. It still **fails to capture the one signal that works** — grade A sits at 50%
while RANGE sits at 59% — so the `regime_ok` staleness fix (have the grade *reward* RANGE/LOW_VOLATILITY) is
still the right move, just framed as "make the grade reflect regime" rather than "unbreak an inversion."

**Revised recommended actions:** (1) ~~investigate UNKNOWN regime~~ **CLOSED — historical, not a bug**;
(2) make `_confluence()` reflect the regime edge (reward RANGE/LOW_VOLATILITY, down-weight non-predictive
order-flow), re-verify on regime-era data only; (3) **prototype the regime-selection gate** — at minimum
"avoid TRENDING_UP" (70% coverage, LB 50.5%), ideally "prefer RANGE/LOW_VOLATILITY" (38% coverage, LB 53.5%)
— in shadow, promote only if the lower bound holds on more samples.

### F5 — UPDATE (2026-06-21): `_confluence()` redesign applied
Action (2) is **done**. `_confluence()` (server.py:2156) is now **regime-first**: regime_tier = 2 favorable
(RANGE/LOW_VOLATILITY), 0 adverse (TRENDING_UP/HIGH_VOLATILITY), 1 neutral (TRENDING_DOWN/UNKNOWN); grade A
requires favorable regime **and** ≥2 of 3 order-flow confirmations, so TRENDING_UP/HIGH_VOL can never grade A.
Confirmed: this is a **logged/displayed label only** — `model.py` and `decision_champion.py` have zero
references; no bet/abstain/conviction/champion decision changes. The fix also emits `regime_favorable` /
`flow_agree` so the scoreboard chips (main.js) reflect the new tier. **Takes effect on backend restart**;
the `setup_fingerprint` recorder + `grade_ok` monotonicity check will now measure whether grade A actually
out-performs C going forward. Promoting regime to a real *gate* still requires the shadow-monitor LB to hold
> 50% **and** explicit sign-off (unchanged). Known minor gap (pre-existing, out of scope): the scoreboard
`models` chip reads `cd.models_agree`, which `_confluence()` still does not emit.