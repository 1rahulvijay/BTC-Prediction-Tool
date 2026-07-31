# P(hold): the retrain premise was wrong, and calibration is the real lever

`2026-07-31`. Prompted by "retrain p_hold on the current v14 contract."

---

## 1. Correction: p_hold is not on the v14 contract, and was retrained two days ago

I told you the June AUC of 0.746 "was measured on a system that no longer exists," citing the
VWAP formula changes. **That is true of the main ensemble. It is not true of p_hold**, and I
over-applied it.

Checked directly from the artifact:

```
data/saved_models/round_state_heads.pkl
  trained_at : 2026-07-29 02:30:05 UTC        <- two days ago, not June
  features   : rv_15m, rv_30m, rv_60m, compression_ratio, shock_magnitude,
               seconds_left, distance_usd, abs_distance_usd, range_so_far_usd,
               recrosses_so_far, time_above_so_far, current_side_up
  vwap features: NONE
```

The recorded contract changes are `vwap()` (v2, v3) and `cvd_slope_divergence` (v4). **p_hold
consumes neither.** It has its own 12-feature contract, entirely disjoint from the main model's
63/69, and the VWAP semantics changes never touched it.

So there is no v14 retrain to perform for this head. The `UNKNOWN` verdict on all 12 artifacts is
about **missing provenance manifests**, not stale feature semantics — a distinction I collapsed,
and they are not the same problem.

**What is real** is the defect the measurement actually found: p_hold is overconfident. That is a
calibration problem, and a retrain on the same features and the same target would not fix it.

## 2. Calibration, measured

`backend/phold_challenger.py` already existed for exactly this, built in the parallel session,
and independently reports the same overconfidence I measured. Run against the live DB:

| horizon | n | metric | raw | calibrated |
|---|---:|---|---:|---:|
| **5m** | 5,079 | ECE | 0.08833 | **0.01361** |
| | | log-loss | 0.93980 | **0.34295** |
| | | Brier | 0.11151 | **0.10263** |
| | | predicted vs realized | 95.5% vs 86.8% | 87.4% vs 86.8% |
| **15m** | 1,646 | ECE | 0.02668 | **0.00498** |
| | | log-loss | 0.22962 | **0.12419** |
| | | predicted vs realized | 98.0% vs 95.7% | 96.0% vs 95.7% |

Overconfidence **+8.70pp → +0.57pp** on 5m. Wins on Brier **and** log-loss **and** ECE, which is
the challenger's own three-way adoption bar. Both horizons land far under the 0.05 ECE gate.

**Calibration cannot create ranking skill.** A calibrator is a monotone map: it cannot reorder
rounds, so AUC is unchanged (0.7762 → 0.7771, the difference being isotonic tie-flattening). What
it changes is whether a *fair-value* statement is trustworthy — and that is the whole point.

## 3. What calibration unlocks: a fair-value comparison

Raw p_hold cannot support `is the ask below the true probability?`, because it says 95% where 87%
realizes. Every "cheap" call it makes is biased by 8.7 points. Calibrated, the question becomes
askable:

```
edge = calibrated_p - ask - fee      trade only when edge > 0
```

### The leakage that would have made this meaningless

The shipped calibrator in `data/research/phold_challenger/` was **fitted on the same rounds** an
evaluation would score. Applying it there lets its knots carry outcome information about the very
observations being judged. So the study refits the calibrator **inside each split, on strictly
earlier days only, per horizon**.

`python research/phold_calibrated_fair_value.py`

| train d | test d | test n | traded | net/$1 | day LCB | baseline | base LCB | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 7 | 14 | 2091 | 699 | **+0.0430** | **+0.0164** | +0.0076 | −0.0006 | **PASS** |
| 10 | 11 | 1571 | 564 | **+0.0419** | **+0.0119** | +0.0091 | −0.0020 | **PASS** |
| 14 | 7 | 986 | 359 | +0.0078 | −0.0284 | +0.0002 | −0.0130 | **FAILS** |

**2 of 3 strictly temporal splits pass**, each against a trade-everything baseline that does not.
This is the first result in this repository to survive a temporal split — 39 research scripts
produced 0 positive out-of-sample, and this is not one of those 39.

### Why this is a candidate and not a finding

- **The failing split is the most recent one.** That is the window closest to what tomorrow looks
  like. It also has the fewest test days and the fewest trades, so decay and small-sample noise
  are not separable. **21 days of live rounds cannot separate them.**
- 21 days total, one venue, one rule's quote stream, ~33% of rounds selected.
- The in-sample version of this number was `+0.0445` with LCB `+0.0183` at every threshold. The
  temporal version is close to it on two splits and collapses on the third, which is exactly the
  pattern that separates a real effect from a fitted one — and it has not fully separated here.

**What would settle it**: forward evidence on rounds recorded *after* this measurement, scored by
a calibrator frozen today. That is a week of collection, not an analysis.

## 4. The economic mechanism, stated plainly

This is not a better forecast. The head's ranking is unchanged and the price still contains it —
the earlier finding that "the mean ask is 0.8810 against an 89.2% hold rate" stands.

What the calibrator adds is the ability to identify the subset where the **ask is above the true
probability**, i.e. to decline the expensive two-thirds. Trading everything earns +0.0076 with a
negative bound; declining correctly earns +0.0430. The edge is in the **refusals**, not the picks.

That is also why it is fragile: it depends on the market's mispricing being persistent in the same
direction, which is exactly the kind of thing that decays. The most recent split failing is
consistent with decay, and consistent with noise, and 21 days cannot tell you which.

## 5. Status

| | |
|---|---|
| p_hold retrained on v14 | **not applicable** — p_hold is not on that contract |
| p_hold retrained recently | already done, 2026-07-29, by the existing trainer |
| calibrator fitted and measured | yes, wins on all three metrics |
| calibrator applied to serving | **NO** — requires `BTC_APPLY_PHOLD_CALIBRATION=1`, an explicit operator decision |
| fair-value edge | **CANDIDATE**, 2 of 3 temporal splits, most recent one fails |
| real orders | **DISABLED** |

Nothing was wired. No artifact was promoted. The calibrator remains shadow-only by its own
design, and this document does not authorize a real order.

## 6. Reproduce

```bash
python backend/phold_challenger.py --db data/btc_duckdbs/analytics.duckdb
python research/phold_auc_and_expectancy.py
python research/phold_calibrated_fair_value.py
```
