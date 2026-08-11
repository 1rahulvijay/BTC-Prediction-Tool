# Two-Sided Round-Trip Fade Engine — $30/$50 multi-barrier + second-leg (2026-07-01)

> **RETRACTED PERFORMANCE CLAIMS:** The v4 event counts/AUC/top-bucket rates below used the completed
> one-minute touch candle and therefore included post-entry movement; most such candles had unknowable
> target/stop ordering. Live serving now requires causal v5 and emits PAPER ONLY. See
> `PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md` for the current source of truth.

The anchor-centric, two-sided Polymarket play, wired live. Builds on the honest strict-label fade retrain
(see the retraction in `PATH_FORECASTER_TRADE_PLAN_HEAD`). **Everything here is PAPER** until the Polymarket
recorder proves the edge survives the share price + costs.

## The play (operator's mental model)
On Polymarket, **the anchor (price-to-beat) is everything**. Near the anchor, a **$20–30** move already
swings the UP/DOWN share prices — you don't need $50. The profit engine is the **round-trip**:
```
price spikes UP $30  → UP share dear, DOWN share cheap → BUY DOWN, sell as it reverts to anchor   (LEG 1)
price reverts, spikes DOWN $30 → DOWN dear, UP cheap    → BUY UP,   sell as it reverts to anchor   (LEG 2)
```
Each leg is an independent fade (buy the cheap *losing* side, take profit at the anchor). In a two-sided /
chop window you can catch **both** — "long and short," profit on each spike. The engine grades **each leg**
with the honest fade model; it does **not** pre-position both sides blindly (a clean both-legs-to-anchor is
only ~30%). It fades each spike **as it happens, gated by the model.**

## Why $30, not $50 (measured, honest)
The whole stack was built at a $50 barrier, but the shares reprice on $20–30. The multi-barrier retrain
(`train_fade_model.py` v4, 360d, strict reach-anchor label) proves $30 is the better Polymarket barrier —
**more setups AND higher win rate**:

| barrier | horizon | events | base reach-anchor | AUC | **win@top-10%** | win@top-25% |
|---|---:|---:|---:|---:|---:|---:|
| **$30** | 5m | 609,615 | **35.2%** | 0.778 | **77.2%** | 65.1% |
| **$30** | 15m | 780,050 | **43.3%** | 0.728 | **77.0%** | 67.9% |
| $50 | 5m | 461,589 | 26.6% | 0.798 | 68.5% | 55.1% |
| $50 | 15m | 669,090 | 38.2% | 0.733 | 69.3% | 62.4% |

A $30 reversion is *easier* (less distance back to the anchor) → higher base rate; and ~1.3–1.5× more touches.
`FADE_L = 30` is the live barrier (`BTC_FADE_BARRIER` overrides). The $50 head stays in the bundle for the
path/round-trip context and back-compat.

## The honest gate (unchanged discipline)
A fade fires **only** when the touch is **EARLY** *and* the model grades it high:
- **early touch AND `p_fade ≥ 0.55`** → ENTER FADE (buy the cheap side, exit at anchor).
- `< 0.45` → SKIP. Borderline → WATCH.
- **late touch → `p_fade ≈ 0.0`** — the model *learned* a late touch can't revert before expiry (strict
  reach-anchor at $50/5m: late 6.9% vs early 41%). Verified live: a 20s-left touch grades 0.0.
Early touch is **necessary but not sufficient**; the touch-context (overshoot, opposite pre-excursion,
pre-range) is what separates a 41% early touch from a ~69–77% top-decile one.

## What was wired (this change)
| Layer | File | Change |
|---|---|---|
| **Model** | `train_fade_model.py` | v4 multi-barrier: trains a separate head per barrier `{30, 50}`; `predict_fade(..., L)` selects the head; bundle `["barriers"][L]["horizons"][w]`, back-compat `["horizons"]` == $50. |
| **Live grade** | `price_to_beat.py` | `FADE_L=30`; `_grade_fade(..., L)` forwards L to the matching head; touch detection at `$FADE_L`. |
| **Second leg** | `price_to_beat.py` | after leg 1, the OPPOSITE-side $FADE_L touch = the round-trip return → graded as an independent fade; surfaced as `touch_state.leg2 = {side, fade, p_fade, call}`; `_trade_signal` branch 3b emits the ROUND-TRIP RETURN play. Leg 2's running hi/lo correctly encodes leg 1's spike as the `pre_opp` "stretched spring." |
| **Signal** | `price_to_beat.py` | `_trade_signal` gates both legs on `p_fade` (≥0.55 ENTER / <0.45 SKIP). |
| **UI** | `src/main.js` | 🎯 TOUCHED line shows the leg-1 `p_fade` and a `↔ 2nd-leg FADE` chip with its P(anchor); the 💹 signal renders the round-trip return. |
| **Retrain** | `train_heads.py` | added `fade` as a **version-aware head** — so `start.bat` now trains it (it was only in the nightly `auto_finetune.py` before). |
| **Nightly** | `auto_finetune.py` | already in REFIT (unchanged); trains both barriers by default. |
| **Window prior** | `backtest_reversal_strategy.py --export-windows` → `price_to_beat.py` | soft reversal-window favorability (per-CEST-hour × weekday fade rate / base) baked to `data/reversal_window_favorability.json`; `_window_quality` surfaces `trade_plan.window_quality={score,label,note}` on the card. **Display/soft only — NOT a hard gate** (the evening edge is real but modest and partly selection bias). |

## Window awareness (soft prior, added 2026-07-01)
The reversal backtest (`REVERSAL_STRATEGY_BACKTEST`) showed reversals cluster by time: **CEST 16–19h (US open) run
~1.2–1.27× the average fade-reach-anchor rate**, Mon–Wed slightly above. That table is exported to JSON and loaded
live: each round's open time gets a `window_quality` **STRONG / OK / WEAK** chip (🕐 on the card). It informs *how
selective to be*, it does **not** gate the fade — consistent with the project's earlier finding that the evening
edge is partly selection bias. Regenerate after a data-window shift with
`python backend/research/standalone/backtest_reversal_strategy.py --source matrix --export-windows`.

## start.bat wiring (confirmed)
`start.bat` → `train_heads.py` (version-aware). The `fade` head now retrains automatically when its
`HEAD_VERSION` changes or the artifact is missing; the single knobs `BTC_HISTORICAL_DAYS` (360) and
`BTC_TRAIN_SPLIT_FRAC` (0.98) drive it like every other head. Also in the nightly `auto_finetune.py` REFIT
(both barriers). No new manual step.

## Validation done
- `train_fade_model.py --selftest` → builds $30 (3277) & $50 (1593) events, both barriers.
- Full 360d retrain → the table above; `fade_model.pkl` = `2026-07-01-fade-v4-multibarrier-30-50` (4.9 MB).
- Backend + `main.js` compile/syntax clean; `train_heads --dry-run` lists `fade` (version-aware).
- Live smoke test: leg-1 early $30 touch grades 0.63, $50 grades 0.70 (barrier selection works), leg-2
  return graded independently, **late touch grades 0.0**. Caught + fixed a bug where `_grade_fade` didn't
  forward `L` to `predict_fade` (was grading $30 touches with the $50 head).

## The honest boundary (unchanged)
This is a **BTC-price-reversion** engine: it says *how likely a $30 spike reverts to the anchor*, not which
side settles (coin-flip) and not that it makes money. A fade/round-trip is +EV only if the cheap share is
**mispriced** vs the reach-anchor odds after costs: `fair − ask − costs − buffer > required_edge`. Gated on
the Polymarket recorder's ~500 joined quote+outcome rounds. **Until then: PAPER.**
