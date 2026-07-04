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
| 3 | **Activity / speed** | P(touch ±$50) — *will price move $50 at all* | touch AUC **0.85 / 0.91** (magnitude; calibrated below) | `probe_first_touch_timing.py` |
| 4 | ⚠️ ~~Fade / round-trip~~ **RETRACTED — leaked** | early touch that reaches anchor | the "$30 top-10% **77%**" was a **1m touch-candle look-ahead leak** (80.6% resolve in-candle). **Causal = ~42% win, below 50% breakeven, NOT deployable**; fade disabled live. See `ANCHOR_ROUNDTRIP_180D_RESULTS`, `PROFITABILITY_AND_BETTING_VALIDATION` |
| 5 | **Risk / hold** | big-drop downside path · P(Hold) late-entry | big-drop gated AUC ~0.75; P(Hold)≥0.93 → ~95% realized | live keeper heads |

> **⚠️ RETRACTION (2026-07-01).** The former Layer 4 — "post-touch reversal AUC 0.63/0.59, early touches revert
> **~2×** (28%→13%), early-touch AUC 0.75/0.83" — was **FALSE**. Two bugs: `probe_fade_entry_exit.py` had
> `early = tm > half` **inverted** (a *late* touch), and a settle-generous grade scored any $1 tick off the level
> as a win. Strict grade ($50, 5m): earliest-quartile reach-anchor **41%**, latest **6.9%**, overall **29.5%**.
> **Early touch is necessary but not sufficient** (timing alone ~48%, still a loser); the *touch-context* lifts
> 41%→~69%. The fade model was retrained on the honest strict label (`2026-07-01-fade-v3-strict-honest`) and the
> live gate now requires early touch **AND** model P(reach-anchor) ≥ 0.55. Round-trip frequency by barrier:
> $20 **39.6%/62.6%**, $30 **27.9%/52.1%**, $50 **13.9%/35.1%** (clean both-legs-to-anchor ~30%). PAPER until
> the recorder proves after-cost.

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
3. ACTIVITY/SPEED  P(touch) high            → expect a $50 touch (magnitude, calibrated)
4. FADE GATE       early touch AND P(reach-anchor)≥0.55 → fade the extreme ONLY when the honest model grades it high
                   (late touch → never fires: 6.9% reach anchor; early alone → only ~41%)
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
python backend/probe_first_touch_timing.py    # P(touch) $50 magnitude predictable; side = coin-flip
python backend/probe_range_expansion.py       # BIG-window + vol-expansion predictable
python backend/train_fade_model.py            # honest strict-label fade / reach-anchor model (v3-strict-honest)
python backend/probe_fade_features_ab.py      # A/B: timing-only vs +touch-ctx vs +research on the HONEST label
# NOTE: probe_post_touch_reversal.py / probe_fade_entry_exit.py held the inverted-label myth — superseded above
```

## Next frontiers (in order)
1. ✅ **DONE — honest fade gate composed live** — retrained on the strict reach-anchor label
   (`2026-07-01-fade-v3-strict-honest`, AUC ~0.80/0.73, top-decile → anchor ~69%); the live gate fires FADE only
   on **early touch AND model P(reach-anchor) ≥ 0.55** (late touch never fires). Supersedes the retracted
   "early-touch AUC 0.797/0.802 / reverts 2×" story. Direction ensemble untouched. (`PATH_FORECASTER_TRADE_PLAN_HEAD` fade section.)
2. **Live path-plan verifier** — wire grading into `_resolve` for production CHOP/TREND hit-rates (gated).
3. **Ask underreaction** — the profit edge; gated on recorder accrual.
4. **Microstructure / L2 micro-direction (1–30s)** — once `microstructure.duckdb` has weeks of data.
