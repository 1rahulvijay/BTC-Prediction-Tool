# Polymarket btc-updown Trading Agent — MASTER SPEC (living doc, 2026-06-16)

Single source of truth for the automated agent. Supersedes `POLYMARKET_BOT_REQUIREMENTS_2026-06-16.md`
(removed). Companion: `VNEXT_BREAK_THE_CEILING_VISION_2026-06-15.md` (strategy/evidence).

---

## 0. The one rule (everything else protects it)
```
BET ONLY WHEN:  calibrated_P(Hold) − market_ask − costs − safety_buffer  >  required_edge
```
The agent **never** bets because a model says UP/DOWN. **"Profitable" is earned on recorded data, not
specified.** The agent is built **edge-gated**: paper-only by default; a real order is physically blocked
until the recorded edge clears the promotion gate (§6) and an operator flips it on.

---

## 1. The evidence base (why this design, in hard numbers)
- **Direction is a coin-flip — confirmed 12 ways, now at large sample.** Committed sign-accuracy over
  **8,423 resolved rounds / 5,829 leans**: real 5m+15m = **49.3% [46.2, 52.4]**; practice mirror n=4,855 =
  **49.9% [48.5, 51.3]**. **No confluence grade beats 50%** (A 47.4%, B 54.8% [47.7, 61.8] — includes 50,
  C 48.3%). → Direction/grades will NEVER be the edge. Do not build on them.
- **P(Hold) IS calibrated and deployable** (validated, frozen `persistence_model.pkl`): P≥0.93 → **95.1%**,
  P≥0.95 → **96.0%** realized. The σ-recalibrated analytic barrier ≈ model on Brier (0.163 vs 0.160) — so
  P(Hold) is ~90% distance/vol/time — but the ML carves the deployable high-precision tier the barrier can't.
- **Anchor-relative danger surface (deployable no-ML rules):** `dist<0.02% & seconds_left>60` → hold ≤68%
  (NO_TRADE); `dist>0.1% & seconds_left<60` → **98–99%** hold (T3). Late-flip risk is concentrated **near the
  line**, not just late.
- **Polymarket structure (read-only probe):** markets exist (`btc-updown-5m/15m`, anchor in slug,
  slug-queryable); **NO usable traded history** (≲1 day window, prices-history ≤5 pts/round, all pre-open
  flat) → **forward recording is the only path**; but **liquidity is DEEP** (~$50k/side, 1¢ spread).
- **The single open empirical question:** does the ask **lag** P(Hold) during live running rounds enough to
  clear the 1¢ spread? Unanswerable offline; the recorder exists to answer it.

### BTC-side fair-value head stack — FROZEN 2026-06-16 (multi-head bakeoff complete; STOP expanding)
The `backend/research/` bakeoff (90-day, 5m+15m, round-grouped walk-forward) settled the BTC-side model shape:
| Head | Status (bakeoff) | Use in the bot |
|---|---|---|
| **P(Hold) fair value** | validated; **ML ≈ analytic barrier** (no ML lift) | `p_hold_fair = min(analytic_barrier, keeper_model)` — conservative for paper; barrier is the sanity anchor |
| **P(LineCrossFromNow)** | **real danger head**, forward-from-snapshot label, ~0.82 AUC | block: `NO_TRADE_FLIP_RISK if P(line_cross) > 0.20–0.25` |
| **P(BigMove)** | strong timing/selectivity head | watch/tradability filter (NOT side selection) |
| **High/low/range quantiles** | **CQR-calibrated to ~80%** | projected band — UI says *"80% calibrated range"*, NOT "predicted high/low" |
| **Direction (up/down)** | path-mechanics only (0.70→0.99 by seconds-left = no skill) | **display only, never edge** |

**Acceptance (evaluate on the 90-day run):** line_cross AUC ≥ 0.75 + acceptable **false-safe rate** in the trade
zone (predicted-safe but crossed); CQR coverage **78–82%**, stable across 5m/15m. **Do NOT add** another
direction ensemble, more DL, more RF/XGB seats, more generic BTC indicators, or snapshot-pooled accuracy.
The BTC-side is **done**. The only remaining make-or-break is `p_hold_fair − ask − buffer` on recorder data.
*(`p_hold_fair` and `analytic_barrier` are computed offline in the edge analyzer from the recorder's logged raw
inputs — no recorder change needed; the live recorder keeps accruing untouched.)*

---

## 2. Architecture / data flow
```
live_btc_updown_recorder.py   (BUILT)  -> execution_layer.duckdb : pm_round_snapshots / _meta / _settlements
        │  every ~1.5s: BTC distance-from-anchor · seconds_left · P(Hold)up/down/cur · UP/DOWN book · settlement
        ▼
backend/bot/paper_agent.py     (BUILD)  -> PAPER_BET / SKIP_*  (no wallet, no orders)
        ▼
backend/bot/analyze_bot_performance.py (BUILD) -> promotion report (§6)  ── GATE ──┐
        ▼ (only if gate passes + operator sign-off)                                │
backend/bot/live_adapter.py    (BUILD LAST) -> CLOB order signing/placement        │
        ▼                                                                          │
backend/bot/{bot_risk,bot_store,bot_math}.py  enforce caps / log / size  ◀─────────┘
```

---

## 3. Functional requirements
| # | Requirement | Status |
|---|---|---|
| F1 | Discover live `btc-updown-5m/15m` rounds, tokens, anchor, expiry | ✅ recorder |
| F2 | Live state + calibrated P(Hold) (frozen model, read-only) | ✅ recorder |
| F3 | Edge computation `P(Hold) − ask − buffer` per side | ✅ recorder logs it |
| F4 | Decision engine (gated; §5) | 🔧 paper_agent |
| F5 | Order execution — EIP-712 sign + place CLOB orders, track fills | 🔧 live_adapter (last, highest risk) |
| F6 | Position / portfolio / PnL tracker | 🔧 bot_store |
| F7 | Settlement reconciliation | 🔧 analyze_bot_performance |
| F8 | Monitoring + global kill switch | 🔧 bot_risk |

---

## 4. First build slice (Phase 1/2 paper agent — consume the recorder, do NOT rebuild collection)
```
SOURCE:  data/execution_layer.duckdb  (pm_round_snapshots / pm_round_settlements)
         ⚠ NOT pm_recorder.duckdb / btc_updown_recorder.py — RETIRED. Recorder = live_btc_updown_recorder.py.
BUILD:   backend/bot/bot_math.py    edge / EV / kelly (LOGGED ONLY; quarter-Kelly off the LOWER-bound edge)
         backend/bot/bot_store.py   paper decisions + settlements (own tables in execution_layer.duckdb)
         backend/bot/bot_risk.py    caps, circuit breakers, legal-venue gate, kill switch
         backend/bot/paper_agent.py snapshots -> PAPER_BET / SKIP_* (no wallet, no orders)
         backend/bot/analyze_bot_performance.py  score after settlement -> promotion report (§6)
```
`bot_math.py`: `ev_per_share = p − ask`; `net_edge = p − ask − fee − slippage − buffer`;
`kelly = (p − ask)/(1 − ask)` (log only; production size = `min(cap, 0.25·kelly_on_lower_bound)`).

---

## 5. Decision logic (paper + live share it; live additionally requires gate+sign-off)
```
if not legal_venue_verified:                      SKIP  legal_not_verified
if not EDGE_PROVEN(horizon):                      SHADOW_ONLY            # never a real order
if abs(distance_from_anchor_pct) < 0.02 and seconds_left > 60:  NO_TRADE line_risk
if seconds_left > 180:                            WAIT  too_early
if p_line_cross > 0.25:                           NO_TRADE flip_risk
if spread > max_spread:                           SKIP  spread_too_wide
if liquidity_within_2c < min_size_usd:            SKIP  low_liquidity
if quote_age_ms > max_latency_ms:                 SKIP  feed_stale
if net_edge < required_edge:                      NO_EDGE   # "HIGH PROBABILITY, NO EDGE: market priced it"
else:                                             BUY_side  size = fractional_kelly
```
- **Edge gate, NOT an ask cap.** Don't hard-cap `max_ask`; a 0.99 P(Hold) at 0.94 ask (net 0.04) is valid; a
  0.96 at 0.95 (net 0.00) is blocked. **`required_edge ≈ 0.04`** to start.
- **Accuracy ≠ profit.** When `net_edge ≤ 0`, decision = `NO_EDGE`; UI shows *"HIGH PROBABILITY, NO EDGE."*
- Reasons: `PAPER_BET, NO_EDGE, NO_TRADE(line/flip), WAIT, SKIP_SPREAD_TOO_WIDE, SKIP_LOW_LIQUIDITY,
  SKIP_FEED_STALE, SKIP_LEGAL_NOT_VERIFIED, SKIP_DUPLICATE_ROUND`.

---

## 6. Promotion gate (ALL must pass before any real order; round-level, not snapshot-level)
- ≥ **500 resolved shadow rounds** with edge signals, per horizon.
- **Realized paper EV after modeled fills > 0** (ask, not mid; conservative empirical buffer).
- **Wilson 95% lower bound on net/contract > 0** (round-clustered).
- **Calibration stable** across ≥ 6 rolling windows; works on the traded horizon.
- **Edge-decay / executability:** `edge_duration_seconds, max_edge, edge_after_1s/2s/5s` + depth-within-1–2¢.
  If edge persists < loop latency, it is **not executable** — this single test can kill the strategy.
- **Explicit operator sign-off** (per `confirm-before-wiring`). No exceptions.

**This gate IS champion/challenger** (repurposed from V10 PART 7): the **champion = the Polymarket ask** (the
market's fair price, efficient until proven otherwise); the **challenger = our calibrated P(Hold)**. We bet
only when the challenger beats the champion after costs. The original "challenger = a better BTC *direction*
model" is dead (n=5,829, §1) — do NOT build it. The legitimate future challenger is a **P(Hold) retrained on
the recorder's own live anchor-beat data**, promoted over the current Binance-proxy-trained champion ONLY if
it is better-calibrated AND yields better realized edge (the `champion_challenger.py` harness, pointed at
P(Hold) versions). Champion/challenger is a **promotion discipline, not an edge source** — it protects against
deploying a fake edge; it cannot create one. The recorder still has to prove the edge exists first.

---

## 7. Sizing & 8. Risk (non-negotiable, hard-coded)
- **Quarter-Kelly (≤0.25) off the LOWER-bound** calibrated edge + hard per-trade cap. Never full Kelly.
- Per-round, per-day, total exposure caps independent of Kelly.
- **Paper → micro-stakes ($1–5) → scale**, each gated on the previous.
- **Circuit breakers / halt on:** daily loss > limit · N consecutive losses · drawdown > X% · live fills
  worse than shadow · reconciliation mismatch · feed staleness · wallet/allowance error.
- **Global kill switch** (one env var / command) halts all placement; positions tracked to settlement.
- No averaging into losers, no martingale, no overriding NO_TRADE.

---

## 9. Execution / CLOB specifics (built LAST, after the gate)
- CLOB REST+WS; orders **EIP-712-signed**. **Dedicated low-balance Polygon wallet**, USDC.e, set allowances
  once (CTF Exchange + Neg-Risk). Key NEVER in code/logs/repo — encrypted store or hardware signer.
- **Taker** (cross spread, certain fill) vs **maker** (better price, fill not guaranteed → fill-rate becomes a
  tracked metric). Start taker; if the 1¢ spread eats the edge, maker is mandatory.
- Per-order: max size, max price (don't chase), FOK/GTC, client-order-id (idempotency).
- **Compliance: verify jurisdiction/ToS permits trading** — operator responsibility, not a code feature.
- Isolation: trading process separate from the app; reads frozen models read-only; never blocks the feed.

---

## 10. Phased rollout + STOP criteria
```
Phase 0  Recorder accrues live rounds          ← run live_btc_updown_recorder.py persistently (bottleneck)
Phase 1  Edge analysis: P(Hold)−ask−buffer >0? ← GO/NO-GO gate (§6)
Phase 2  Paper agent on live book (no wallet)
Phase 3  Micro-stakes live ($1–5), reconcile fills vs shadow, measure slippage/lag
Phase 4  Scale within caps — ONLY if live EV ≥ shadow EV and fills hold up
```
**STOP if:** Phase-1 edge flat even optimistically → ship the probability dashboard, don't build execution.
Live fills systematically worse than shadow → halt. Market structure changes → re-prove from Phase 1.

---

## 11. Open decisions (operator input before Phase 2/3)
Taker-first vs maker-first? · capital + per-trade cap + daily-loss limit? · 5m / 15m / both? · jurisdiction
confirmed? · key custody (encrypted env vs hardware signer)?

## 12. Build status
- **Exists:** discovery, P(Hold) engine, edge computation, **recorder** (`live_btc_updown_recorder.py`),
  separate `execution_layer.duckdb`, validated no-trade/T3 rules.
- **Net-new (only after §6 gate):** `backend/bot/` (paper_agent, bot_math/store/risk, analyze, live_adapter).
- **Current bottleneck:** the recorder must RUN persistently to accrue rounds. That data — not more code —
  decides whether this becomes a trading agent or stays an honest probability dashboard.
