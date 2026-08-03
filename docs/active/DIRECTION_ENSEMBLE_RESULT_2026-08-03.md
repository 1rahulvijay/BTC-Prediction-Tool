# Seven direction heads + voting — result

**Protocol** `PREREG_DIRECTION_ENSEMBLE_V1.md` sha256 `bd48d3c5…`, frozen before training ·
**Script** `research/direction_ensemble_v1.py` · Scored **once**

```
train 362,855 / test 155,450 bars    horizon 15 bars    train up-rate 49.8%
```

## Result

| head | test AUC |
|---|---:|
| **LogisticRegression** | **0.5151** ← best |
| RandomForest | 0.5130 |
| XGBoost | 0.5113 |
| LightGBM | 0.5109 |
| ExtraTrees | 0.5098 |
| MLP | 0.5088 |
| GaussianNB | 0.5085 |
| HARD_VOTE | 0.5115 |
| SOFT_VOTE (primary) | 0.5137 |

```
null floor (labels shuffled by day, 200 reps)   median 0.5012, 95% [0.4946, 0.5072]
mean pairwise correlation between heads          0.451
SOFT_VOTE traded, 9,716 non-overlapping windows  -13.72 bps, CI [-14.15, -13.31]
```

**VERDICT: `ENSEMBLE_NO_BETTER_THAN_SINGLE`** — the soft vote (0.5137) does not exceed the best
single head (0.5151).

## There is a real directional signal, and it is worth 0.28 bps

This is the finding the null floor made visible, and it changes the picture from the single-model
test.

**All seven heads sit above the null floor's upper bound of 0.5072.** Direction at this horizon
is *not* pure noise — the signal is statistically distinguishable from chance, and the earlier
AUC 0.498 was the **gated subset**, not the unconditional one, which read 0.511 there too. The
two results agree.

Now the economics, which is why it does not matter:

```
SOFT_VOTE net       -13.72 bps
cost                 14.00 bps
implied gross edge   +0.28 bps

the cost of acting is 50x the edge being acted on
```

The signal is real, and the round trip is **fifty times** its size. Its post-cost interval
[−14.15, −13.31] lies entirely below zero.

In rank terms: the best head has a 3.0% ranking advantage over a coin, the noise ceiling is
1.4%, so roughly **1.6 points of genuine advantage** survive — and they buy 0.28 bps.

## The ensemble did not help, and the reason is visible

Mean pairwise correlation between heads is **0.451** — moderate, so diversification was
genuinely available. It still did not produce a better ranker than its best member.

More telling: **plain logistic regression beat every tree ensemble and the neural network.**
Whatever weak structure exists here is essentially linear, and the higher-capacity models spent
their capacity fitting noise. Adding model families to a problem with ~1.6 points of linear
signal does not find more signal; it averages one weak linear predictor with six that are
slightly worse.

That is the direct answer to the question this test was built for: **no, voting across families
does not recover direction here.** Not because the ensemble was built badly, but because there
was 0.28 bps to recover.

## Why the null floor mattered

Without it, "AUC 0.5137" is uninterpretable — it could be signal or it could be what 155,450
correlated bars produce by chance. Measured: chance produces 0.5012 with a 95% range reaching
0.5072. So 0.5137 is genuinely above noise, and 0.5060 would not have been.

The floor was shuffled in **whole-day blocks**. Shuffling individual bars would have destroyed
within-day autocorrelation, tightened the floor artificially, and made several of these heads
look far more impressive than they are.

## What this changes, and what it does not

**Changes:** the claim "direction is unpredictable" is too strong. Direction is *weakly*
predictable at this horizon — reliably, across seven independent model families, above a
measured noise floor.

**Does not change:** the lane is still closed. A 0.28 bps gross edge against a 14 bps round trip
is not a strategy at any size, with any model, on this venue. `ENSEMBLE_NO_BETTER_THAN_SINGLE`
and the negative post-cost interval say the same thing from two directions.

## Governance

- Protocol frozen and hashed before training; 17/17 hashes verify in CI.
- The prior — *"ensembling reduces variance, it does not create information"* — was written into
  the protocol before the result, so this cannot be read as a surprise in either direction.
- `SOFT_VOTE` was declared primary in advance, before it was known that `HARD_VOTE` scored lower.
- Seven families were fixed in advance; none added, dropped or swapped after seeing AUCs.
- Same features, target, split and purge as `CONDITIONAL_DIRECTION_V1`, which is what makes the
  comparison meaningful.
- Selftest 11 checks, including that an uninformative score falls **inside** its null floor and
  an informative one sits **above** it — so the floor is neither vacuous nor unbeatable.

## Where this leaves direction

```
single LightGBM (gated)     AUC 0.498    inside noise
single LightGBM (uncond)    AUC 0.511    above floor, worth 0.28 bps
seven heads + soft vote     AUC 0.5137   above floor, worth 0.28 bps
best single (logistic)      AUC 0.5151   above floor, worth 0.28 bps
```

Every route to *more model* over this feature set converges on the same 0.5 bps of gross edge.
Per the protocol's kill rule the model-family search is closed: the remaining hypotheses are
**different information** (sequenced L2, book depth — absent from this archive) and a
**different horizon**, each needing its own preregistration.
