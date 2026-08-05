# RESULT — CONDITIONAL_OFFSET_V2

**`2026-08-05`.** `research/conditional_offset_v2.py`, artifact
`research/results/conditional_offset_v2.json` (sha256 `b0e1c184…`). 200,000 1m bars; 15m and 12m
rounds evaluated independently; chronological split with a one-round purge; 42 day-blocks per
round length.

## Verdict, stated at the precision the evidence supports

**The offset fails its Brier gate at both round lengths. It is NOT conclusively worse on log
loss.** The concept is not "closed"; it is *not promotable*.

```text
                          15m                              12m
d_brier     +0.00125  [+0.00030, +0.00217]  WORSE   +0.00131  [+0.00051, +0.00214]  WORSE
d_log_loss  +0.00137  [-0.00085, +0.00344]  incon.  +0.00179  [-0.00016, +0.00372]  incon.
d_auc       -0.00298  [-0.00636, +0.00065]  incon.  -0.00409  [-0.00689, -0.00123]  WORSE
```

Day-block bootstrap, 95%, paired against the baseline on identical rows.

The previous write-up said the offset "loses" from point estimates alone. With intervals, that
holds on Brier and does **not** hold on log loss — which is the metric a log-odds additive model
is actually fitted through. Reporting it as a clean loss overstated the evidence.

## Full table

```text
15m arm              logloss     brier      AUC      ECE    calib
  baseline            0.6003    0.2083   0.7240   0.0115    1.003
  offset              0.6017    0.2095   0.7210   0.0133    0.960
  offset_permuted     0.6010    0.2086   0.7232   0.0103    0.994
  zero_correction     0.6003    0.2083   0.7240   0.0115    1.003

12m arm              logloss     brier      AUC      ECE    calib
  baseline            0.5735    0.1978   0.7537   0.0138    1.032
  offset              0.5752    0.1991   0.7497   0.0101    1.001
  offset_permuted     0.5738    0.1979   0.7535   0.0132    1.026
  zero_correction     0.5735    0.1978   0.7537   0.0138    1.032
```

## The reconstruction is correct — proven, not asserted

`zero_correction` reproduces the baseline to the last digit at both round lengths. That is the
guard against the `init_score` trap: LightGBM's `predict_proba` has no per-row init for new
rows, so the offset must be re-added by hand. Using `predict_proba` would have returned the
trees' own probability and silently dropped each test row's geometry.

```python
correction = booster.predict(X_test, raw_score=True)
p_final    = expit(logit(p_base_test) + correction)
assert np.allclose(expit(logit(p_base) + 0.0), p_base, atol=1e-10)
```

`boost_from_average` was raised as a possible interference. **Measured:** with `init_score`
supplied it changes the learned correction by `< 1e-6`. And `init_score` is genuinely the
starting point — mean |correction| 0.175 with it, 0.651 without.

## Correction magnitude is not evidence — and my earlier claim was wrong

I previously wrote that a mean |log-odds correction| of 0.25 showed the model "doing real
work". That was wrong, and the check that shows why is now in the selftest: with labels drawn
**exactly from `p_base`**, where the correct correction is identically zero, this configuration
still produces mean |correction| ≈ 0.175. Magnitude alone measures flexibility, not signal.

The permuted-feature control is the right diagnostic, and it says something more interesting:

```text
15m  real 0.2545   permuted 0.0685   ratio 3.7x
12m  real 0.2596   permuted 0.0547   ratio 4.7x
```

The correction is **not** noise — it is 3.7–4.7× what the same model produces on shuffled
features, so it is responding to genuine structure in the feature set. **And acting on that
structure makes the forecast worse.** That is a sharper finding than "the model learned
nothing": the features contain structure that does not generalise, and a well-calibrated
baseline is damaged by incorporating it.

Note the permuted arm is itself significantly worse on 15m Brier and log loss. Even a shuffled
correction degrades this baseline — which is a statement about how good `Φ(z)` already is.

## Why the baseline is hard to beat

```text
calibration slope   1.003 (15m) / 1.032 (12m)      1.0 is perfect
ECE                 0.0115 / 0.0138
```

There is very little miscalibration left to correct. A closed form with no parameters is
already producing near-perfectly calibrated probabilities, and the remaining error is
irreducible given the information.

## What this does NOT establish

Everything here is measured against the **outcome**. Nothing is measured against the
**Polymarket price**, which is the only comparison that decides tradeability — and the market
can compute this same `z`. Until book snapshots support that join, "beats geometry" and "is
tradeable" remain different claims and only the first has been tested.

## Reproduce

```bash
python research/conditional_offset_v2.py --selftest
python research/conditional_offset_v2.py --run --rounds 15 12 --rows 200000
```

The run writes `research/results/conditional_offset_v2.json` with every arm, every paired
interval, the parameters and the correction magnitudes.
