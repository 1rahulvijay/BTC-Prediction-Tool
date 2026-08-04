# RESULT — CROSSING_CALIBRATION_V1

Protocol `PREREG_CROSSING_CALIBRATION_V1.md`, sha256 `add7cb22d4c6f04f7a920c45fc1a0f94cb43d584056f06fddacd85130cfde540`,
frozen 2026-08-04 **before** any calibrator was fitted. Scored **once**, 2026-08-04.

Implementation `research/crossing_calibration_v1.py`. 22 selftest checks, 6/6 mutations caught.

---

## 1. Primary endpoint, as frozen

Round-equal-weighted ECE, 10 equal-count bins, calibrators fitted on train days only.

| target | RAW | ISOTONIC | PLATT | flips | AUC shift | verdict |
|---|---|---|---|---|---|---|
| `is_final_crossing` | **0.1563** | 0.1584 | 0.1607 | 4.95% | +0.0003 | `CALIBRATION_FAILS` |
| `state_original_side_at_30s` | **0.0499** | 0.0790 | 0.0863 | 6.66% | −0.0005 | `CALIBRATION_FAILS` |
| `state_original_side_at_60s` | **0.0839** | 0.1131 | 0.1146 | 11.60% | −0.0007 | `CALIBRATION_FAILS` |

`CALIBRATION_FAILS` was declared as "no calibrator reduces ECE". None did. The verdicts stand.

These three numbers are pinned in `FIRST_SCORING` in the implementation, and the run **exits
non-zero** if a recomputation drifts by more than 0.002 or lands a different verdict. The
stopping rule is now a control, not a promise.

---

## 2. The primary endpoint is measuring my specification error, not the head

The reliability curves contradict the headline. For `is_final_crossing`:

```
predicted -> observed      0.135 -> 0.169    0.365 -> 0.353    0.597 -> 0.564
Murphy reliability term    0.00074           (rms bin deviation ~0.027)
```

That is a well-behaved head. A 0.1563 ECE is not consistent with it. The decomposition explains
the whole gap:

| target | pooled base | round-equal base | shift | round-equal ECE | explained by shift | **residual** |
|---|---|---|---|---|---|---|
| `is_final_crossing` | 0.3706 | 0.5505 | +0.1799 | 0.1563 | 0.1563 | **0.0000** |
| `state_original_side_at_30s` | 0.1800 | 0.1225 | −0.0574 | 0.0499 | 0.0421 | **0.0078** |
| `state_original_side_at_60s` | 0.2957 | 0.2074 | −0.0883 | 0.0839 | 0.0835 | **0.0004** |

**Why.** Sampling one crossing per round changes the population's *base rate*. Every round has
exactly one final crossing, so

```
pooled base rate      = n_rounds / n_crossings = 1 / mean(n) = 0.370
round-equal base rate = mean(1 / n)                          = 0.550
```

and those differ by Jensen's inequality whenever round sizes vary — here mean 2.70, max 16. The
head's probabilities barely move under the resampling (0.3838 → 0.3942) because **how many more
crossings a round will produce is not causally available at the moment of a crossing**. So the
labels shift and the scores do not, and a perfectly calibrated head must show an ECE of about
`|mean(p) − base|`. Measured, that term is 0.1563 of a 0.1563 ECE.

I carried round-equal weighting over from the `CROSSING_HEADS_V1` correction, where it is
correct: it stops a few choppy rounds dominating a **ranking** statistic. For an **absolute
probability** statistic it changes the estimand. That was my error, made when writing the
protocol, and it is the substantive finding of this study.

It is now enforced rather than described. `weighting_decomposition()` reports both weightings so
neither can be quoted alone, and the selftest constructs a by-construction perfect score whose
pooled ECE is ~0 and whose round-equal ECE exceeds 0.10 — if a later edit reports only one
weighting, the test fails.

---

## 3. Pooled figures — diagnostic only, no verdict

Computed **after** the primary endpoint was unblinded. No threshold may be declared against them
retroactively; a bar set after seeing the number is not a bar.

```
is_final_crossing            pooled RAW ECE 0.0255
state_original_side_at_30s   pooled RAW ECE 0.0328
state_original_side_at_60s   pooled RAW ECE 0.0289
```

Against the protocol's 0.02 materiality bar these are near-misses, and a near-miss is a miss.
They are recorded so a future preregistration can be written knowing the scale — not to claim a
pass.

---

## 4. What survives both weightings

**No calibrator improved anything, under either weighting.** Isotonic made the pooled Murphy
reliability term an order of magnitude *worse* — 0.00074 → 0.00757 on `is_final_crossing`,
0.00143 → 0.01622 on the 60s head. That is isotonic overfitting the train distribution, not
calibrating. Platt, being rigid, did the same thing slightly less.

The actionable conclusion does not depend on the weighting dispute: **fitting a calibrator on
these heads is not worth doing.** The heads' probabilities are already about as good as they get,
and the residual error is small enough that it is not what stands between these heads and a
usable action.

The AUC correctness check passed on all three (|shift| ≤ 0.0007 against a 0.005 tolerance),
confirming the calibrators are monotone and the procedure itself is sound.

---

## 5. A defect found in the metric, which affected the real measurement

Equal-count binning sorts by probability. `np.argsort` is **stable**, so tied scores keep their
row order and the bins cut along whatever that order encodes. LightGBM emits many exactly-tied
probabilities — the same property that forced average-rank AUC earlier in this work — and
isotonic regression produces ties *by construction*, since it is a step function.

Ties are now broken by a fixed-seed random key (`_ordered()`), shared by `ece`, `reliability`
and `brier_decomposition`. Effect on the reported numbers: RAW unchanged to four decimals;
ISOTONIC moved (0.1564 → 0.1584, 0.1133 → 0.1131) exactly where the theory says it should.

Mutation-tested: reverting to `np.argsort` is `CAUGHT`.

---

## 6. Mutation testing

| mutation | result |
|---|---|
| tie-break follows row order again | CAUGHT |
| decomposition hides the shift (reports pooled ECE as round-equal) | CAUGHT |
| decomposition claims the shift explains nothing | CAUGHT |
| ECE always returns zero | CAUGHT |
| verdict ignores an AUC shift | CAUGHT |
| cosmetic calibration promoted to a material finding | CAUGHT |

**6/6.** Run against a temp copy; the source was verified byte-identical afterwards, per the
lesson from the background job that silently reverted a fix on 2026-08-04.

---

## 7. What this does and does not unblock

```
crossing heads DISCRIMINATE                     established by CROSSING_HEADS_V1
crossing-head probabilities usable in an EV     residual calibration error is small;
    calculation                                     no calibrator improves it
a crossing-informed ACTION is profitable        NOT ESTABLISHED - unchanged
```

A calibrated probability is an **input to a decision, not a decision**. Every action lane
measured in this repository is closed on cost, and calibration does not change that — it makes
the EV arithmetic honest when a lane finally opens.

Sequence item 6 ("calibrated crossing-state heads") is answered: the heads do not need
calibrating, and the gate it was supposed to place in front of the action tests is not a
blocker. The blocker downstream remains cost, and the blocker upstream remains collection.

---

## 8. Registration

```
docs/active/PREREG_CROSSING_CALIBRATION_V1.md   sealed, 24/24 hashes intact
research/crossing_calibration_v1.py             registered in run_all_sequence FRONTIER
                                                (versioned=31 frontier=31 uncovered=0)
.github/workflows/invariants.yml                --selftest added beside crossing_heads_v1
model_registry                                  NOT registered - this study fits no artifact
                                                and grants no authority
```
