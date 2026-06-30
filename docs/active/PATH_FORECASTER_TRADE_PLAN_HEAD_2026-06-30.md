# Path Forecaster - Layer-2 Path Plan Head - 2026-06-30

## Purpose

The path forecaster is a standalone Layer-2 ensemble for BTC 5m and 15m markets. It predicts
intra-window magnitude and shape, not endpoint UP/DOWN direction.

It answers:

- how high and low price may travel;
- whether either side may move at least $50 or $100;
- whether both +$50 and -$50 may be touched in one round;
- whether a ±$50 extreme will arrive EARLY (in the first half) — the "a fade is coming soon" signal (v3);
- the expected absolute close displacement;
- whether the path is relatively quiet, one-sided, two-sided, or mixed;
- a composed **play** (v3): FADE-SETUP / RIDE / SKIP / WATCH (see Composed Play below).

It does not choose an entry side and does not establish a profitable Polymarket trade. Entry still
requires measured fair-value-versus-ask mispricing after costs.

## Architecture

```text
Frozen direction ensemble: 5m/15m endpoint direction confirmation
Parallel specialist heads: P(Hold), big move, signed band, path forecaster
```

Each path target averages three model families:

1. CatBoost;
2. LightGBM;
3. HistGradientBoosting.

The input schema is the parity-tested keeper set:

```text
rv_15m
rv_30m
rv_60m
compression_ratio
shock_magnitude
```

The saved artifact is:

```text
data/saved_models/path_forecaster.pkl
```

Current schema version:

```text
2026-06-30-path-v3-usd-early
```

## Corrected Targets

Version 1 approximated $50/$100 with fixed 7/14 basis-point labels. That was only exact near a BTC
price of $71,000-$74,000 and became wrong as BTC repriced.

Version 2 trains the classification targets in exact dollars for every historical row. **Version 3 adds
the `touch_early` (fade-setup) target** — a ±$50 excursion within the FIRST HALF of the window, validated
by `probe_first_touch_timing.py` (AUC 0.75/0.83) as the "the extreme is coming soon, and an early extreme
reverts ~2× more than a late one" signal:

```text
touch_50     = future_high - anchor >= $50 OR anchor - future_low >= $50
touch_100    = future_high - anchor >= $100 OR anchor - future_low >= $100
roundtrip_50 = future_high - anchor >= $50 AND anchor - future_low >= $50
asymmetric   = future_high - anchor >= $50 AND anchor - future_low >= $30
touch_early  = first_half_high - anchor >= $50 OR anchor - first_half_low >= $50   # v3
```

High/low quantiles and absolute close displacement remain basis-point regressions and are converted to
dollars at the round's opening anchor. That scale choice is intentional.

## Data And Validation

Training source:

- 518,400 one-minute rows;
- 360 days from 2025-07-02 through 2026-06-26;
- rows sorted and deduplicated by `ts_ms` before forward labels;
- chronological 70% train, 15% calibration, 15% test;
- isotonic calibration for touch probabilities;
- conformal correction for nominal 50% high/low quantile bands.

Latest exact-dollar holdout results:

| Horizon | P(move ≥$50) AUC | P(move ≥$100) AUC | Round-trip AUC | Asymmetric AUC | **Early-touch AUC (v3)** | Net mag skill | High/low coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5m | 0.795 | 0.799 | 0.851 | 0.820 | **0.797** | +0.193 | 0.50 / 0.49 |
| 15m | 0.837 | 0.786 | 0.758 | 0.728 | **0.802** | +0.175 | 0.50 / 0.49 |

These are path-ranking and range metrics, not trade ROI. Independent verification of the *composed* plan
(out-of-sample, via the live serving function) is in `path_plan_verifier.py`: P(move≥$50) calibrated to
~1pt, band coverage 0.50/0.48, and the CHOP/TREND style separates round-trip **24%/48% (two_sided) vs
0.5%/1.7% (quiet)** — the basis for the fade-vs-ride play. Full predictability map: `PREDICTABILITY_MAP_2026-06-30.md`.

## Serving Semantics

`price_to_beat.py` loads only the current schema. A stale basis-point artifact is rejected rather than
silently displayed with incorrect dollar labels.

The loader checks artifact modification time every 30 seconds. `auto_finetune.py` can therefore replace
the path model atomically without restarting the backend.

The plan is computed on the first valid near-open specialist pass and then frozen for that round. Other
specialist heads may refresh every few seconds, but the path plan cannot be recomputed from later keeper
values against the old opening anchor. Late-captured rounds do not receive a path plan.

Served fields:

```text
pred_high
pred_low
high_band
low_band
pred_range_usd
p_move_50
p_move_100
p_roundtrip
p_touch_asym
net_move_usd
p_early        # v3: P(±$50 extreme in the first half)
style
play           # v3: FADE-SETUP / RIDE / SKIP / WATCH
threshold_units
generated_at_ms
```

Style is descriptive and direction-free:

- `quiet`: large movement is less likely than its holdout base rate;
- `two_sided`: a round trip is elevated relative to its base rate;
- `one_sided`: movement is elevated while a round trip is suppressed;
- `mixed`: no strong shape classification.

## Composed Play (v3)

`_predict_path_plan` emits a `play` derived from `style`, `p_move_50`, and the new `p_early`:

- **FADE-SETUP** — `two_sided` (chop) AND `p_early ≥ 0.5`: an early ±$50 extreme is likely and the window
  visits both sides. Trade: wait for the early touch and fade the extreme (buy the losing side), exiting as
  price reverts toward the anchor.
- **RIDE** — `one_sided` (trend): unlikely to round-trip; do not fade.
- **SKIP** — `quiet`: no range to capture.
- **WATCH** — `mixed`: wait for the touch.

This supersedes the earlier "the UI deliberately avoids fade/ride" stance — the fade-vs-ride signals are now
**validated out-of-sample**: round-trip 24%/48% in chop vs 0.5%/1.7% in quiet; post-touch reversal AUC
0.63/0.59 with early touches reverting ~2× more; early-touch AUC 0.80 (`probe_post_touch_reversal.py`,
`probe_first_touch_timing.py`, `path_plan_verifier.py`). The play is a SELECTION + TIMING + fade-vs-ride
read; it still does **not** pick the settled side and is **not** a proven-profit policy. A FADE-SETUP is +EV
only if the cheap share is mispriced versus the reversal odds — it must clear
`fair_value − ask − costs − buffer > required_edge`. **Until the Polymarket recorder proves that edge after
costs, every fade is PAPER.**

## Retraining And Failure Safety

The path forecaster is included in both:

- `backend/train_heads.py` as a versioned required specialist head;
- `backend/auto_finetune.py` as one of four recalibration/refit jobs.

Training writes to a process-specific temporary file and commits with `os.replace`. An interrupted fit
keeps the previous valid artifact. `auto_finetune.py` now exits nonzero if any requested step fails.

Manual retrain:

```powershell
python backend\train_path_forecaster.py
```

Validation:

```powershell
python backend\train_path_forecaster.py --selftest
python backend\train_heads.py --dry-run
```

## Interpretation Rule

Use the head for range, exit planning, and the fade-vs-ride play (v3). Do NOT infer the settled side from
predicted high/low symmetry, round-trip probability, the `one_sided` label, or the `play`: which side wins
remains coin-flip. `FADE-SETUP` says the window will likely visit both extremes early (fade the touch) — not
which way it closes. Every play is PAPER until the recorder proves the `fair − ask − buffer` edge after costs.
