> **v4 (2026-07-03): BPS labels.** Classifier targets (touch/round-trip/asym/early) now compare each
> row's RELATIVE excursion (bps of its own price) to the bps-equivalent of the nominal $50/$100/$30
> thresholds at the latest training price. Serve keys/UI unchanged (`touch_usd` 50/100 remain the
> lookup contract). Validated on 400d vs v3: equivalent (5m touch 0.783/0.785 vs 0.780/0.793, band
> cov 0.49, net-mag +0.212). Motivation: fixed-$ labels drift ~2x/quarter with BTC's price level and
> ~7.5x over the planned 1200-1500d window ($15.5k..$115k). Same upgrade applied to the four keeper
> heads (`keeper_head_training.derive_buckets_bps` + `rel_bps`, versions bumped to `keeper5-bpslabels`).

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

> **⚠️ READ THE RETRACTION FIRST (2026-07-01).** Everything below tagged *(v3-original)* — the
> "early-touch AUC 0.75/0.83 → 0.797/0.802", the "early extreme reverts ~2×", and the Composed Play win rates —
> is **RETRACTED as an artifact** (inverted `early = tm > half` label + settle-generous grade). The **authoritative
> honest numbers, the two-bug root cause, and the strict re-grade** are in the **"Honest strict re-grade"**
> section at the **bottom of this doc**, and summarized in `PREDICTABILITY_MAP` / `PROJECT_MASTER_REFERENCE`.
> Short version: fade base rate **27%/38%**, model AUC **~0.80/0.73**, top-decile → reaches anchor **~69%**;
> early touch is **necessary but not sufficient**; live gate = early touch **AND** P(reach-anchor) ≥ 0.55; PAPER
> until the recorder proves after-cost.
>
> **↑ SUPERSEDED (2026-07-01) by the multi-barrier round-trip engine.** The fade model is now v4
> (`2026-07-01-fade-v4-multibarrier-30-50`): a **$30** barrier (Polymarket-relevant: base 35%/43%, top-10% **77%**,
> ~1.3–1.5× more setups than $50) is the live default, and the live path grades **both legs** of the round-trip
> (leg 1 spike + leg 2 return). Full design: **`FADE_ROUNDTRIP_ENGINE_2026-07-01.md`**.

## Corrected Targets

Version 1 approximated $50/$100 with fixed 7/14 basis-point labels. That was only exact near a BTC
price of $71,000-$74,000 and became wrong as BTC repriced.

Version 2 trains the classification targets in exact dollars for every historical row. **Version 3 adds
the `touch_early` (fade-setup) target** — a ±$50 excursion within the FIRST HALF of the window. *(v3-original:
"validated by `probe_first_touch_timing.py` (AUC 0.75/0.83) as an early extreme that reverts ~2×" — **RETRACTED**,
see banner; the honest reach-anchor model is `train_fade_model.py` v3-strict-honest.)*

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

| Horizon | P(move ≥$50) AUC | P(move ≥$100) AUC | Round-trip AUC | Asymmetric AUC | ~~Early-touch AUC (v3)~~ ⚠️RETRACTED | Net mag skill | High/low coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| 5m | 0.795 | 0.799 | 0.851 | 0.820 | ~~0.797~~ → honest fade **~0.80** (reach-anchor) | +0.193 | 0.50 / 0.49 |
| 15m | 0.837 | 0.786 | 0.758 | 0.728 | ~~0.802~~ → honest fade **~0.73** (reach-anchor) | +0.175 | 0.50 / 0.49 |

*(The P(move≥$50)/$100/round-trip/asymmetric/net-mag/band columns are the path head's magnitude & range metrics
and stand. Only the **early-touch/fade** column was the artifact — replaced by the strict reach-anchor model.)*

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

The CHOP-vs-quiet round-trip separation stands (24%/48% chop vs 0.5%/1.7% quiet). **⚠️ But the v3-original fade
claim in this paragraph — "post-touch reversal AUC 0.63/0.59, early touches reverting ~2×, early-touch AUC 0.80"
— is RETRACTED** (inverted-label + settle artifact; see the banner and the "Honest strict re-grade" section).
The **honest live gate** is not the loose `p_early ≥ 0.5` heuristic: `_grade_fade` now requires an **early touch
AND the strict reach-anchor model `p_fade ≥ 0.55`** (base rate only 27%/38%; a *late* touch grades ~0.0 and never
fires). The play is a SELECTION + TIMING + fade read; it does **not** pick the settled side and is **not** a
proven-profit policy — a fade is +EV only if the cheap share is mispriced versus the honest reach-anchor odds and
clears `fair_value − ask − costs − buffer > required_edge`. **Until the recorder proves that edge after costs,
every fade is PAPER.**

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

## Live Recording & Real-Time Touch State (2026-06-30)

Two serving additions (additive, crash-safe; the frozen plan predictions are never altered):

**1. Live metrics recording (the verifier's "ongoing live recording" step).** `_refresh_live` tracks the
window's running high/low (`_tp_run_hi`/`_tp_run_lo`); at resolution `_log_path_plan_outcome` appends one
row per round to `data/path_plan_outcomes.csv` (served plan + realized extremes: touched ±$50, round-trip,
band coverage, net move). `backend/research/standalone/path_plan_scorecard.py` (read-only, `--selftest`) grades the LIVE log:
P(move≥$50) calibration, round-trip calibration, CHOP/TREND realized-round-trip separation, band coverage,
and a per-`play` breakdown. Accumulates as the app runs — the production counterpart to the offline
`path_plan_verifier.py`.

**2. Real-time TOUCH STATE (early-exit reversal/continuation read).** Once the live price touches a
predicted ±$50 barrier, `_path_touch_state` (per-tick, in `_refresh_live`) freezes the touch timing and
emits `trade_plan["touch_state"] = {side, phase(early/late), bias(UP/DOWN), call}`:
- **two_sided / round-trip≥0.30** → `REVERSAL possible — fade back toward anchor` (bias = opposite the touched
  side; ⚠️ the old "an EARLY touch reverts ~2×, AUC ~0.63" was RETRACTED — the honest gate is early touch AND
  strict `p_fade ≥ 0.55` from `_grade_fade`; a *late* touch reaches anchor only 6.9% and grades ~0.0).
- **one_sided** → `CONTINUATION more likely — ride it` (bias = same as the touched side).
- else → `MIXED — watch`.

`bias` is the conditional reversal LEAN (price likely to **increase** vs **decrease** from here), rendered
as `🎯 TOUCHED HIGH (early) → ↓ expect decrease …`. It is NOT a guaranteed direction — which side ultimately
settles stays coin-flip; this is the fade-vs-ride tendency at the moment of the touch, for the any-direction
early-exit (sell the spiked side and re-enter the fade, etc.). Entry edge still requires mispricing.

---

## Fade entry model v2 — touch-context features (2026-07-01)

`backend/train_fade_model.py` predicts **P(fade wins)** = at the moment price touches anchor±$50, will it
revert to the anchor (TP) before extending to the 2× stop? (Ensemble CatBoost+LightGBM+HistGBM, isotonic,
temporal 98/2 via `BTC_TRAIN_SPLIT_FRAC`, saved `data/saved_models/fade_model.pkl`.) v1 used timing only
(keepers + `touch_frac` + `side_up`). **v2 adds three leak-free touch-context features**, all known AT the
touch minute `tm` (the outcome is scanned strictly from `tm+1`, so features 0..tm never overlap the label):

| feature | meaning | intuition |
|---|---|---|
| `overshoot_bps` | how far past $50 the spike ran at the touch | a bigger overshoot = a more stretched spring |
| `pre_opp_bps` | furthest OPPOSITE excursion before the touch | round-trip / chop context |
| `pre_range_bps` | total high-low range before the touch | chop (wide) vs one-way (narrow) |

**A/B verdict** (`backend/research/standalone/probe_fade_features_ab.py`, temporal 96/2/2, n≈150k–220k events):

| feature set | 5m OOS AUC | 15m OOS AUC |
|---|---|---|
| BASELINE (timing, 7 feats) | 0.613 | 0.543 |
| **+TOUCH-CTX (10 feats)** | **0.765 (+0.15)** | **0.701 (+0.16)** |
| +RESEARCH (26 feats: vol/vpin/cvd/funding/basis) | 0.767 (+0.00) | 0.700 (−0.00) |

Two findings: (1) the 3 touch-context features are a **real, leak-free +0.15 AUC** and they **rescue the 15m
fade** (0.54→0.70, previously written off as unusable); (2) the 16 heavy microstructure/flow features add
**~nothing** on top — the fade is about *how price reached the touch*, not the order-flow state.

**Leak caught & removed:** `ret_5m` from the research matrix is a **FORWARD 5-minute return** (corr **0.976**
with the future return) — a label, not a feature. Including it faked AUC 0.94. It is excluded from every fade
feature set (Rule 1: too-good ⇒ leakage until audited). The `*_accel` features are clean (forward-corr ~0.03).

> ⚠️ The v2 AUC/win numbers above used a **settle-generous label** and are RETRACTED — see the v3 correction.

## Fade entry model v3 — HONEST label + live wiring (2026-07-01, supersedes v2)

**The artifact (caught via `backend/probe_roundtrip_and_timing.py`).** v2's `fade_win` reused
`_first_passage_fade`'s settle-by-close fallback: a touch that reached neither the anchor TP nor the stop by
expiry counted as a "win" if price merely ticked back off the level. This **massively inflated LATE touches**
— a $50 last-minute touch "won" 71% by settle but strictly reaches the anchor only **6.9%** of the time (no
time to revert). The v2 win@top10%=0.998 was this artifact. Two upstream bugs fed the myth:
- `probe_fade_entry_exit.py` had `early = tm > half` **inverted** (`tm > half` is a *late* touch) — the origin
  of the false "an early touch reverts ~2×" claim that had propagated into `_path_touch_state`. **Fixed.**
- a `+2L` sign asymmetry in the down-touch `overshoot_bps` (up vs down encoded differently). **Fixed** (symmetric).

**Honest strict label (`_fade_strict`): price must ACTUALLY reach the anchor TP before the stop; unresolved =
loss.** Timing audit (`probe_roundtrip_and_timing.py`, $50/5m): Q1 earliest touch **41% strict** vs Q4 latest
**6.9%**. So **early touch is NECESSARY** (late can't revert in time) but **not sufficient** (Q1 alone < 50%).

**Production model v3** (`2026-07-01-fade-v3-strict-honest`, honest label, symmetric feature):
`[5m] base 0.266  AUC 0.798  win@top25%=0.551  win@top10%=0.685` · `[15m] base 0.382  AUC 0.733
win@top25%=0.624  win@top10%=0.693`. Real signal (AUC ~0.80) but the **top-decile touches reach the full anchor
~69%, not 99%** — and only ~27% of all touches ever do. The touch-context is what lifts timing-only (~48% top
decile) to ~69%. Added to `auto_finetune.py` REFIT (400-day rebuild after the matrix).

**Wired LIVE (`price_to_beat.py`).** `_load_fade_model()` (mtime-reload) + `_grade_fade(side, touch_secs_left,
pre_hi, pre_lo, anchor, horizon, keepers)` rebuilds the 3 touch-context features from the **pre-touch running
hi/lo frozen at the touch** (`_tp_touch_pre_hi/_lo`) and returns `P(reach anchor)` onto `touch_state["p_fade"]`.
`_trade_signal` now fades an early touch **only when the model greenlights it**: `p_fade≥0.55` → ENTER (text
shows "model P(reach anchor)=X%"), `<0.45` → SKIP, else WATCH. A late touch never sets `bias` (watch-only), and
the live grade returns ~0.0 for a late touch — verified. `_path_touch_state` text corrected: fade LEAN is
emitted only on an EARLY touch in chop.

**Honest bottom line:** the fade edge is real but **modest** (top touches ~69% reach anchor, strict) and remains
a BTC-price-reversion stat — **paper until the Polymarket recorder proves the after-cost edge**. On Polymarket a
*partial* revert ($20–30 of the $50) already moves the share price, so the strict full-anchor number is a
conservative FLOOR; realized share P&L is likely better. Open next step: a **$30-barrier fade variant** (round-
trip is ~2× more frequent at $30 — see below) since Polymarket shares react to $20–30 moves.

### Round-trip (bet-both-ways) frequency — `probe_roundtrip_and_timing.py`
Both ±L touched in one window (price goes both ways → fade each extreme, TP anchor, exit):

| barrier | 5m round-trip | 15m round-trip | both-legs reach anchor (strict) |
|---|---|---|---|
| $20 | 39.6% | 62.6% | ~30–33% |
| $30 | 27.9% | 52.1% | ~29–31% |
| $50 | 13.9% | 35.1% | ~28% (5m) |

The round-trip *touch* is common (esp. at $20–30, the Polymarket-relevant sizes), but a clean **both-legs**
revert-to-anchor is only ~30% — so you don't pre-position both blindly; you **fade each spike as the model
greenlights it** (the live gate above), which is the correct "buy both ways" mechanic.
