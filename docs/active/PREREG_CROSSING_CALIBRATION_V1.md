# PREREG — CROSSING_CALIBRATION_V1

**Frozen `2026-08-04`, before any calibration was fitted.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction. May not promote a
strategy, tune a threshold, or authorise capital.

## Question

`CROSSING_HEADS_V1` established **discrimination**: round-equal AUC 0.6694 / 0.6814 / 0.6547
against a clock baseline, every interval clear of zero.

AUC is invariant to any monotone transform of the score. A head with AUC 0.68 can be
systematically overconfident, systematically underconfident, or simply mis-scaled, and the AUC
will not move. Every downstream use of these heads is an **expected-value** calculation:

```
E[value of action] = P(state) x payoff(state) - cost
```

which consumes the probability itself, not its ranking.

> Are the crossing-head probabilities calibrated, and does calibrating them change what a
> decision would be?

## Why this is the gate for the action tests

Crossing-informed HOLD / EXIT / REDUCE / SWITCH / LOCK, opposite-token excursion, and the
digital-option residual all multiply a probability by a payoff. An uncalibrated 0.70 that is
really 0.55 turns a losing action into an apparent winner. Calibration is therefore a
prerequisite, not a refinement.

## Method — frozen

```
data          the same 15,428 labelled crossings, same day split, same features
targets       is_final_crossing, state_original_side_at_30s, state_original_side_at_60s
fit           isotonic regression AND Platt scaling, both fitted on TRAIN ONLY
evaluation    the untouched test days, scored ONCE
weighting     ROUND-EQUAL - one crossing per round, 400 draws, per CROSSING_HEADS_V1's
              correction, because a round with 12 crossings must not count 12 times
```

Two calibrators are fitted because they fail differently: isotonic is flexible and can overfit a
small sample; Platt is rigid and cannot fix a non-monotone distortion. Reporting both, with the
uncalibrated head beside them, prevents choosing a method after seeing which flatters the result.

## Primary endpoint

**Expected Calibration Error (ECE)**, 10 equal-count bins, round-equal weighted, for:

```
RAW        the head as CROSSING_HEADS_V1 published it
ISOTONIC   fitted on train
PLATT      fitted on train
```

## Secondary endpoints

```
Brier score, and its Murphy decomposition: reliability - resolution + uncertainty
reliability curve, 10 bins: predicted vs observed frequency
maximum single-bin deviation
AUC before and after - it MUST be unchanged by a monotone calibrator, and that is a
    correctness check on the procedure, not a result
decision flip rate: share of crossings whose EV sign changes at a declared payoff
```

## The decision-flip test

Calibration only matters if it changes a decision. Using a declared, illustrative payoff of
**+1.0 / −1.0 per unit at a 0.50 EV threshold**, the share of test crossings whose implied
action flips between RAW and the better calibrator is reported. A calibration that moves ECE but
flips nothing is a cosmetic improvement and is reported as such.

## Verdicts — declared before results, per target

```
HEAD_IS_CALIBRATED
    RAW ECE <= 0.02. Calibration is unnecessary; the head may be used in an EV
    calculation as published.

CALIBRATION_MATERIALLY_IMPROVES
    Best calibrator reduces ECE by >= 0.02 absolute AND flips >= 1% of decisions.

CALIBRATION_COSMETIC
    ECE improves but the decision-flip rate is < 1%. Worth applying, not worth
    treating as a finding.

CALIBRATION_FAILS
    No calibrator reduces ECE, or AUC moves by more than 0.005 - which would mean
    the calibrator is not monotone and the procedure is broken.
```

`0.02` is the declared materiality bar for ECE, set before any result, and matches the scale at
which a probability error would change a marginal EV decision at these payoffs.

## What this may not do

No threshold tuning, no feature changes, no re-training of the underlying heads, no selection of
a calibrator after seeing test results — both are reported. Calibration is fitted on train only;
touching test would make the measurement meaningless.

A calibrated probability remains an **input to a decision, not a decision**. Every action lane in
this repository is closed on cost, and calibration does not change that.

## Stopping rule

Scored **once**.
