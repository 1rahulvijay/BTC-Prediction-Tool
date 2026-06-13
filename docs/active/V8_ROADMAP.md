# V8 Roadmap — positioning + microstructure information, tuning, the precision tier (2026-06-13)

Successor to v7. v7 (136 features) banks class-balance + full CVD/VPIN/large-trade backfill +
trend-persistence + `variance_ratio`/`rv_term_structure`/session flags + the per-model metric fix +
A1 `P(hold)` live. That's a real but **incremental** gain — still mostly price/volume-derived features
with the L2/options slots constant in training. V8 is where the *ceiling* gets attacked.

## Guiding principle (non-negotiable)
The ceiling is broken by **new information the market hasn't priced** + **selectivity/discipline** —
NOT by indicator count. At 136 features, adding more price/volume transforms = overfit risk, not edge.
Every V8 item is either a genuinely new *information source* or a *precision-tier* mechanism. Each
enters as a challenger and earns its place on **purged walk-forward sign-truth**, one change at a time.

## Triage of requested concepts (operator list, 2026-06-13)
- **Already in the matrix:** CVD divergence/delta/delta-flips (`cvd_delta_divergence`,
  `cvd_slope_divergence`), volume profile + low-volume nodes (`volume_profile_*`), absorption
  (`absorption_ratio/persistence`), exhaustion (`exhaustion`). No action — present.
- **Real new info → V8:** options positioning suite (GEX ✅ recording → feature; DEX dealer-delta;
  0DTE intraday gamma/pin levels from Deribit daily expiries; 25Δ skew; put/call), initiation/auction
  (aggressive vol at extremes, from aggTrades), A4 spot-perp/cross-venue divergence, L2 depth.
- **Weak / unproven (test ≤1, low priority):** ICT/SMC — fair-value gaps (FVG), inverse-FVG (IFVG),
  "state of delivery". Narrative-heavy, edge unproven at 5m, overfit-prone. If tested, FVG as a single
  quantified feature (3-candle imbalance) on the held-out scorecard — adopt only if it beats incumbent.
- **Rejected (honesty):** lunar/moon cycles — no credible predictive edge; pure noise the trees would
  overfit. Not in V8, not later.

## V8 themes (priority order)
1. **OPTIONS POSITIONING suite — the standout new information.** Non-price, non-L2 → actually
   learnable, unlike the constant L2 slots. GEX (recorder live → add the feature slot once
   `gex_live` has history), DEX (dealer delta exposure), **0DTE gamma** (Deribit daily expiries →
   intraday pin/repel levels — the strongest intraday options signal), 25Δ skew, put/call ratio.
   Live-accumulation (like B1) or a Deribit history/vendor backfill.
2. **MICROSTRUCTURE / cross-source.** A4 spot-vs-perp divergence — bridge the live `perp_cvd_live`
   recorder into the signal_history buffer + a `candle_ts` overlay, THEN add the slots (parity-safe).
   L2 depth slope / multi-level OBI (live B1 accumulation, or Tardis.dev L2 archive for history).
   Initiation/absorption refinements from aggTrades.
3. **TUNING — A7 Optuna.** Per-horizon hyperparameter search on purged walk-forward sign-truth.
   No new data; highest-EV pure-tuning lever. Runs offline/overnight.
4. **PRECISION TIER.** A10 setup fingerprints + the kNN voter (decorrelated stacker seat, gated on
   lift) + the T3 Wilson-lower-bound gate (n≥100, ≥80% LB). A1-ext path labels
   (UP_THEN_DOWN/…). **OOF warm-start** of calibration/meta/signal_history (kill post-retrain dormancy).
5. **CONTINGENT — transformer.** ONLY if v7/v8's TCN shows decorrelated stacker lift on 136+ features.
   A small patch-attention encoder into the EXISTING stacker — never a parallel ensemble. If TCN
   shows nothing → sequence models don't pay at this data scale; no transformer.

## Specialized OFFLINE heads suite — ✅ ALL BUILT 2026-06-13 (trainers + noise gates + start.bat)
Status: `train_beat_classifier.py`, `build_path_labels.py`, `train_magnitude_quantiles.py`,
`build_fingerprints_historical.py` all built, core-tested, wired into start.bat (`[0/3] d.`, train-if-
missing). Each self-validates and refuses to save a noise horizon. NEXT: run them over the full
60–90d window ("train hard"), then wire P(beat)/path/magnitude outputs into the Polymarket card +
the T3 gate (serving — like P(hold) already is). Original plan below.


The A1 persistence head proved the pattern: train a focused classifier offline on reconstructed
history, ensemble it in. Buildable now (no live uptime), each answers ONE betting question:
- **Beat classifier** ⭐ — per-horizon binary `P(close ≥ clock-window-open)` (the EXACT Polymarket
  settlement framing — strict up/down, no NEUTRAL, clock-aligned). Trained on historical klines+
  features. Calibrated → **this IS the proper `p_up` / fair value** (A2 re-anchored to a real model,
  not the rejected flat-magnitude formula). Honest caveat: it won't beat the information ceiling
  (same 5m problem, cleaner label) — its value is the exact betting framing + a calibrated probability
  + a clean second opinion to ensemble. New trainer `train_beat_classifier.py`, builder pattern.
- **Path labels (A1-ext)** — per-window first-passage SHAPE (UP_THEN_DOWN / DOWN_THEN_UP / DIRECT /
  CHOP) from tick aggTrades → a learned path classifier (replaces the heuristic outlook).
- **Magnitude quantiles (C3)** — q10/q50/q90 of |move| per horizon×regime×vol from klines → the
  vol-aware "how far" band (kills the flat ~$40).
- **Historical setup fingerprints (A10)** — derive from the historical feature matrix + outcomes
  (the live `setup_fingerprint` recorder is the going-forward twin).
All four + persistence = a "specialized heads" layer feeding the one decision stack. Each enters as a
challenger, measured on held-out sign-truth.

## Explicitly NOT in V8
- Lunar cycles (astrology), more price-derived TA (diminishing returns at 136), parallel ensembles
  (extend the one stacker), the betting layer (fair value/penny-sniper — only after 5m precision proven).

## Sequencing from v7
1. **Let v7 finish (~2–3h).** Ignore inflated 1m/RANGE OOF (NEUTRAL base rate, not skill).
2. **Measure** the 24h sign-truth scorecard. Gate: **5m committed-lean ≥ ~56–60%, UP/DOWN within ~8pts.**
3. **V8 bundle, one lever at a time, each measured:** A4 (bridge perp) → A7 Optuna → options suite as
   features → A10/kNN/T3. OOF warm-start folds into whichever retrain runs next.
4. Betting layer stays deferred until the precision gate is cleared.
