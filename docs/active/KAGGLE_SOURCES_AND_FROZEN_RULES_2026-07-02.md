# Kaggle Sources & Frozen Trading Rules (2026-07-02)

Permanent record: where every archive came from (re-download anytime), the frozen rule family, and the
replay verdicts. Companion docs: `KAGGLE_DATA_TEST_PLAN_2026-07-02.md` (inventory + test results),
`PM_DATASET_HUNT_AUDIT_2026-07-02.md` (audit gate rules).

## Dataset sources (URL → local archive)

| Kaggle dataset | Local file | Verdict |
|---|---|---|
| [kachoio/polymarket-5-minute-crypto-updown-markets](https://www.kaggle.com/datasets/kachoio/polymarket-5-minute-crypto-updown-markets) | `archive (7).zip` (0.5 GB) | ⭐ **THE GEM** — per-second executable bid/ask both sides + outcomes, 15.7k BTC 5m rounds; powered all three ask tests |
| [marvingozo/polymarket-tick-level-orderbook-dataset](https://www.kaggle.com/datasets/marvingozo/polymarket-tick-level-orderbook-dataset) | `archive (1).zip` (41 GB) | ✅ useful (L2 depth, maker-fill sim). ⚠️ **CC-BY-NC — research only** |
| [eimadevyni/btcusdt-market-lake](https://www.kaggle.com/datasets/eimadevyni/btcusdt-market-lake) | `archive (4).zip` (7.9 GB) | ✅ Binance FUTURES aggTrades 2025+ (geo-blocked live here); research-only timing probes |
| [luciferforge/polymarket-engineered-features](https://www.kaggle.com/datasets/luciferforge/polymarket-engineered-features) | `archive (6).zip` (0.1 GB) | 🟡 all-market features; not BTC-5m-specific |
| [krrdev1/binance-btcusdt-l3-market-microstructure-data](https://www.kaggle.com/datasets/krrdev1/binance-btcusdt-l3-market-microstructure-data) | `archive (5).zip` (0.2 GB) | 🟡 one-day spot L3 sample; schema scout |
| [adamatractor/institutional-crypto-l2-orderbook-30lvl-1m-5m](https://www.kaggle.com/datasets/adamatractor/institutional-crypto-l2-orderbook-30lvl-1m-5m) | `archive (3).zip` (0.05 GB) | 🟡 low value (depth proven no-edge for direction) |
| [marvingozo/hyperliquid-l1-order-flow-microstructure-10-perps](https://www.kaggle.com/datasets/marvingozo/hyperliquid-l1-order-flow-microstructure-10-perps) | `archive.zip` (83 GB) | ❌ niche; **reclaimable disk** |
| [marvingozo/hyperliquid-btc-high-frequency-microstructure](https://www.kaggle.com/datasets/marvingozo/hyperliquid-btc-high-frequency-microstructure) | `archive (2).zip` (60 GB) | ❌ same — 143 GB combined reclaimable |
| [luciferforge/polymarket-historical-prices](https://www.kaggle.com/datasets/luciferforge/polymarket-historical-prices) | `archive (9).zip` (2 MB) | ❌ sample teaser |
| [luciferforge/polymarket-markets-prices-sample-2026](https://www.kaggle.com/datasets/luciferforge/polymarket-markets-prices-sample-2026) | `archive (10).zip` (2 MB) | ❌ sample teaser |
| [debayan31415/polymarket-5-minutes-btc-up-down-data](https://www.kaggle.com/datasets/debayan31415/polymarket-5-minutes-btc-up-down-data) | **not identified among downloads** (needed API auth per the hunt audit) | UNAUDITED — re-download if ever needed |
| Notebook: [can-you-beat-a-prediction-market](https://www.kaggle.com/code/luciferforge/can-you-beat-a-prediction-market-polymarket) | — | reference reading |

## FROZEN RULE: `LATE_LEADER_30S_V1`  (adopted paper rule — do NOT tune, try to KILL it live)

```text
Market:    BTC 5m Up/Down only
Timing:    enter at ~30 seconds before settlement
Side:      buy the current leader only
Price:     executable ASK (never last trade); skip ask < 0.60;
           require ask <= fair_value - fee - 0.03 buffer
Execution: one first qualifying entry per round; size = quarter-Kelly; PAPER ONLY
No extra BTC-side gates: no trend gate, no $20-lead gate, no 15m agreement, no Champion SETUP requirement
Champion role: risk veto, fee math, sizing, data-quality/book-quality gate
```

Evidence (14,226 settled rounds, full null protocol, `probe_champion_ask_lift.py`): EV +2.5c/share,
95%-LB +1.6c, PF 1.22, 8/8 weeks positive; every BTC-side gate FAILED the shuffled-gate null (p 0.74–0.97)
— the ask is the sufficient statistic. Full tables in `KAGGLE_DATA_TEST_PLAN_2026-07-02.md`.

**Live validation gate (the ONLY remaining question):** does the SAME rule stay positive on June–July
live markets with real /book ask, ask size, VWAP for intended size, <1–3s latency, official settlements,
one entry per round — plus ask-bucket/week stability and the trailing-side control? Recorders collect
this now. No re-tuning permitted; the rule passes or dies as written.

**LIVE FORWARD LEDGER WIRED (2026-07-02):** the app now executes the rule on paper automatically —
at 20–32s left in every 5m round it reads the same fresh bridge quote the champion sees, buys the
market leader (higher bid side) at its ask per the frozen gates (skip <0.60), logs to
`rule_paper_trades` (SKIP/NO_QUOTE rows kept for honest denominators), settles at resolution
(pnl = settle − ask − fee, hold-to-settle), and the **📜 RULE STATUS tile** (Polymarket tab, above the
round-state board) tracks n/500 · EV · LB · PF · weeks+ · recorder liveness against the pre-declared
promotion thresholds. Ledger: `database.log_rule_paper_trade / settle_rule_paper_trades /
rule_paper_summary`. Open follow-up: an offline cross-check replay against the recorder's
`execution_layer.duckdb` (schema inspectable only while recorders are stopped — write it then).

## KILLED: `EARLY_LEADER_SCALP_V1`  (tested 2026-07-02, `probe_early_leader_scalp.py`)

Spec (frozen before running): enter 180–60s left, leader ask 0.50–0.70, spread ≤2c, BTC |dist| ≥ $10,
enter at ASK, exit at BID, TP +5c / SL −3c / 30s time stop, fees both sides, one entry per round.

| latency | n | win | mean pnl | PF | weeks positive |
|---|---:|---:|---:|---:|---|
| 0s | 8,076 | 36.8% | **−4.11c/share** | 0.28 | **0/9** |
| 1s | 8,076 | 35.7% | −4.33c | 0.31 | 0/9 |

**Why it dies (the instructive part):** the 36.8% win rate ≈ the martingale expectation for TP+5/SL−3
(3/8 = 37.5%). The share-price path in the 180–60s window is **efficient** — there is no early repricing
drift to harvest — so the strategy simply pays the spread + two taker fees every trade (~4c). This also
explains WHY the late-window settlement rule is the surviving edge: it crosses the spread ONCE and holds
to resolution instead of betting on path.

**What could revive an early strategy (untested, needs live L2):** maker-side entries (join the bid —
kills half the cost), and the BTC-shock underreaction trigger (needs sub-second quotes; the 1s Kaggle
cadence cannot see it; our `l2_recorder` can). Any such variant is a NEW rule, tested from scratch —
not a tuning of this dead one.

## GATE-FAILED (kept dormant): `FADE_V6_1S` — 1-second fade retrain (2026-07-03, `train_fade_model_1s.py`)

Context: v5's audit fixes (causal features, ambiguous-bar exclusion) revealed the 1m fade ceiling is
top-decile ~0.44 → the live P(fade)≥0.55 gate never fires → fade dormant. Quarterly label slicing
proved the 400d window/barrier drift was NOT the cause (base reach-rate stable 17–23% every quarter
despite BTC $115k→$61k). Root limit = 1-minute resolution. Fix attempt: rebuild at **1-second bars**
from the cached aggTrades (150d, 316,874 events, true overshoot, unambiguous TP/stop ordering,
restored live parity). **Pre-declared joint gates: OOS AUC ≥ 0.70 AND strict top-decile ≥ 0.55.**

| cell | n | AUC | top10% | gate |
|---|---:|---:|---:|---|
| $30 5m | 116,286 | 0.689 | 0.625 | **FAIL** (AUC by 0.011) |
| $50 5m | 88,072 | 0.706 | 0.540 | **FAIL** (top10 by 0.010) |
| $30/$50 15m | 60k/52k | 0.62 | 0.63/0.52 | FAIL |

1s resolution genuinely helped (top-decile 0.44 → 0.625) — but no cell passed BOTH gates, so the
model was **not saved** and the fade stays dormant. Near-misses are precisely what frozen gates
exist for. Legitimate next levers (same gates, no bending): more 1s history (build the full 400d at
1s), or live microstructure features once the 1s recorders accumulate months. The v3 "69%" figure is
retracted as partly leak-inflated (touch-bar extremes contained post-entry movement).

## KILLED: `TP_OR_SETTLE_5M_V1`  (tested 2026-07-02, `backend/probe_tp_or_settle.py`)

Operator idea: buy the leader EARLY, take profit at +20–50% of entry (a $1 bet → exit at 20–50c gain),
otherwise ride to settlement (no stop-loss leg; spread crossed twice only on winners). Frozen spec:
entry 240–180s left, leader ask 0.50–0.70, spread ≤2c, one entry/round, fees, latency 0/1s.
n=11,939 entries, avg ask 61.1c, settle-win 62.2%:

| variant | EV/share | 95% LB | PF | TP hit | weeks + |
|---|---:|---:|---:|---:|---|
| A hold-to-settle (same entries) | −0.49c | −1.35c | 0.98 | — | 2/9 |
| B TP +20% else settle | **−4.41c** | −4.96c | 0.67 | 78% | 0/9 |
| C TP +35% else settle | −3.13c | −3.81c | 0.83 | 71% | 0/9 |
| D TP +50% else settle | −1.79c | −2.56c | 0.91 | 48% | 1/9 |

**Two lessons:** (1) EARLY entry itself is already ≈zero/negative (A: −0.5c — matches the calibration
curve: the market is efficient until the final minute). (2) The TP overlay is **monotonically worse the
tighter the target** — the optional-stopping signature: every profit-take pays an exit fee + spread AND
caps a winner that would on average converge to $1, while losers still ride to 0. 78% of +20% TPs hit
and the variant STILL loses 4.4c/share. **Profit-taking on early entries donates money; the surviving
structure remains: enter LATE (~30s), hold to settlement.**

**15m version:** NO 15m quote history exists in any downloaded archive (archive 7 and archive 1 BTC
up/downs are both 5-minute windows). The 15m TP-or-settle test is QUEUED for the live recorder (records
both horizons since 2026-07-02). Same optional-stopping math applies; test before believing otherwise.

## KILLED: `STRADDLE_SCALP_5M_V1` — "bet both ways, ride the swings" (2026-07-02, `probe_straddle_scalp.py`)

Operator idea: near the anchor (book ~50/50, reversal possible), buy BOTH sides; sell each leg at
+20–50% as price swings each way; un-TP'd legs settle (one always pays $1). Frozen spec: entry 270–180s
left, max(bid) ≤ 0.55, both spreads ≤ 2c, one straddle/round, fees, latency 0/1s. n=9,944 straddles,
avg cost 104.5c (both asks + fees) vs a 100c guaranteed settle floor:

| TP | EV/straddle | 95% LB | PF | both legs TP'd | winner-sold-loser-kept | weeks + |
|---|---:|---:|---:|---:|---:|---|
| +20% | **−10.70c** | −11.33c | 0.48 | **52%** | 48% | 0/9 |
| +35% | −10.27c | −10.95c | 0.55 | 35% | 65% | 0/9 |
| +50% | −9.50c | −10.17c | 0.55 | 23% | 77% | 0/9 |

**The operator's swing intuition is CORRECT — and it still loses.** The both-ways ride succeeds **52%**
of rounds at +20% (the share path genuinely swings both ways half the time). The arithmetic that kills
it: both-TP pays ≈ **+18c**, but the failure mode (one-way trend: you sold the eventual winner at +20%
and kept the loser to $0) costs ≈ **−43c**. 0.52·(+18) + 0.48·(−43) ≈ **−11c** — the straddle premium
(~104.5c for a 100c floor) prices the round-trip frequency EXACTLY. `tp_loser_kept_winner` (the jackpot)
happens ~0% — the leg that TPs first is nearly always the eventual winner, sold cheap. There is no free
volatility: chop pays 1×, trend costs 4×, and the market knows the ratio. Multiple shots per window just
repeat a −10c lottery. The worst per-trade EV of any rule tested in this project.
