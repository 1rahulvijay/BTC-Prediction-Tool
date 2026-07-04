# V10 Consolidated Master — Version Lineage, Architecture & All Proposals (2026-06-18)

Single consolidation of **every version (V3 → v11), the current architecture, and every proposal**
(built, deferred, gated, or rejected). Supersedes the scattered roadmaps as the *index*; the detailed
docs remain authoritative for their topic and are linked inline.

> **North star (unchanged since VNEXT, now proven at 150 days):** the product does **not** win by
> forecasting BTC up/down — that is a coin-flip at the information ceiling, confirmed ~15 ways incl.
> n=5,829 live leans and the 150d retrain (anti-predictive at 30m). It wins by **calibrated conditional
> probabilities** (P(Hold), big-drop, big-move, range, activity) + **strict abstention** + a single
> trade rule: `calibrated_fair_value − market_ask − costs − buffer > required_edge`. The model is not the
> edge; the edge exists only if calibrated fair value beats a tradeable price after costs.

---

## 1. Version lineage (what each delivered)
| Version | Theme | Delivered | Status |
|---|---|---|---|
| **V3 / V5** | accuracy plumbing | feature set, class-balance, holdout discipline, conformal bands | folded in |
| **V7** | 136-feature ensemble | full CVD/VPIN/large-trade backfill, trend-persistence, variance-ratio/session flags, **A1 P(hold) live** | superseded by v11 |
| **V8** ([roadmap](V8_ROADMAP.md)) | attack the ceiling with **new information** | triage of options/GEX, L2 depth, cross-venue, initiation/auction; precision-tier mechanism; "ceiling broken by new info + selectivity, not indicator count" | partly mined → heads |
| **V9** ([roadmap](V9_ROADMAP.md)) | recorders + grading truth | B1 `feature_outcome_log`, A1 `persistence_snapshot`, A4 `perp_cvd_live`, A10 `setup_fingerprint`; sign-truth grading fixes | shipped (data clocks) |
| **V10** ([roadmap](V10_ROADMAP.md)) | keeper run + restart discipline | 60–90d keeper bundle, leak-free head rebuild, **confirmed coin-flip ceiling** (bakeoff ~0.50–0.54) | shipped |
| **v11** (model arch) | pruned, parity-safe, multi-horizon | **136→69 features** (KEEP+PARITY-FIX), 7-horizon (1–30m), regime-bucketed, split-0.98, class-balanced; **frozen** | **current ensemble** |
| **VNEXT** ([vision](VNEXT_BREAK_THE_CEILING_VISION_2026-06-15.md)) | stop fighting direction | Anchor-Beat probability engine + Polymarket mispricing + champion/challenger | realized as heads+champion |
| **Bot master** ([spec](POLYMARKET_BOT_MASTER.md)) | the one rule | edge-gated agent: bet only when `fair − ask − costs − buffer > required_edge`; paper-only until proven | doctrine |

---

## 2. Current architecture (what runs today)
```
Live BTC + Polymarket → feature builder ─┬─ frozen v11 direction ensemble (confirmation / abstention only)
                                         ├─ P(hold) keeper        (calibrated, fair_value source)
                                         ├─ big-move keeper       (timing gate, per-horizon, $ buckets)
                                         ├─ big-drop keeper        (downside path risk)
                                         ├─ directional up/down    (confirmation only, weak)
                                         ├─ activity/range         (participation proxy)
                                         ├─ signed-quantile band   (80% CQR reward/risk room)
                                         └─ regime + feed health
                                              ↓
                            CHAMPION decision validator (rules-first, strict)
                            → ACTION + confidence + plain reason + risk flags + invalidate + zone
                            → bet candidate ONLY when a live ask clears: fair − ask − buffer
                                              ↓
                            DuckDB champion_snapshots → (later) learned meta-champion reject
```
All heads are **horizon-aware (1,3,5,7,10,15,30m)** and **isotonic-calibrated**; the window is one knob
(`BTC_HISTORICAL_DAYS` → matrix rebuild → every head, each on a 98/2 split). Detailed evidence:
[150d retrain results](OVERNIGHT_150D_RETRAIN_RESULTS_2026-06-18.md), [specialist-head implementation](SPECIALIST_HEAD_CHAMPION_IMPLEMENTATION_2026-06-17.md),
[validation ledger](CLAUDE_ENHANCEMENTS_AND_VALIDATION_LEDGER_2026-06-17.md).

---

## 3. All proposals — by priority tier (the forward roadmap)
> The expanded forward strategy (ceiling-break levers + the Live Market Recommender architecture) now
> lives in **[MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md](MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md)**.
> The tiers below remain the near-term build order; the master strategy is the bigger picture.

### Tier 0 — the make-or-break (nothing else matters more)
- **Run the Polymarket recorder persistently → `analyze_recorder_edge.py`.** Every head is an *input*
  to one gate with **364 official outcomes but only 4 joined quote rounds**. This is the fork between "world-class dashboard"
  and "real bot." Until the `fair − ask − buffer` table is positive after costs, do not build execution.
  **Status: open — needs uptime through live windows (≥30 resolved; ≥500 to tune).**

### Tier 1 — prediction *quality* (calibration > accuracy)
| # | Proposal | Status | Retrain? |
|---|---|---|---|
| 1 | **Live calibration monitor + recalibration overlay** (`calibration_monitor.py`) | ✅ **BUILT (this session)** | none |
| 2 | **Learned meta-champion** (`train_champion_meta.py`) — learns when the rules-champion is wrong | ⏳ gated on ≥500 resolved snapshots | trivial (seconds), auto |
| 3 | **Bucket report shows iso-calibrated ECE** (`head_probability_buckets.py`) | ✅ **BUILT (this session)** | none |
| — | per-regime isotonic (optional; heads' 4 feats are already vol-regime proxies) | proposed | cheap heads-only |
| — | warm-start live order-flow buffers from backfill at boot (kills the 4.2% cold-start) | proposed | none (serving) |

### Tier 2 — new predictions worth adding (asymmetric)
- **Downside playbook** — big-drop is the one real directional-risk signal (held-out 0.64–0.665):
  `high drop-risk + DOWN confirm + range supports → short setup`, validated on recorder data.
- **True future-volume head** — activity is currently a high/low-range proxy (strongest head, 0.78–0.86);
  add a real volume label for a genuine "does the move have fuel" filter.
- **Per-regime conformal bands** — signed-quantile is global 80% CQR; regime-conditional once data allows.

### Tier 3 — execution realism (GATED behind Tier 0 proving edge)
Latency-aware edge persistence, depth/slippage-aware sizing, then paper-agent → micro-live → scale.
**Do not build before the recorder edge table is positive after costs.**

---

## 4. Built this session (2026-06-18)
- **Calibration monitor** ([calibration_monitor.py](../../backend/calibration_monitor.py)) — joins
  `champion_snapshots ⋈ price_to_beat` (read-only) and measures live P(hold) reliability. **First run
  on 13,972 resolved rounds found a real ~2-pt optimism at the top tiers** (P≥0.93 claims 96.3%,
  realizes **94.0%**; P≥0.95 claims 97.2%, realizes **94.9%**) — overall ECE 0.033, verdict STABLE but
  edge-of-tolerance. `--recalibrate` emits a P(hold) iso overlay (NOT auto-applied; opt-in). Report:
  [CALIBRATION_MONITOR_2026-06-18.md](CALIBRATION_MONITOR_2026-06-18.md).
- **Bucket-report iso fix** — `head_probability_buckets.py` now applies isotonic before computing ECE,
  so the report shows the **served** calibration, not the raw rank score (the old ~0.18 was pre-iso).
- **Single days-knob + 98/2 split** (prior step, confirmed on the 150d run) — `BTC_HISTORICAL_DAYS`
  rebuilds the matrix and retrains every head with an honest 2% held-out test.

**Actionable finding for the operator:** because `fair_value = P(hold)`, the ~2-pt live optimism means
the edge gate is currently slightly generous at the top tier. The fix is opt-in live recalibration
(overlay already producible) — review the monitor report, then decide whether to apply.

---

## 5. The stop-list (do NOT rebuild — proven dead)
- ❌ More direction ensembles / sequence models (PatchTST/iTransformer) — coin-flip at every test incl. 150d.
- ❌ Exact-price prediction — naive "≈ now" baseline wins.
- ❌ big_up / big_down as trade triggers — 20–47% top-5%, confirmation only.
- ❌ 160-feature research models / L2-book direction without live parity — no servable parity, no edge.
- ❌ Any execution layer before the recorder edge table is positive after costs.

---

## 6. The one rule (the whole point)
```
BET ONLY WHEN:  calibrated_P(Hold) − market_ask − costs − safety_buffer  >  required_edge
```
Everything above sharpens the inputs to this gate. The gate itself is answered only by recorded
Polymarket rounds. Positive after costs → paper → micro-live → scale (all gated). Flat → ship the honest
probability/risk dashboard. **Both are acceptable, truthful outcomes.**

---

### Source-doc map (authoritative per topic)
Strategy/vision: `VNEXT_BREAK_THE_CEILING_VISION_2026-06-15.md` · Agent spec: `POLYMARKET_BOT_MASTER.md` ·
Heads+champion: `SPECIALIST_HEAD_CHAMPION_IMPLEMENTATION_2026-06-17.md`, `MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN_2026-06-17.md` ·
Evidence ledger: `CLAUDE_ENHANCEMENTS_AND_VALIDATION_LEDGER_2026-06-17.md` · Latest retrain: `OVERNIGHT_150D_RETRAIN_RESULTS_2026-06-18.md` ·
Calibration: `CALIBRATION_MONITOR_2026-06-18.md`, `HEAD_PROBABILITY_BUCKETS_2026-06-18.md` · Version roadmaps: `V8_ROADMAP.md`, `V9_ROADMAP.md`, `V10_ROADMAP.md` ·
Architecture: `reference/system_architecture.md` · UI: `reference/UI_GUIDE.md`.
