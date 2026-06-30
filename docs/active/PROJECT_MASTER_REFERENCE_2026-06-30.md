# BTC Quantum Trader — Master Reference (2026-06-30)

The single canonical reference: every model in the app, every experiment (worked + failed, with *why*),
how the system works end-to-end, and all forward plans. Every claim is a measured number. Detailed
per-topic docs are linked inline; this is the index that ties them together.

---

## 0. The one truth everything obeys
**Raw BTC UP/DOWN at 5m/15m is a coin-flip** — live **49.8% over 8,039 resolved rounds** (CI 48.7–50.9),
re-confirmed ~15 ways (tabular, boosted trees, deep sequence, feature sweeps) and across **every cut** this
project tested: every hour, 4-hour block, calendar day, weekday, and timeframe (45–53%). The 150d and 360d
OOS direction backtests are net-negative at every horizon. **You do not beat this with more models on the
same OHLCV.**

The product therefore does **not** bet direction. It wins (if at all) by:
```
predict the PATH / structure / volatility (forecastable)  +  abstain on weak windows
+ calibrated probabilities (P(Hold), touch, round-trip)   +  a strict Polymarket edge gate
```
> **"Better" redefined:** all-round accuracy may stay 50–53% (don't chase it); *acted-decision* quality is
> the target; **profit only when the market ask is mispriced after costs.**

---

## 1. The current model roster (everything live in the app)
**Config:** 360-day training window · 136 raw → **69 selected** features · split **98% fit / 2% holdout**
(conformal heads keep ~20% for calibration) · horizons **{5m, 15m}** only (1m/3m/7m/10m/30m pruned —
no market, coin-flip) · arch `2horizon-5-15` · frozen on this 16GB box.

### Layer 1 — the direction ensemble (FROZEN, confirmation-only)
| Item | Detail |
|---|---|
| Families (7) | XGBoost · LightGBM · CatBoost · HistGradientBoosting · TCN (deep) · LogisticRegression · RandomForest |
| Structure | regime-bucketed (TREND/RANGE/VOLATILE/GLOBAL) + OOF stackers per horizon × regime |
| Output | UP/DOWN/NEUTRAL close direction — **coin-flip, used as confirmation only**, never a trade trigger |
| Tilt fix | symmetric `BTC_DIR_MARGIN_5=0.015` dead-zone neutralizes a measured +34pt 5m UP-lean |

### Layer 2 — specialist heads (the actual value) — `train_heads.py`
| Head | Predicts | Model | Status / metric |
|---|---|---|---|
| **P(Hold) / persistence** | will the side currently ahead hold to close (late-entry) | dual per-horizon iso-calibrated | ✅ **calibrated** — P≥0.93→~95%, P≥0.95→97.2%; ECE **0.0108** / 67,529 rounds. The fair-value source. |
| **Path forecaster v3** | touch $50/$100, round-trip, asymmetric, **early-touch**, net-mag, hi/lo band | ensemble (CatBoost+LightGBM+HistGBM) on 5 vol keepers | ✅ **validated + calibrated** — see §3 |
| **Big-drop keeper** | downside path risk (flush) | 4-model | ✅ strongest risk head — gated AUC ~0.75, top-5% 63.5%; avoid-long flag |
| **Big-move keeper** | will the window be active (timing gate) | 4-model VotingClassifier (LogReg+RF+ExtraTrees+CatBoost), auto $ buckets p75/p90/p97 | ✅ AUC 0.70–0.83 |
| **Activity keeper** | participation / range proxy | 4-model | ✅ confirmation |
| **Directional keeper** | up/down confirmation | 4-model | ⚠️ confirmation-only (top-5% precision low) |
| **Signed-quantile band** | 80% high/low reward-risk zone | CQR conformal, {5,15} | ✅ ~80% coverage |
| **Champion validator** | combines all heads → ACTION + edge gate | rules-first | ✅ strict, edge-gated |
| **Champion-meta** | learns when the champion is wrong | — | ⏳ data-gated (≥500 snapshots) |
| Selectivity / beat / magnitude / fingerprints | supporting | — | noise/data-gated (skip-if-no-signal) |

### Features (69 selected from 136 raw)
OHLCV-derived (rv_15m/30m/60m, compression_ratio, shock_magnitude, range, log_count/vol, accel) ·
**order-flow** (cvd_1m/5m/change, vpin, large_trade_delta/imbalance, delta) · cross-venue (perp_spot_basis,
cvd_spot/perp, vol_spot/perp) · funding_velocity. *Note:* the order-flow features were **dead-zero in live
serving for days** (a real bug, §4) — now fixed; VPIN needs ~1h warmup after each restart (cold-start).

---

## 2. How the system works, end-to-end
```
Live feeds (Binance WS trades/depth · Pyth · cross-venue) + 1m klines
        │
        ▼  feature builder (136 raw → 69 model mask; live order-flow overlay)
        ├──► Direction ensemble  → UP/DOWN (confirmation only, frozen)
        ├──► Specialist heads     → P(Hold) · big-drop · big-move · activity · band · path plan
        │
        ▼  Price-to-Beat tracker (clock-aligned 5m/15m rounds, Pyth anchor; Binance mirror persisted)
        │     · per-tick P(Hold) (time-sensitive) · throttled heads · once-per-round frozen PATH PLAN
        ▼
   CHAMPION validator (rules-first) → ACTION (WAIT/AVOID/AVOID_LONG/WATCH/SETUP/NO_EDGE/PAPER_BET)
        │                              + the path PLAY (FADE-SETUP/RIDE/SKIP/WATCH)
        ▼
   THE EDGE GATE:  fair_value(P(Hold)) − market_ask − costs − buffer > required_edge   → the only path to a bet
        │
        ▼  card (UI) + champion_snapshots (DuckDB) → meta-trainer later
```
**Two record-forward recorders run alongside** (separate DuckDBs, no lock conflict): the **Polymarket
recorder** (quotes + official CLOB/Gamma settlement → the make-or-break edge table) and the **microstructure
recorder** (L2 OFI/microprice/depth + cross-venue lead-lag → the only untested ceiling-break data).

---

## 3. WHAT WORKED (validated out-of-sample, with numbers)
| Signal | What | Evidence (5m / 15m) | Probe / head |
|---|---|---|---|
| **Path: touch odds** | P(move ≥$50/$100) | AUC 0.795/0.799 · 0.837/0.786; **calibrated** (pred 0.659↔0.665, 0.906↔0.914) | path head, `path_plan_verifier.py` |
| **Path: round-trip** | P(touch both ±$50) | AUC 0.851 / 0.758 | path head |
| **Path: CHOP/TREND style** | fade-vs-ride | CHOP round-trips **24%/48%** vs quiet **0.5%/1.7%** (28–48× separation) | verifier |
| **Path: early-touch (v3)** | ±$50 in first half | AUC **0.797 / 0.802** | `probe_first_touch_timing.py` |
| **Window selection** | BIG window (top-⅓ range) | AUC **0.843 / 0.832**; range autocorr 0.65 | `probe_range_expansion.py` |
| **Post-touch reversal** | reversal after touch | AUC **0.634 / 0.586**; early touches revert **~2×** | `probe_post_touch_reversal.py` |
| **High/low band** | conformal range | coverage **0.50 / 0.48** (nominal 0.50) | path head |
| **P(Hold)** | side-holds | ECE 0.0108; P≥0.95→97.2% | persistence |
| **Big-drop / big-move / activity** | risk + timing | gated AUC 0.70–0.83 | keepers |

**The composed engine (live, v3):** window-select → CHOP/TREND → P(touch)+P(early) → **FADE-SETUP/RIDE/SKIP**.
A genuine **within-window fade-vs-ride + timing** edge — the path is predictable where direction is not.

**Faint / conditional:** regime selection — RANGE 59% (Wilson-LB 52.3), LOW_VOLATILITY 63% — but the
recent-window LB drops to 46.3%, so it stays in **shadow**, not promoted. Evening (20:00–24:00 CEST) on 15m
recurs 7/8 days but is selection-biased (watch, don't trade).

---

## 4. WHAT DIDN'T WORK (and WHY) — the stop-list
| Tried | How | Result | **Why it failed** |
|---|---|---|---|
| **Raw direction** (close UP/DOWN) | every model family, 136 features, 360d | ~49.8% live, AUC ~0.50 | information ceiling on OHLCV; no new info in candles |
| **Which-side-touches-first** | classify up-first from keepers | AUC 0.518/0.519 | it's a direction question → coin-flip |
| **Direction by hour / day / weekday** | DuckDB + CEST time-of-day analysis | 45–53% everywhere, all Wilson-LB <50% | no clock/calendar structure in direction |
| **Triple-barrier model** | upper/lower/timeout labels, 5 libraries, top-N precision + profit-after-spread | AUC 0.65–0.69 but **net-negative after 2bps spread** every horizon | AUC is mostly vol-detection (will *a* barrier hit), not *which* — directional precision can't clear costs |
| **Flow / cross-venue proxy** | candle-only vs +cvd/vpin/basis on the touch label | no top-bucket lift (0.675→0.673) | the order-flow we already have adds no directional signal (true L2 needs record-forward) |
| **Meta-skip model** | predict "acted side holds" from heads | top-decile 97.6% hold | circular — uses `p_hold` as a feature; re-expresses calibration, not new edge |
| **Fallback-abstain in TRENDING** | shadow-replay abstaining fallback leans in trends | retained LB <50%, **harmful at 15m** (cut a 65% set) | the live "fallback 0/4" card that triggered it was small-sample noise |
| **Deep sequence promotion** (PatchTST/iTransformer/TCN-as-primary) | research lanes | research-only, not promotable | same ceiling — sequence models don't beat tabular on direction |
| **Exact-price prediction** | regress the close | naive "≈ now" baseline wins | price is a near-random-walk at 5–15m |
| **Confluence grade (A/B/C)** | flow-agreement + regime | was inverted (A<C<B) | UNKNOWN-era contamination + stale regime check; fixed to regime-first, but still non-discriminating on *direction* (it's a label, not a gate) |
| **1m/3m/7m/10m/30m horizons** | full multi-horizon | pruned to {5,15} | no Polymarket market + coin-flip; ~55–70% training cost for nothing |

**Stop-list rule:** do not relitigate direction in any disguise. More models / more data / bigger
architectures on the same candles will not move it.

---

## 5. The make-or-break (gates ALL real money)
```
BET ONLY WHEN:  calibrated_fair_value(P(Hold)) − market_ask − costs − safety_buffer > required_edge
```
Everything above sharpens the *inputs*; none is profit by itself. Answered only by the **Polymarket
recorder** (official CLOB/Gamma settlement — **364 outcomes ingested**, but only ~**4–6 joined quote+outcome
rounds**). **Need ~500 one-entry-per-round observations** before any profit claim. The path engine's
FADE-SETUP is +EV only if the cheap share is mispriced vs the reversal odds — until the recorder proves it,
**every play is PAPER.** Positive after costs → paper → micro-live → scale (each gated). Flat → ship the
honest dashboard. Both are acceptable, truthful outcomes.

---

## 6. Major bugs found & fixed (the discipline working)
| Bug | Impact | Fix |
|---|---|---|
| **Microstructure dead-zero** | cvd/vpin/large-trade fed the model **0** live (trained on real values) | live-overlay broadcast of the snapshot; `of.get()` fallbacks |
| **Path plan recomputed mid-round** | unstable plan vs opening anchor | frozen once at open; late-anchor rounds skipped |
| **bps≈$ approximation** | $50/$100 wrong as BTC repriced | exact per-row dollar labels (`threshold_units=usd`) |
| **Recorder manufactured late anchors** | 24 corrupt rounds polluted ROI | 5s anchor window + pre-discover next slug + token-by-outcome-name |
| **Recorder couldn't find the live round** | mispricing dataset stayed empty | fetch the deterministic live slug directly each cycle |
| **P(Hold) join mixed s/ms + future leak** | analyzer join failed | ms-normalize + backward-only as-of (5s tol) |
| **Model save swallowed disk errors** | "complete" marker on a failed save | atomic `os.replace` + raise-on-failure |
| **auto_finetune ran 60d, not 360d** | recalibrated heads on wrong window | `BTC_HISTORICAL_DAYS or 360` |

---

## 7. All plans (forward roadmap, gated)
**Three levers — the only things that can move the ceiling:**
1. **New information (record-forward):** L2 order book (microprice/OFI/depth) + cross-exchange lead-lag —
   **recording now** via the microstructure recorder; probe in 2–4 weeks. Liquidations/OI; Polymarket ask history.
2. **New targets:** path/touch/round-trip/early-touch ✅ done. Line-cross flip-risk; profitable-after-costs meta-label.
3. **New policy:** regime abstention (shadow) · evening-15m watch · bucket-quality gating · the edge gate.

**Near-term, in order:**
1. **Polymarket recorder accrual** → the edge table (make-or-break). *Everything waits on this.*
2. **Live verifiers** — wire path-plan grading + the post-touch reversal flag into `_resolve` (gated; touches live path).
3. **Microstructure probe** — once 2–4 weeks of L2/cross-venue data: does it lift the top buckets over candle-only?
4. **Ask-underreaction test** — the only untested *profit* edge: does the ask lag the fair-value/path move?
5. **Champion-meta** — train once ≥500 resolved snapshots accrue.

**The "Netflix" end-state vision** (`MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER`): similar-setup memory
(kNN) + online calibration + regime-weighted head selection + a paper-only contextual bandit — layered on
the heads, never merged into the frozen direction model.

---

## 8. Honest bottom line
- **Direction is dead** and will stay dead on this data — proven exhaustively.
- **Path / volatility / structure / speed are predictable** (AUC 0.63–0.91, calibrated) — that's the real,
  measured win, and it's now composed live (the path engine + FADE-SETUP play).
- **Profit is unproven.** The path engine sharpens *how* you trade a window (fade-vs-ride, selection,
  timing); whether it *makes money* still requires the share mispriced vs the odds — gated on the recorder.
- If the recorder edge is flat after costs, the truthful product is a **world-class probability/risk +
  path dashboard** — still the best tool achievable on this data. That is an acceptable outcome.

---

## 9. Document map (authoritative per topic)
| Topic | Doc |
|---|---|
| Forward strategy / 3 levers / Netflix vision | `MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md` |
| What's predictable vs dead (5 validated layers) | `PREDICTABILITY_MAP_2026-06-30.md` |
| Path forecaster v3 (head design + composed play) | `PATH_FORECASTER_TRADE_PLAN_HEAD_2026-06-30.md` |
| Latest full code/logic audit | `CODE_AND_LOGIC_VALIDATION_2026-06-30.md` |
| Microstructure parity bug + fixes | `MICROSTRUCTURE_PARITY_BUG_AND_FIXES_2026-06-28.md` |
| Ceiling-break experiments (triple-barrier/flow/meta-skip) | `CEILING_BREAK_EXPERIMENTS_2026-06-20.md` |
| Direction coin-flip by day/version/horizon | `DUCKDB_METRICS_ANALYSIS_2026-06-21.md` |
| Timeframe / time-of-day / per-day | `TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md` |
| Regime / fallback shadows (negative results) | `REGIME_GATE_SHADOW_2026-06-21.md`, `FALLBACK_ABSTAIN_SHADOW_2026-06-21.md` |
| 360d retrain + settlement + bot requirements | `FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md`, `SETTLEMENT_INGESTION_2026-06-21.md`, `POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md` |
| 100-idea research backlog | `QUANT_RESEARCH_100_CEILING_BREAK_IDEAS_2026-06-30.md` |

**Reproduce the validated signals:** `path_plan_verifier.py` · `probe_first_touch_timing.py` ·
`probe_range_expansion.py` · `probe_post_touch_reversal.py` · `analyze_timeframe_performance.py`.
