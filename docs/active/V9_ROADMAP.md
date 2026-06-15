# V9 Roadmap + Session Ledger (2026-06-13)

Comprehensive record of the 2026-06-13 work: what was built/modified, what was deliberately NOT
implemented (and why), and the forward path V7 → V8 → V9. Companion to [V8_ROADMAP.md](V8_ROADMAP.md)
(detailed V8 themes) and the change-audit [V3_CHANGES_AND_AUDIT.md](V3_CHANGES_AND_AUDIT.md) §5ba–§5cc.

---

## PART 1 — What was built / modified this session

### Grading & measurement fixes (no retrain)
- **Per-model accuracy sign-truth fix** (`model_verifier.py`, §5ba) — graded NEUTRAL abstentions as
  misses → fake ~5%; now committed-only by strict sign (~40–55% real). Activates after a restart.
- **price-to-beat docstrings** corrected (Chainlink/Binance → Pyth).
- Earlier-session sign-truth fixes across calibration / analytics / regime-quality (context).

### Live recorders (no retrain — start the data clocks; activate on restart)
- **B1** `feature_outcome_log` — full live feature vector per cycle (enables the L2/microstructure retrain).
- **A1** `persistence_snapshot` — intra-window round trajectory (feeds the P(hold) head).
- **A4 perp** `perp_cvd_live` — live perp-CVD, parity-verified vs the offline builder.
- **A10** `setup_fingerprint` — per-prediction decision context (regime/conviction/grade/CVD/GEX);
  the kNN/T3 evidence layer + "measure-before-gate" substrate.
- **GEX** `gex_live` (other session) — dealer gamma.

### Serving (no retrain)
- **B2 conviction-gate** — 50–54% coin-flip cells keep the read but lose `actionable`.
- **Binance · Price-to-Beat tab** — Binance-anchored mirror (in-memory tracker, parameterized
  `renderPMCore` so the Pyth tab is byte-identical).
- **P(hold) wired live** (other session) — ⚡ late-entry gated on calibrated P(hold) ≥ 0.93.

### Offline data builders + specialized heads (all historically trainable, noise-gated)
- `build_persistence_dataset.py` — A1 data (validated: late-entry 90–97% held).
- `build_crossvenue_flow.py` — A4 spot-vs-perp divergence (validated).
- `train_beat_classifier.py` — **P(beat)** = fair value / `p_up`.
- `build_path_labels.py` — path shape (UP_THEN_DOWN / DIRECT / CHOP).
- `train_magnitude_quantiles.py` — q10/q50/q90 of |move| (kills flat $40).
- `build_fingerprints_historical.py` — "similar setups: n·success%" evidence.
- `diagnose_model.py` — feature/model NOISE diagnostic.
- All share `resolve_dates()` (`--start/--end | --validate | --days N`).

### v7 feature bundle (RETRAIN — other session)
- Slots 130–135: `variance_ratio`, `rv_term_structure`, `session_asia/eu/us`, `is_weekend`.
  `MODEL_ARCH_VERSION` → `v7-…-136` → auto-retrains on next boot.

### Infra & docs
- `start.bat`: `BTC_BACKFILL_DAYS` knob + 3 data builders + 4 head trainers (train-if-missing).
- Docs: MEASUREMENT_WINDOW, SPEC_ACCURACY_NEXT_RETRAIN, IMPLEMENTATION_QUEUE, NEXT_STEPS_AND_STRATEGY,
  DATA_COLLECTORS, RETRAIN_RUNBOOK, V8_ROADMAP, betting-guide section, change-audit §5ba–§5cc.

### Diagnostic findings (real, from the DB / training logs)
- **Honest model health (v6-era, sign-truth):** 3m 50.8% · 5m 50.0% · 7m 46.8% · 10m 55.6% (only
  edge) · 15m 46.2%. Coin-flip at 5m — confirmed three ways.
- **★ Confluence grade is INVERTED (§5br) — MEASURED, n=222–397/grade.** Grade A 44.3%
  (Wilson-LB 38.7%) < Grade C 50.4% < Grade B 56.8%. "Highest agreement" is the WORST (exhaustion →
  reversal). Grade letters are NOT a trust signal until A≥B≥C, each n≥100, A's LB > C's rate.
  V8 action: rebuild the grade regime-conditioned + maturity-aware (A10).
- **★ Label leakage caught + fixed in the 4 at-open heads (§5bs).** Beat printed h=1 AUC 1.000 —
  features read the current bar's close while the label anchored on its open. Fixed (features → t+1
  window; h=1 AUC 1.000→0.548). The v7 ensemble + P(hold) were NOT affected (forward-only / intra-
  window label designs). The leaked head artifacts must be deleted + rebuilt before wiring.
- **Low-signal features (v7 SHAP):** `price_return, fv_deviation, twap_deviation, price_vs_sma50,
  eth_btc_price_ratio, rv_15m` — redundant; model leans on `atr_norm`.
- **Dead weight (architectural):** the live-only constant-in-training slots (L2 ~52–72, options,
  polymarket 105–108) — B1 will confirm by variance once it has ≥200 rows (had 73).

---

## PART 2 — What we did NOT implement, and WHY

| Not done | Why (honest) |
|---|---|
| **Wire P(beat)/path/magnitude into the card** | First build LEAKED (§5bs — features read the outcome bar's close), so the saved `.pkl`s are invalid. Must DELETE + rebuild leak-free, then wire/verify against the corrected `.pkl`s. P(hold) was wired AFTER validated training for this exact reason. |
| **Volume-profile overlay (#4)** | Deprioritized (frontend, operator-context only). The diagnostic + heads were higher value. Still queued. |
| **GEX / exhaustion / CVD as live GATES (#1/#2/#3)** | **Measure-before-gate discipline.** Gating on unproven signals = overfitting to noise. The `setup_fingerprint` recorder must first SHOW these have edge (join to outcome) before any becomes a gate. |
| **0DTE / DEX / skew recorder (#6)** | Touches `institutional_feeds.py` — the *other session's* file → concurrency-clobber risk. Deferred until single-session. |
| **A9 — Polymarket crowd price as a feature** | BLOCKED by data: the accessible API only returns long-dated markets; the 5m up/down market is geo-blocked on this box. No real 5m crowd price to record. |
| **A4 cross-venue AS A FEATURE** | Needs the perp→signal_history-buffer bridge + a `candle_ts` overlay for parity (the recorder logs `ts_ms` to a side table). Without it the column is constant over history = train/serve skew. Recorder built; feature deferred. |
| **Moon / lunar cycle** | REJECTED — no credible predictive edge; pure noise the trees would overfit. Honesty over agreement. |
| **ICT FVG / IFVG / "state of delivery" as features** | Narrative-heavy, unproven at 5m, overfit-prone. At most a chart OVERLAY (operator context), never fed to the model speculatively. |
| **Transformer / new parallel ensemble** | Data-hungry at ~46k samples → overfits. Gated on v7/v8's TCN showing decorrelated stacker lift first. And it extends the ONE stacker, never a parallel app. |
| **A7 Optuna · kNN voter · OOF warm-start · L2 increment** | Specced; all are *retrain-coupled* (only exist during a train) → bundled into the V8 retrain, not built piecemeal. |
| **Betting layer (penny-sniper A11, pair-arb A12, exit hints A13)** | Deferred until 5m committed precision is proven (≥56–60%). Pricing a coin-flip prices noise. |
| **git init / checkpoint** | Operator declined. (Risk noted: concurrent two-session edits on a non-git repo can clobber.) |

---

## PART 3 — The path: V7 → V8 → V9

### V7 (training now) — FOUNDATION
136 features (kline/time-derived adds), class-balance, ATR labels, per-model fix, TCN full seat.
**Expectation: incremental — ~50–52% at 5m, not a jump.** Its job is to set the stage; the usable
edge today is the **P(hold)** late-entry tier. Validate on the 24h sign-truth scorecard (gate: 5m
≥ ~56%, UP/DOWN balanced).

### V8 — INFORMATION + HEADS + PRECISION TIER (specced, mostly built)
0. **Rebuild the 4 heads leak-free FIRST** (delete the leaked `.pkl`s; §5bs) — then everything below.
1. **Wire the 4 heads** (P(beat)/path/magnitude/fingerprints) into serving + ensemble.
2. **Information features** that break the ceiling: options positioning (GEX/DEX/0DTE/skew),
   A4 cross-venue (bridge perp→buffer), L2 depth (live B1 or Tardis archive).
3. **A7 Optuna** tuning + **OOF warm-start** (kill post-retrain dormancy).
4. **T3 precision tier**: A10 fingerprints (+ **rebuild the inverted confluence grade**, §5br) →
   kNN voter → Wilson-lower-bound gate (n≥100, ≥80% LB).
5. **A1-ext path labels** live.

### V9 — CEILING BROKEN · BETTING PRODUCT LIVE · SELF-IMPROVING
The horizon beyond V8 — when the pieces compound into a product:
- **The information ceiling addressed.** Options-positioning + L2 microstructure are TRAINED (not
  constant), so 5m direction has a *real* edge (target: sustained 55%+ on the bettable subset), and
  the gated T3 tier reliably prints 90–95% on a few calls/day.
- **The betting product is live and gated on proven precision.** P(beat) fair value + value-betting,
  then A11 penny-sniper (calibrated tail probabilities) and A12 pair-arb detector — each promoted
  only after paper-tracking shows positive EV. The product *makes money*, not just predictions.
- **Self-improving loop.** Daily fresh retrains (GPU), the noise diagnostic auto-pruning dead
  features, the warm-started calibration/meta active from boot, the recorders continuously widening
  the trainable feature set (L2, options, cross-venue) — the model improves without manual surgery.
- **Architecture maturity (contingent, measured):** a small attention encoder IF TCN proved out;
  multi-asset extension (ETH/SOL) reusing the same stack; a Deribit/options history backfill so the
  positioning features get a deep training set.
- **Standing rule, all the way to V9:** nothing ships because it sounds advanced. Every feature,
  head, and gate enters as a measured challenger and earns its seat on held-out sign-truth. The docs
  stay forensic; honest measurement is the moat.

**The one-line path:** V7 lays the foundation → V8 adds the information and the precision tier →
V9 is when that information breaks the ceiling, the betting layer goes live on proven precision, and
the whole thing self-improves. Each step gated on a real number, never a hope.
