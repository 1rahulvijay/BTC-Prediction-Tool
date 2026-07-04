# Free Data Sourcing Tests + Recorder State — 2026-07-02

Tested the proposed free data sources **live** (not from claims) and audited the recorder state before building
anything. Two conclusions: **the Polymarket book+P(Hold)+depth+settlement recorder is already built (twice)** —
the bottleneck is operational (run it), not code — and **Binance bookDepth adds no predictive lift**.

## 1. Free data-source tests (verified live)
| Source | Claim | Verdict (tested 2026-07-01/02) |
|---|---|---|
| **Binance futures `bookDepth`** (`data.binance.vision/.../futures/um/daily/bookDepth/BTCUSDT/`) | "best free BTC L2" | ✅ real & free, back to 2023 — **but 30s AGGREGATE depth at ±0.2/1/2/3/4/5% bands (`timestamp,percentage,depth,notional`), NOT tick L2.** Liquidity-context only. |
| **Polymarket Gamma discovery** | market/token/condition_id | ✅ works — 12 live BTC up/down markets, slugs `btc-updown-{5,15}m-<anchor_ts>`, Up/Down tokens. |
| **Polymarket CLOB `/book`** | live ask/depth | ✅ works — full live book (bids/asks price+size per cent) → YES/NO ask, spread, depth. **LIVE only, no historical /book.** |
| **Polymarket CLOB `/prices-history`** | probability history | ⚠️ too sparse for 5m (returned **2 points** for a 5-min market). Rough probability, no depth. |
| One live "mispricing row" from free endpoints | buildable? | ✅ built one: `up_ask 0.51, down_ask 0.50, spread 0.01, up_depth_1c 459, up_depth_3c 606, complement_sum 1.01, binance 60814`. Only `P(Hold)/seconds_left/settled_side` need adding — all already recorded. |

**Conclusion:** no free *historical synchronized* dataset exists (BTC state + P(Hold) + ask + depth + settlement). `/book`
is live-only, `/prices-history` too coarse, `bookDepth` 30s aggregate. → **record forward** (which the app already does).

## 2. Binance bookDepth liquidity probe — NEGATIVE (`probe_bookdepth_liquidity.py`)
90 days joined to the 1m matrix; 11 liquidity features (near-depth imbalance ±0.2/1/2%, total depth, depth slope,
30s/2m depth change, liquidity-vacuum z-score) vs an rv baseline, causal 70/30 + shuffled-null:

| Target | base rate | rv baseline AUC | +bookDepth AUC | lift | p |
|---|---|---|---|---|---|
| big-move (top-quartile) | 16.6% | **0.747** | 0.747 | **−0.000** | 0.39 |
| big-drop (≥$50 down, 5m) | 37.2% | **0.707** | 0.706 | **−0.002** | 1.00 |

Top liquidity features univariate ~0.51–0.53. **No lift over realized vol** — the standing-book depth is redundant
with rv at 30s resolution. (`BOOKDEPTH_LIQUIDITY_PROBE_2026-07-02.md`)

**Also tested as a VETO/regime layer (the honest second chance) — also DEAD.** (`BOOKDEPTH_VETO_PROBE_2026-07-02.md`)
On 12,136 real P(Hold)≥0.93 champion snapshots joined to the liquidity regime: held% is **flat** across DEEP/NORMAL/
THIN/VACUUM (95.3–96.2%; VACUUM +0.1pp, i.e. NOT worse). Vetoing VACUUM-book calls removed 90 bad but **2,078 good**
(net −1,988) — a terrible filter. Only a faint interaction survives (near-anchor/late VACUUM calls hold ~2–3pp less),
not enough to wire. **Verdict: bookDepth is dead 3 ways (predictive AUC, big-drop, veto). Drop it entirely** — not a
feature, not a regime label, not a veto. The user's discipline (test it as a veto, not a predictor) was right; the
data says no at 30s resolution.

## 3. Recorder state — #1 is ALREADY BUILT (do not duplicate)
The "log the full /book ladder + join P(Hold) + settlement" ask is already implemented across two recorders:

| Recorder | DB | Captures |
|---|---|---|
| **`live_btc_updown_recorder.py`** | `analytics.duckdb` (`pm_round_snapshots`) | **the exact core dataset:** `p_hold_cur, up_bid/ask/mid/spread, up_d1/d2/d5 (depth 1c/2c/5c), down_*, top_ask_size, seconds_left, decision_tier, condition_id` → joined to `pm_round_settlements` (official outcome). |
| **`l2_recorder.py`** (Codex) | `polymarket_l2.duckdb` | full **WebSocket** ladder (`pm_l2_book_levels`, `pm_l2_level_updates`, `pm_l2_trades`), exact **taker VWAP at size** (1/10/50/100/500), 3-mode **maker-queue** replay, reconnect boundaries. Live smoke test passed. |

Analyzers exist: `analyze_pm_recorder.py` (mispricing scan), `test_polymarket_l2_execution.py` (VWAP/queue report).

**Nothing to build here.** The gap is **operational**: both recorders are **stopped** (last write 2026-07-01 05:42 UTC).
Only **~29 officially-joined rounds over 2 days** exist. Gates: **200 to train** a fair-value residual, **500–1,000 to
promote**. No promotion clock advances while the recorders are off.

## 4. Verdict / next action
- **Do not build another recorder** (#1 is done, better than the 1s-REST plan — Codex's is WebSocket + VWAP + queue).
- **Do not wire bookDepth** (#2 is measured-null).
- **The single highest-value action is to RUN the recorders and keep them running:** `start_recorder.bat`
  (`live_btc_updown_recorder.py` → P(Hold)+depth+settlement) and `run_polymarket_l2_recorder.bat` (full L2/VWAP/queue),
  until 500–1,000 joined rounds accrue. That is the only thing between here and a provable edge.
- The Reddit "5m up/down second-by-second" dataset remains an **unverified** lead — worth locating only to shortcut
  research; the live forward recorder is still required for production proof.
