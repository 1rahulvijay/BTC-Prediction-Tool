# HuggingFace Polymarket Dataset Audit — 2026-07-02

Strict quality/parity audit of `obadiaha/polymarket-crypto-5m-15m` before ingest. Research data until it passes.

**Files:** 21 orderbook days (2026-01-09.parquet .. 2026-03-19.parquet), markets/all.parquet, resolutions/all.parquet.

## 1. Market completeness
- BTC markets: **6,833** — 5m **5,124**, 15m **1,709**, other 0
- duplicate slugs: **0** ✅
- slug anchor is a valid unix ts: 100.0%
- window length correct (5m=300s / 15m=900s): 5m 100.0%, 15m 100.0%

## 2. Settlement correctness
- BTC resolutions: **7,508**; outcome ∈ {Up,Down}: 100.0%
- resolved_at ≥ window close (no future leakage): 100.0% ✅
- markets with a matching resolution: **6,828** of 6,833 (99.9%)

## 3. Orderbook completeness (3 sample days, 119,870 rows)
- snapshot cadence (median dt per token): **10s** ✅ ~10s
- valid best bid/ask in [0,1]: 96.4% (rest = empty/thin → quarantine)
- crossed books (bid>ask): **0** (0.0%) ✅
- top-10 ladder parseable: 99.6% ✅
- 2 tokens per (market, snapshot): 100.0% ✅
- snapshot ts inside [window_start, window_end]: 42.9%

**Liquidity characterization (not a defect — the key trading caveat):**
- median bid/ask spread: **$0.80** (of a $1 share) — these books are WIDE
- tight snapshots (spread ≤ 5c): **0.0%** — the only tradeable subset
- both-sides overround (ask_a+ask_b in [0.99,1.10]): 0.0% — the rest are wide/illiquid (often 0.1/0.9 at open). Fair value ≠ ask until the book tightens.

## ⚠️ DECISIVE — the ORDERBOOK is a broken barbell; use the TRADES instead
Parsing the ladder confirms the books are **barbell-shaped**: bids only at 0.01–0.10, asks only at 0.90–0.99, an
**$0.80 dead zone with NO tradeable inside quote**, and it **never tightens** (median inside spread $0.80 across the
whole distribution, 0% ≤5c, even in the last 30s). `best_bid/ask` match the ladder, so it's internally consistent —
but there is **no executable ~$0.50 price**. The live book (July 2026) is 0.50/0.51, so this is either an early-market
barbell or a recorder that missed the inside. **Either way the orderbook `best_bid/ask` CANNOT be used as the
executable ask** — `P(Hold) − ask` is not computable from it.

**But the `trades/` subset IS real and usable** (~43M rows): BTC executed price **median 0.539**, 48% in 0.3–0.7,
with `side` (aggressor) + `size`. Trades reflect where the market actually is (a probability / execution-price
proxy), even though the resting orderbook does not.

## Verdict — KILL (for edge); metadata/resolutions only
**KILL: the HF orderbook is structurally unusable for edge analysis.** Reason: persistent ~$0.80 spread / barbell
ladder (0.01–0.10 bids, 0.90–0.99 asks) / **no inside quote** / no expiry tightening. Using `ask=0.90` would make
every opportunity look bad; inferring a midpoint from a broken ladder would fabricate probability. Either way it
would corrupt the model. **Do NOT build `build_pm_historical_mispricing_table.py` on this dataset.**

| HF subset | Use |
|---|---|
| markets, resolutions | ✅ **usable** — slug/window/condition_id validation, settlement outcomes, market-calendar, recorder-coverage audits |
| orderbook `best_bid/ask` + ladder | ❌ **KILL** — not an executable quote (barbell; no inside; no tightening) |
| trades (executed price/side/size) | 🟡 **support only** — activity/labeling features (late-volume, fill-side pressure, rough probability path). **NOT** an executable-ask edge source — a trade price is *not* the ask you'd pay. |

**There is still no reliable free historical Polymarket ask/depth dataset.** The only trustworthy route is
**record-forward** the live CLOB `/book` (real tight books — verified 0.50/0.51) → join P(Hold) → join settlement →
analyze after 500–1,000 rounds. Any future candidate dataset must pass a hard pre-ingestion gate first:
*median active spread ~1–5c (not 80c), UP ask + DOWN ask ≈ 1.00–1.05, inside near live probability, book tightens
toward expiry, ts inside the window.* This audit is why that gate exists.