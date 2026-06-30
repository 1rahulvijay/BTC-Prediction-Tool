# Predictability Map — what BTC 5m/15m windows can and cannot predict (2026-06-30)

The canonical "what's predictable here" reference, every claim measured out-of-sample on the 360-day
matrix (518,400 rows) with walk-forward AUC vs a shuffled null. **The one rule:** predict the
**path / structure / volatility / speed** of a window — *never* its close-direction (the dead coin-flip).

Companions: [PATH_FORECASTER_TRADE_PLAN_HEAD](PATH_FORECASTER_TRADE_PLAN_HEAD_2026-06-30.md) (the live head),
[CODE_AND_LOGIC_VALIDATION](CODE_AND_LOGIC_VALIDATION_2026-06-30.md) (the audit).

---

## The five validated layers (all beat their null on the held-out tail)
| # | Layer | What it predicts | Evidence (5m / 15m) | Probe / head |
|---|---|---|---|---|
| 1 | **Window selection** | BIG window (next range in top ⅓) | AUC **0.843 / 0.832**; range autocorr 0.65 / 0.61 | `probe_range_expansion.py` |
| 1b | Vol expansion | next range > previous range | AUC 0.635 / 0.680 | `probe_range_expansion.py` |
| 2 | **Structure** | CHOP vs TREND (round-trip both ±$50) | round-trip AUC **0.84 / 0.78**; CHOP round-trips **24% / 48%** vs quiet **0.5% / 1.7%** | path head (`_predict_path_plan`) |
| 3 | **Activity / speed** | P(touch ±$50) · P(early touch) | touch AUC **0.85 / 0.91**; early-touch AUC **0.75 / 0.83** | `probe_first_touch_timing.py` |
| 4 | **Fade trigger** | post-touch reversal (close back through anchor) | AUC **0.63 / 0.59**; early touches revert **~2×** (5m 28%→13%) | `probe_post_touch_reversal.py` |
| 5 | **Risk / hold** | big-drop downside path · P(Hold) late-entry | big-drop gated AUC ~0.75; P(Hold)≥0.93 → ~95% realized | live keeper heads |

**Calibration (path head, verifier on the held-out 20%):** P(move≥$50) predicted 0.659/0.906 vs realized
**0.665/0.914**; band coverage **0.50/0.48** (nominal 0.50); net-drift means match ($61↔$60, $105↔$102).
The path head is **calibrated**, not just rank-ordered. (`path_plan_verifier.py`)

---

## The dead zone (do NOT relitigate — coin-flip, measured every way)
| Target | Result |
|---|---|
| Close direction UP/DOWN (any horizon/hour/day/weekday) | ~**49.8%** live; AUC ~0.50 |
| Which side touches **first** (up vs down) | AUC **0.519 / 0.518** → coin-flip |
| "Predict the close better with feature X / sentiment / more candles" | measured dead ~15 ways |

Direction is the information ceiling. More models on the same OHLCV will not move it.

---

## The composed engine (every layer is validated; none predicts the settled side)
```
1. WINDOW SELECT   BIG-window AUC 0.84      → skip the quiet bottom third (no $ to capture)
2. STRUCTURE       CHOP vs TREND            → CHOP = fade extremes · TREND = ride · quiet = sit out
3. ACTIVITY/SPEED  P(touch)+P(early) high   → expect a touch, and expect it EARLY
4. FADE TRIGGER    early touch + P(reversal)→ buy the cheap (losing) share at the extreme
5. RISK            big-drop / P(Hold)       → avoid-long on flush risk · late-entry hold when P(Hold) high
```
This is the **"fade the early extreme in active chop windows; ride trends; sit out quiet"** engine — the
within-window, any-direction edge realized through measured signals.

---

## The honest boundary (the make-or-break, unchanged)
All five layers predict the **path**, which is genuinely forecastable. They do **NOT** predict which side
*settles*, and they are **not profit by themselves**. Real Polymarket profit requires the **share to be
mispriced** relative to these (now-forecastable) path odds:
```
BET ONLY WHEN:  forecastable_path_odds  vs  market_ask  →  ask is mispriced after costs + buffer
```
The only untested layer that could *make money* is **Polymarket ask underreaction** — does the ask lag the
fair-value/path move we can now forecast? That needs the recorder's ~500 one-entry-per-round
quote+official-outcome observations (accruing; currently ~6 trustworthy). Until then: **path is proven,
profit is not.**

---

## Reproduce
```
python backend/path_plan_verifier.py          # grade the path head's composed plan (calibration + CHOP/TREND)
python backend/probe_first_touch_timing.py    # P(touch) + P(early) predictable; side = coin-flip
python backend/probe_range_expansion.py       # BIG-window + vol-expansion predictable
python backend/probe_post_touch_reversal.py   # post-touch reversal predictable; early reverts 2x
```

## Next frontiers (in order)
1. ✅ **DONE — engine composed live** — `P(early-touch)` trained into the path head (v3, AUC 0.797/0.802) and a composed **FADE-SETUP / RIDE / SKIP / WATCH** `play` surfaced on the card. Direction ensemble untouched. (`PATH_FORECASTER_TRADE_PLAN_HEAD` §Composed Play.)
2. **Live path-plan verifier** — wire grading into `_resolve` for production CHOP/TREND hit-rates (gated).
3. **Ask underreaction** — the profit edge; gated on recorder accrual.
4. **Microstructure / L2 micro-direction (1–30s)** — once `microstructure.duckdb` has weeks of data.
