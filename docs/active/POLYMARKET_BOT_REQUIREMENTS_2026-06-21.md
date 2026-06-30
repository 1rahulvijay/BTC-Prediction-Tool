# Polymarket Auto-Betting Bot — Requirements Draft (2026-06-21)

**Goal stated:** "AI Polymarket bot/agent that is profitable, makes money, takes bets automatically."

**What this document is:** a requirements spec + phased plan + gap analysis for an automated betting
agent on Polymarket's BTC 5m/15m up/down markets, grounded in everything this project has measured.

---

## 0. The premise you cannot skip (read this first)

A bot does **not** create profit. It executes an edge faster and without emotion. If there is no edge,
an automated bot loses money *faster and more reliably* than a human would. So the first requirement is
not code — it is **proof that an edge exists.**

What this project has established (do not relitigate):
- **BTC direction is a coin-flip** at every timeframe/hour/day (confirmed ~12 ways). A bot that bets a
  *direction* will lose to fees. **Do not build a direction bot.**
- **The only measured edge is mispricing:** `EDGE = P(Hold) − market_ask − buffer`, where P(Hold) is the
  late-entry persistence probability (the already-ahead side holding to close, 87–99% in the right
  cells). The bot's entire thesis is: *Polymarket sometimes prices the held side below its true hold
  probability, and we buy that gap.*
- **This edge is currently UNPROVEN at the money level.** `analyze_pm_recorder.py` is built and official
  settlement ingestion has backfilled **364 outcomes**, but only **4 rounds have contemporaneous asks**.
  Until it prints a **positive ROI at ≥3c buffer over 500–1000 joined quote+outcome rounds, after
  costs**, there is nothing to automate.

**Therefore the #1 requirement (a hard gate):**

> **R0 — EDGE PROOF GATE.** No real-money order is placed until `analyze_pm_recorder.py` shows positive
> net ROI on ≥500 resolved rounds, after the realistic ask spread + fees + gas + slippage. If the edge
> table is flat/negative, the correct outcome is *don't trade* — ship the dashboard instead.

Everything below is what you build *assuming the gate passes*. Build the gate-measurement and the
paper-trading harness first; build live execution last.

---

## 1. Functional requirements (the bot itself)

Priority: **MUST** = required for a minimum viable profitable bot · **SHOULD** = needed before scaling ·
**COULD** = later.

| ID | Requirement | Priority | Status today |
|----|-------------|----------|--------------|
| **F1** | **Market discovery** — auto-find the active BTC 5m/15m up/down markets, their `condition_id`, yes/no token ids, and resolution time. | MUST | ✅ recorder already discovers + tracks top markets |
| **F2** | **Live signal** — serve the same P(Hold) the UI shows (`persistence_snapshot`), per market, per tick. | MUST | ✅ keeper P(Hold) served + recorded |
| **F3** | **Live order book** — current `yes_ask`/`no_ask`, spread, and *available size at the ask* for each tracked market (CLOB WS). | MUST | 🟡 quotes recorded (`polymarket_quotes`); **need depth/size at ask, not just top price** |
| **F4** | **Edge computation** — `EDGE = P(Hold)_heldside − ask_heldside − buffer`; abstain unless `EDGE ≥ buffer_min` AND time-to-close / distance are in a proven cell. | MUST | 🟡 offline analyzer exists; **needs a live online version** |
| **F5** | **Position sizing** — fractional Kelly on the *measured* edge, capped (e.g. ¼-Kelly, hard per-trade $ cap). Never full Kelly. | MUST | ❌ not built |
| **F6** | **Order execution** — place a limit/marketable order on the held side via Polymarket CLOB API; confirm fill; record fill price + size. | MUST | ❌ not built (no execution client) |
| **F7** | **Settlement + PnL accounting** — detect market resolution, redeem/realize, log realized PnL per trade and cumulative. | MUST | 🟡 official outcome ingestion **DONE** (364 CLOB outcomes); trade-level realized PnL/redemption remains unbuilt |
| **F8** | **Risk engine + kill switches** — daily loss limit, max open exposure, max per-market, circuit breaker on N consecutive losses or feed staleness → auto-halt. | MUST | ❌ not built |
| **F9** | **Monitoring/alerting** — live dashboard of open positions, edge, PnL, fill quality; alert on halt/error/drawdown. | SHOULD | 🟡 app dashboard exists; bot panel not built |
| **F10** | **Paper-trading mode** — identical decision path, simulated fills at recorded asks, no real orders. The mandatory step between proof and live. | MUST | ❌ not built |
| **F11** | **Maker vs taker logic** — prefer resting maker orders (better price, possible rebate) with a taker fallback when edge is large + time short. | COULD | ❌ later |
| **F12** | **Multi-market concurrency** — run the loop across all active BTC rounds simultaneously. | SHOULD | partially (recorder tracks multiple) |

---

## 2. The decision loop (precise spec for F4–F6)

Per tracked market, every tick:

```
1. read P(Hold) for the currently-ahead side          (F2)
2. read ask for that side + available size            (F3)
3. edge = P(Hold) - ask - buffer
4. GATE: trade only if ALL hold:
     - edge >= EDGE_MIN            (e.g. >= 0.03, set from the proof table)
     - seconds_left in [S_lo, S_hi]   proven late-entry window
     - |distance| >= DIST_MIN          proven "already ahead" cell
     - market spread <= SPREAD_MAX     (don't trade illiquid books)
     - not already at max exposure on this market/day
5. size = clamp( kelly_fraction * edge / odds , 0 , PER_TRADE_CAP )   (F5)
6. place order at ask (or 1 tick better as maker)     (F6)
7. on fill: record; hold to resolution (no in-round exit v1)
8. on resolution: realize PnL, update risk counters   (F7)
```

`EDGE_MIN`, `S_lo/S_hi`, `DIST_MIN`, `SPREAD_MAX` are **not guessed** — they come straight out of the
`analyze_pm_recorder.py` proof table (the buffer/cell that was actually positive). The bot only ever
trades the cells the offline proof said were profitable.

---

## 3. Risk requirements (this is what "makes money / doesn't blow up" actually means)

A bot with a small real edge still goes bankrupt without these. **All MUST:**

- **Per-trade cap:** fixed max $ (e.g. $5–$20 while proving live).
- **Fractional Kelly:** ≤ ¼-Kelly. The measured edge is noisy; full Kelly over-bets and ruins.
- **Daily loss limit:** halt all trading for the day at −$X realized.
- **Max concurrent exposure:** cap total at-risk across all open markets.
- **Consecutive-loss breaker:** N losses in a row → auto-halt + alert (edge may have decayed/broken).
- **Feed-staleness guard:** if P(Hold) or quote data is stale > T seconds → abstain (never trade blind).
- **Settlement/oracle-dispute guard:** account for markets that resolve late or get disputed.
- **Global kill switch:** one flag/file/endpoint that stops new orders immediately.
- **Cold-start safety:** bot boots in *paper* mode by default; live requires an explicit, deliberate flag.

---

## 4. Technical / infrastructure requirements

| Area | Requirement |
|------|-------------|
| **Wallet** | A funded Polygon wallet: USDC.e for stakes + small MATIC for gas. Private key stored securely (env/secret store, **never** in code or git). |
| **Allowances** | One-time on-chain approvals so the CLOB/CTF exchange contracts can move your USDC and outcome tokens. |
| **API client** | Polymarket CLOB API via the official `py-clob-client` (place/cancel/query orders). Generate L2 API key/secret/passphrase from a wallet signature. |
| **Auth/secrets** | Private key + API creds in a secret manager or `.env` excluded from git; least-privilege; rotation plan. |
| **Order types** | Support limit (GTC/GTD) for maker and marketable/FOK for taker. |
| **Data plane** | CLOB WebSocket for live book + your existing P(Hold) serving. Reconnect/backoff on drop. |
| **Storage** | Keep the bot's trade log in a **separate DuckDB/sqlite** (like the other recorders) — never contend with the app's locked DB. |
| **Runtime** | Standalone process (own `.bat`/service), independent of the prediction app, restart-safe, idempotent (no double-orders on restart). |
| **Idempotency** | A client order id + local "already placed this round" guard so a crash-restart never re-fires an order. |
| **Observability** | Structured logs of every decision (even abstains, with the reason) for post-hoc analysis. |

---

## 5. Compliance / legal constraint (your responsibility, stated as a requirement)

- **R-Legal:** Polymarket restricts certain jurisdictions (e.g. US persons) under its Terms. Confirm that
  automated trading from your account/jurisdiction is permitted before going live. This is a real
  go/no-go requirement — not a coding detail. I can't advise on the law; you own this gate.
- **R-Tax:** keep the trade log for PnL/tax reporting.

---

## 6. Phased rollout (with hard go/no-go gates)

```
Phase 0  PROVE THE EDGE        -> analyze_pm_recorder.py positive on >=500 joined quote+outcome rounds, after costs.
         (offline)                GATE: if flat/negative -> STOP. Ship the dashboard. No bot.
                                  Requires: keep the recorder + settlement ingestion running ~weeks.

Phase 1  PAPER TRADE           -> F10 sim bot runs the full loop on live data, fills simulated at the
         (live data, no money)    recorded ask. Run >= 200-500 paper rounds.
                                  GATE: paper PnL positive AND matches the offline proof (no execution
                                  surprise: slippage, latency, partial fills modeled).

Phase 2  MICRO LIVE            -> real orders, per-trade cap $5-$20, full risk engine + kill switch on.
         (tiny real money)        Run weeks. GATE: live PnL positive after ALL costs; fills near paper.

Phase 3  SCALE                 -> raise caps / Kelly fraction gradually, only while the edge table and
                                  live PnL stay positive. Continuous monitoring; auto-halt on decay.
```

**You are at the start of Phase 0.** Official settlement ingestion is operational and restart-safe.
The current blocker is **quote accrual**: 364 official outcomes exist, but only 4 markets have the
contemporaneous P(Hold)+ask snapshots required for an honest edge calculation.

---

## 7. Gap analysis — what exists vs. what to build

**Already have (reuse):**
- Market discovery + tracking (recorder).
- Served P(Hold) signal (`persistence_snapshot`) — the edge source.
- Quote recording (`polymarket_quotes`) — the ask side.
- The offline edge proof (`analyze_pm_recorder.py`) — Phase 0 measurement.

**Must build (in order):**
1. **Continuous quote accrual** → keep the auto-started recorder alive until ≥500 joined rounds.
2. **Live edge engine (F4)** — only after the offline edge table is positive and stable.
3. **Paper-trading harness (F10)** — decision loop + simulated fills + PnL log.
4. **Risk engine + kill switch (F8)** — before any real order.
5. **CLOB execution client (F6)** — wallet, auth, place/cancel, fill capture.
6. **Sizing (F5)**, **bot dashboard (F9)**, **maker logic (F11)**.

---

## 8. Definition of "profitable" (acceptance criteria)

The bot is "profitable" only when **all** hold:
- Offline proof: positive net ROI, ≥500 joined quote+outcome rounds, after spread+fees+gas+slippage, buffer ≥ 3c.
- Paper: positive PnL over ≥200 rounds, consistent with the offline proof.
- Live micro: positive realized PnL after all costs over a multi-week run, fills close to paper.
- Risk: no single day breaches the loss limit; kill switch verified working.

If any fails, the honest result is: **the edge isn't there at tradeable size — don't automate it.**

---

## 9. Bottom line

- A "profitable auto-betting bot" here = **a proven mispricing edge + disciplined execution + hard risk
  controls.** The AI/model part is already done (P(Hold)); the missing 80% is *proof, execution, and risk*.
- The single most important requirement is **R0: prove the edge before automating it.** The settlement
  plumbing is done; let quote+outcome pairs accrue, run the analyzer, then build the paper-trader *first*.
  Only wire real orders after both pass.
- Do **not** build a direction bot. It will lose. The bot's only job is to buy P(Hold) when Polymarket
  underprices it, in the exact cells the proof says are positive, and to stop instantly when it isn't.
```

---
*Next concrete step: keep `start_recorder.bat` running continuously and monitor joined quote-round count.
Everything downstream waits on a positive, after-cost edge result over enough joined rounds.*
