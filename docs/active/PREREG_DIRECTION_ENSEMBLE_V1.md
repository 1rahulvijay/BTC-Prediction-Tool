# PREREG — DIRECTION_ENSEMBLE_V1

**Frozen `2026-08-03`, before any ensemble was trained.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

`CONDITIONAL_DIRECTION_V1` measured a single LightGBM direction model at **AUC 0.498**.

> Do **seven direction heads of different model families**, combined by voting, recover
> directional information that one model class missed?

## The prior, stated before the result

Ensembling reduces variance; it does not create information. If one gradient-boosted model
reads 0.498 on a feature set, seven families over the **same features and the same target** are
expected to land near 0.50 as well.

The test is still worth running: a single model class can miss structure another captures — a
linear model finds linear structure a shallow tree splits badly, and vice versa — and a negative
from seven families is far more decisive than a negative from one. This prior is recorded so the
result cannot later be presented as a surprise in either direction.

## The null floor is mandatory

With ~155,000 test bars, small AUC deviations from 0.500 are not automatically meaningful. So
the noise floor is **measured, not assumed**:

```
NULL_FLOOR   200 replications, labels shuffled in whole-DAY blocks, AUC recomputed
```

Day-block shuffling preserves within-day autocorrelation. Shuffling individual bars would
destroy it and produce an artificially tight floor, making noise look like signal.

Any AUC inside the null floor's 95% interval is **indistinguishable from chance**, whatever its
distance from 0.500.

## The seven heads — frozen

```
1  LightGBM              gradient-boosted trees
2  XGBoost               gradient-boosted trees, different implementation and regularisation
3  RandomForest          bagged deep trees
4  ExtraTrees            extremely randomised trees
5  LogisticRegression    linear, standardised inputs
6  MLPClassifier         small neural network
7  GaussianNB            generative, strong independence assumption
```

Chosen to span inductive biases — boosting, bagging, linear, neural, generative — rather than
seven variations of one idea. No head may be added, dropped or swapped after results.

## Voting

```
HARD_VOTE   majority of the seven binary calls
SOFT_VOTE   mean predicted probability across the seven heads
```

Both reported. Neither is selected after the fact: `SOFT_VOTE` is declared the **primary**
ensemble, because averaging probabilities uses more information than averaging discretised
calls.

## Data, target and split

Identical to `CONDITIONAL_DIRECTION_V1` — same 23 frozen backward-looking features, same
15-bar forward return sign, same chronological 70/30 split with a 60-bar purge. Reusing them
exactly is what makes the comparison to AUC 0.498 meaningful.

## Primary endpoint

**Test AUC of `SOFT_VOTE`**, against the null floor and against the best single head.

## Secondary endpoints

```
per-head AUC             all seven, plus HARD_VOTE
head correlation         mean pairwise correlation of predicted probabilities
post-cost net bps        SOFT_VOTE traded on non-overlapping windows, 14 bps round trip,
                         with a day-block CI - the decision-relevant quantity
```

Head correlation is reported because it explains the result either way: heads that agree almost
perfectly cannot diversify, and an ensemble of near-identical predictors is one predictor.

## Verdicts — declared before results

```
ENSEMBLE_ADDS_DIRECTION
    SOFT_VOTE AUC lies ABOVE the null floor's 97.5th percentile, AND its
    post-cost net bps has a day-block 95% CI whose lower bound exceeds zero.

ENSEMBLE_AUC_ONLY
    SOFT_VOTE AUC beats the null floor, but post-cost value does not clear zero.
    Statistically detectable, economically absent.

ENSEMBLE_NO_BETTER_THAN_SINGLE
    SOFT_VOTE AUC does not exceed the best single head's AUC.

DIRECTION_NOT_PREDICTABLE_CONFIRMED
    No head and neither vote exceeds the null floor.
```

`ENSEMBLE_AUC_ONLY` is listed because it is the most likely way to be misled here, and because
AUC is what was asked for: an ensemble can be reliably better than a coin and still lose money
on every trade, if it is right on small moves and wrong on large ones.

## Kill rule

If `DIRECTION_NOT_PREDICTABLE_CONFIRMED`, direction at this horizon on this feature set is
closed, and no further model-family search may be run against it. The remaining hypotheses are
different information or a different horizon, each needing its own preregistration.

## What this may not do

No threshold tuning, no feature search, no alternative horizons, no head substitution, no
weighting of heads by performance, no resampling of the split.

## Stopping rule

Scored **once**.
