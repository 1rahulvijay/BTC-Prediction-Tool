# VNEXT — Breaking the Ceiling: the Anchor-Beat + Mispricing Vision (2026-06-15)

**Status: STRATEGY / VISION. Nothing here is implemented.** This is the consolidated plan for the next
version, drawing together the Anchor-Beat Probability engine, the Polymarket mispricing edge, and the
champion/challenger harness. Companion to `V10_ROADMAP.md` PART 6 (Polymarket data) and PART 7
(champion/challenger), `ENSEMBLE_ENHANCEMENTS_AND_TESTS_2026-06-15.md` (what's measured/wired), and
`PRICE_TO_BEAT_MODEL_ANALYSIS_2026-06-14.md` (the direction-ceiling research).

---

## 0. North star (one paragraph)
The current product asks *"will BTC go up or down?"* — a coin-flip, proven 11 ways at the information
ceiling (~0.50). **We do not break that ceiling by forecasting direction better. We stop fighting it.**
The product never needed a direction forecast; it needs (a) **calibrated conditional probabilities**,
where we already have a measured edge (P(Hold) 0.74–0.82, P(big_move) 0.70–0.75, calibrated bands), and
(b) those probabilities to **disagree with a tradeable market price by more than the spread**. (a) is a
modelling problem we've largely solved; (b) is a *microstructure/behavioural* problem, not a forecasting
one. The next version is built entirely around (a)→(b): the **Anchor-Beat Probability (ABP) engine**
feeding a **Polymarket mispricing edge**, validated by a **champion/challenger harness**.

---

## 1. The reframe — why anchor-beat has information that direction never did
Old question: *"From now, will price be higher in 5 minutes?"* — no revealed information; coin-flip.

New question: *"Given the round anchor, current price, time left, and path so far, will the current side
still beat the anchor at expiry?"* — this carries **revealed-path information**: the realized trajectory
from anchor→now (distance, time-above/below, cross count, path efficiency) is data the from-scratch
question never had. That is *why* P(Hold) works and raw direction does not. Measured proof (HistGB,
research_matrix, temporal split): the keeper features score **0.51 on direction but 0.70 on magnitude /
hold** — they tell you *how big / will it hold*, never *which way from scratch*.

---

## 2. The honest edge equation (read this twice)
```
edge = calibrated_P(side beats anchor)  −  market_ask(side)  −  spread_buffer
```
**The BTC model is NOT the edge.** A 94% hold (+12 bps, 60s left, calm) is the most *public* signal there
is — everyone sees the same price and clock, so the UP token already trades ~0.93–0.96. Modelling that
hold perfectly re-derives the order book's fair price; edge ≈ 0. **The edge lives only in the zones where
our calibrated probability disagrees with the market price after spread** — primarily the late
line-cross / late-flip zone, where path-stability features may price the flip slightly better than a
market looking only at distance+time. That edge is small, adversarial, regime-dependent, and
**unmeasurable from BTC data alone** — it requires Polymarket price history (§5). Any backtest that scores
"94% hold!" without subtracting a real market price is measuring a **tautology**, not profit.

---

## 3. The ABP Engine — heads (what exists vs what's new)
Specialist heads, not one global model. Anchor-relative features throughout
(`distance_from_anchor_bps`, `seconds_left`, `time_above/below`, `recent_cross_count`,
`max_favorable/adverse_distance`, `path_efficiency`, `current_side_age`, `vol_15/30/60s`).

| Head | Question | Status |
|---|---|---|
| **A — P(Hold_current_side)** | side is ahead now; does it still win at expiry? | **~exists** — re-target `persistence`/keeper model to anchor labels |
| **B — P(Line_Cross)** | does price cross back through the anchor before expiry? | **NEW** — the danger model near the line |
| **C — P(Big_Round_Move)** | does the round move far enough to make a clear winner? | **~exists** — `selectivity` head re-targeted to anchor distance |
| **D — P(Late_Flip)** | in the final 30–120s, does the winning side flip? | **NEW** — the Polymarket-specific protection model |
| **Evidence — kNN similar-setup** | "842 similar past setups: UP held 91.8%, Wilson LB 89.7%" | **NEW** — UI evidence layer, NOT a signal source |

≈70% of this is re-targeting heads we already have + the anchor coordinate system; the genuinely new work
is heads B/D + the kNN evidence layer.

---

## 4. The analytic baseline the ML must beat (don't skip this)
P(Hold) is ~90% a **barrier first-passage calculation**, not an ML problem. For a near-driftless price `σ`
that is `d` bps from the anchor with `t` seconds left:
```
P(Hold)        ≈ Φ( d / (σ·√t) )              # stay on the current side
P(Line_Cross)  ≈ reflection / first-passage term in (d, σ, t)
```
Build this **closed-form baseline first** — it IS the "empirical probability table", computed analytically,
zero training. (The one real modelling choice is **σ** — the local-vol estimator/window, e.g. a short EWMA
of recent returns re-estimated per snapshot. Get σ right and the barrier is accurate; get it wrong and
both P(Hold) and the edge are silently miscalibrated. Driftless is deliberate — over 5–15m, drift ≈ 0.)
The ML heads must then beat it **out-of-sample**. Prior (consistent with "more features
don't beat direction"): the ML beats the analytic barrier only **marginally**, capturing residual
microstructure / vol-clustering / path effects. Crucially, **the market also knows this formula** — which
is exactly why modelling the hold harder does not manufacture edge. The analytic baseline is both our
champion-to-beat and our sanity check against over-engineering.

---

## 5. Data architecture
### 5a. BTC side (we already have most of it)
From 1m/tick BTC data build synthetic 5m/15m **anchor rounds** + intra-round **snapshots** (per second
where available). Output: `anchor_rounds_5m/15m.parquet`, `anchor_snapshots.parquet`. Cheap; we have 100+
days of 1m and aggTrades already.

### 5b. Polymarket side — sourced via public APIs + timestamp joins (viable for a first-cut)
```
BTC snapshot (per round, per second/minute)
  ⋈ Gamma:                token_ids, anchor/expiry times, RESOLUTION (settlement truth — use THIS as the label)
  ⋈ CLOB /prices-history?market=<token_id>:   UP/DOWN price over time → implied prob
  ⋈ Data-API /trades?market=<token_id>:       actual prints (price, size, time)
```
This yields `(features, market_price_at_T, settled_winner)` per snapshot → the real `edge` of §2 becomes
computable on history. **The live `/book` quote recorder is strictly better (true depth, exact fills, our
latency) but is NOT a hard prerequisite for a first signal.**

**Four limits that keep the historical test honest (all make it OPTIMISTIC):**
1. **PM-side lookback is the binding constraint.** 5m/15m BTC markets are young — likely **months, not
   years**. The edge study is limited by the shorter side, not by BTC history. **Verify coverage first** (one Gamma query).
2. **Coarse resolution** — `prices-history` ≈ 1-min fidelity → ~5 points per 5m round; the second-level
   late-flip zone (where the edge lives) is blurred. Trades fill gaps only where prints exist.
3. **mid/last ≠ executable ask** — you cross the spread as a taker; raw mid **overstates edge by ~½ spread**.
   Treat any historical edge as an **upper bound**; estimate `spread_buffer` **empirically** from the
   trade-print bounce / prices-history per liquidity bucket — never a hand-picked constant.
4. **Sparse-print survivorship** — trades exist only where there was liquidity, biasing the sample toward
   tradeable-looking rounds. Cluster errors by round/day; don't extrapolate to illiquid setups.

### 5c. History depth & weighting
Use 1–2 yrs of BTC for the heads, **recency-weighted** (last 90d = 1.0, 90–180d = 0.75, 180–365d = 0.50,
older = 0.25) — more samples, but BTC regimes drift. Splits: **train oldest 70% / calibrate 15% / test
newest 15%**, plus **rolling monthly walk-forward** (train 180d → test next 30d → roll).

---

## 6. Champion / Challenger harness (the validation spine)
- **Champion** = the frozen `v11-pruned69` ensemble (and, for the anchor product, the analytic barrier
  baseline). **Challenger** = ABP heads, or the Polymarket mispricing model.
- **Offline-safe:** challenger has its own `saved_models/challenger/` dir and a separate
  `challenger_eval.duckdb`; it **never** touches the frozen champion or live `analytics.duckdb`.
- `backend/champion_challenger.py`: scores both on the same OOS/live stream, writes per-prediction rows +
  a head-to-head summary, reports sign-acc, Brier, calibration, and (for the PM model) **realized EV after
  spread**.
- **Promotion gate:** challenger replaces champion only if it beats it on the target metric by a margin on
  **≥500 resolved samples**, with stable calibration, **and explicit operator sign-off** (per
  `confirm-before-wiring`). Otherwise it stays shadow. **Never auto-promote.**

**The reference stack (what competes against what):**
```
Champion 1 — analytic barrier baseline        (zero-training floor; must be beaten OOS)
Champion 2 — existing keeper P(Hold) model    (already anchor-relative = learned barrier+residual)
Challenger 1 — ABP ML residual heads          (path-stability / micro-vol residual over the barrier)
Challenger 2 — P(Line_Cross) / P(Late_Flip)   (the danger layer — genuinely new)
Decision layer — Polymarket mispricing engine (edge = calibrated_P − market_ask − empirical_buffer)
```
A challenger earns promotion only by beating **both** champions on the §9 gate — and the mispricing
decision layer only trades when the calibrated probability beats the *market price*, not the baselines.

---

## 7. Anti-self-deception rules (the part that actually decides success)
1. **Cluster standard errors by round, day, AND vol-regime.** Snapshots within a round share one outcome;
   effective N ≈ #rounds (really #regime-days), NOT #snapshots. Wilson LBs on snapshots are far too tight
   — the #1 way this backtest will lie.
2. **Match Polymarket's exact anchor spec** (oracle, timestamp, settlement rule). A synthetic anchor on
   the wrong reference feed calibrates P(Hold) to the wrong target. Use Gamma RESOLUTION as the label.
3. **Edge is measured vs market price, never vs the BTC model.** No `edge` number is real until a recorded
   (or history-joined) `market_ask` is subtracted with a conservative spread buffer.
4. **No same-round leakage; purged walk-forward only; sign-truth labels; calibration > raw accuracy**
   (Brier + reliability curves). Live-shadow before any real capital.
5. **Beat the analytic barrier baseline OOS** before believing any ML head adds value.

---

## 8. Critical path (build order — do NOT reorder)
1. **Gamma coverage probe** (read-only, `backend/polymarket/probe_gamma_coverage.py`): how far back do
   5m/15m BTC markets go, and what fidelity does `prices-history` return? This single fact decides
   months-deep vs years-deep study. Output: `market_count, date_range, 5m_count, 15m_count,
   token_ids_available, price_history_points_per_round, trade_count_per_round`.
2. **Anchor rounds + snapshots** from existing BTC data (cheap), errors clustered by round/day.
3. **Analytic first-passage baseline** for P(Hold)/P(Line_Cross) — the champion to beat.
4. **Polymarket collectors** (Gamma + prices-history + trades) → `polymarket_*` parquets.
5. **Timestamp-join → first `edge` test** with conservative buffers + round-clustered CIs. **This one
   number gates everything downstream.** Positive even pessimistically → proceed. Flat even optimistically
   → stop; the edge isn't there.
6. **Live `/book` quote recorder** — stand up only once the offline edge shows promise (for the precise,
   fill-accurate version). Accrues from then on.
7. **ML heads (A–D)** as challengers to the analytic baseline; **kNN evidence layer** for the UI.
8. **Champion/challenger promotion** only after the §6 gate + live-shadow.

---

## 9. Acceptance gates (promote nothing that fails these)
- P(Hold) ≥ 0.93 bucket: realized ≥ 92%, **Wilson LB (round-clustered) ≥ 88%**.
- Brier better than the analytic barrier baseline, OOS.
- Calibration error acceptable; stable across **≥6 rolling monthly walk-forwards**; works on **both** 5m & 15m.
- No same-round leakage.
- **Tradeable gate:** `calibrated_P − market_ask − spread_buffer > min_edge` on real (history-joined or
  recorded) prices, **after** the optimism haircuts of §5b.

---

## 10. What we explicitly will NOT do
- ❌ Re-model BTC direction (any model/feature/architecture/data-volume — measured null, 11 ways).
- ❌ Add the 4 keepers to the direction ensemble (0.51 direction; already deployed for magnitude).
- ❌ Trust snapshot-count N or snapshot-level Wilson LBs.
- ❌ Believe any "hold %" or "edge" computed without subtracting a real market ask.
- ❌ Auto-promote a challenger, or wire anything live without explicit sign-off.

---

## 11. The decision loop the product becomes
```
Wait for the round to develop (seconds_left < ~180).
Measure distance-from-anchor + time-left + path stability.
Price P(Hold)/P(Line_Cross)/P(Late_Flip) (analytic baseline, ML residual on top).
Look up the kNN evidence (round-clustered).
Enter ONLY when P(Hold) is calibrated-high AND Wilson-LB clears AND the Polymarket
ask is cheap enough that  calibrated_P − ask − buffer > min_edge.
Otherwise: WATCH / NO_TRADE.
```
This stops fighting the information ceiling and starts trading the only thing that was ever tradeable:
**the gap between a calibrated conditional probability and a mispriced market.**

---

## 12. Empirical gate results (2026-06-15 — offline tests + read-only API probes, NO retrain/restart)

### 12a. Offline ABP battery — `persistence_dataset.parquet`, newest-15% holdout
- **Trained P(Hold) is calibrated + deployable as the probability engine:** P≥0.93 → **95.1%** realized,
  P≥0.95 → **96.0%** (27% coverage). No retrain needed to use it.
- **Analytic barrier vs model:** raw `Φ(d_z)` is *overconfident* (Brier 0.188); σ-recalibrated (isotonic on
  `d_z`) → Brier **0.163 ≈ model 0.160** → confirms **P(Hold) is ~90% distance/vol/time, no magic**. BUT the
  single `d_z` **saturates ~0.93** and can't form a usable high-precision tier; the 5-feat model carves a
  96%/27%-coverage bucket. **Champion = trained P(Hold); barrier = sanity baseline (validates σ caveat §4).**
- **Near-line danger surface (monotonic → deployable no-trade rules, no ML needed):**
  - `dist < 0.02% AND seconds_left > 60` → hold ≤ 68% → **NO_TRADE (line risk)**
  - `dist > 0.1% AND seconds_left < 60` → hold **98–99%** → **T3 zone**
- **Late-flip is concentrated near the line**, not just late → condition P(Late_Flip) on *distance*, not time.
- ⚠️ snapshot-level rates; round-clustered CIs are wider (§7) — the monotonic *structure* is robust.

### 12b. Polymarket coverage GATE — read-only Gamma/CLOB probe → **PIVOT** (corrected after rigorous mapping)
- ✅ Markets exist, exact anchor-beat shape, **directly slug-queryable**: `btc-updown-5m-<anchor_ts>`,
  `btc-updown-15m-<anchor_ts>`, CLOB token IDs present, anchor in the slug.
  *(Mechanism note: the `closed=true&order=endDate` / `/events?closed=true` queries falsely return 0 — they
  surface long-dated markets first. Direct `/markets?slug=btc-updown-5m-<ts>` is the correct method.)*
- ❌ **Queryable window ≲ 1 day** — `-1d/-2d/-7d/-30d` anchors are **NOT FOUND**; older rounds are purged.
- ❌ **No traded history observable** — every queryable round is **pre-open and dead flat**
  (`outcomePrices ["0.505","0.495"]`; prices-history pinned at ~0.51, zero movement, even a 6h-old market).
  Not a single resolved, price-moving round is visible to backtest against.
- ❌ **Resolution coarse** — `prices-history` ≈ 1-min fidelity regardless.
- ✅ **Liquidity is REAL and deep** (`/book` probe, even pre-open): ~**54k–60k size per side, 37–49 levels,
  1¢ spread** (bid 0.500 / ask 0.510). The flat 0.505/0.495 was just the seeded pre-open mid, not absence
  of a book. This removes the scariest unknown — there is depth to trade against.
- **VERDICT / PIVOT:** an offline historical edge backtest is **impossible** (no depth + no traded series),
  but the **liquidity kill-test PASSES**. **The live quote/price recorder (§8 step 6) is the ONLY path and
  becomes the CRITICAL PATH.** Record `btc-updown-5m/15m` forward (book + price + our P(Hold) + settlement),
  accrue rounds, THEN run the §2 edge test on recorded data. **The single make-or-break question the
  recorder answers:** when a round is *running* and BTC has moved vs anchor, does the market **ask LAG our
  calibrated P(Hold)** enough to clear the 1¢ spread (laggy MMs → edge) or snap to it instantly (efficient
  → no edge)? Deep book + 1¢ spread means *if* the lag exists, it's tradeable. The offline P(Hold) engine
  (12a) is validated and ready to pair with the recorder.
  *(Caveat: free/standard path; data-api `/trades` may add traded prints — untested.)*
- **STATUS 2026-06-15: recorder BUILT + smoke/report-validated** — `backend/polymarket/live_btc_updown_recorder.py`
  (standalone, shadow-only). Writes `pm_round_snapshots` / `pm_round_meta` / `pm_round_settlements` to
  `data/execution_layer.duckdb` (override `BTC_EXEC_DB`); reads frozen P(Hold) read-only; logs UP+DOWN book
  (bid/ask/mid/spread/depth_1c/2c/5c), anchor state, p_hold_up/down/current, decision_tier, **precomputed
  edge_up/down @1c/2c/3c + shadow_label**, and resolves each round (oracle / btc-proxy). Run continuously
  (`python backend/polymarket/live_btc_updown_recorder.py`) through LIVE windows; `--report` prints the edge
  scorecard. NOT wired into the app (per `confirm-before-wiring`).

### 12c. Direction ceiling — LARGE-SAMPLE confirmation (the 12th, and strongest, 2026-06-16)
Read from `analytics.duckdb` `price_to_beat` (8,423 resolved rounds, 5,829 committed UP/DOWN leans),
committed sign-accuracy with Wilson 95% CIs (each resolved round = 1 independent row):
| | win% | n | 95% CI |
|---|---|---|---|
| REAL 5m+15m (committed) | **49.3%** | 974 | [46.2, 52.4] |
| PRACTICE mirror (1/3/7/10/30) | 49.9% | 4,855 | [48.5, 51.3] |
| Grade A (real) | 47.4% | 272 | [41.6, 53.4] |
| Grade B (real) | 54.8% | 186 | [47.7, 61.8] |
| Grade C (real) | 48.3% | 516 | [44.0, 52.6] |
**No horizon and no confluence grade beats 50% with significance** (Grade B's CI still includes 50;
one-sided p≈0.09). The screenshot "Grade B 71% (5/7)" was small-sample noise — at n=186 it's a coin-flip.
**Verdict: direction/grades are definitively NOT an edge at large sample.** This is the empirical bedrock for
gating the bot on `P(Hold) − ask`, never on direction or grade. Do not re-litigate.
