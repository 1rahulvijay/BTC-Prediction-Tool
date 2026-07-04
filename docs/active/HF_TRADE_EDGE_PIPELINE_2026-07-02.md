# HF Trade-Price Edge Pipeline — 2026-07-02

The first historical test of **our P(Hold) vs the actual Polymarket traded price**, built from the HF
`obadiaha/polymarket-crypto-5m-15m` **trades** subset (the orderbook was killed as a barbell — see
`HF_POLYMARKET_DATASET_AUDIT`).

> **⚠️ CORRECTED by the null tests (`HF_EDGE_ROBUSTNESS_2026-07-02.md`).** The headline "+27% P(Hold) edge"
> below is **NOT a P(Hold) edge** — a shuffle-null (permuting P(Hold)) still returns **+25.7% ROI** (≈ the real
> +27.0%), so **P(Hold) contributes almost nothing**; the `edge≥2c` filter simply selects *cheaper* leaders. The
> real, P(Hold)-**independent** signal is structural: **buying the leader every round (no filter) already wins
> 65.6% at price 0.574 → +14.3% ROI** — i.e. *leaders are underpriced in the HF trade data*. The pipeline is
> internally consistent (invert/trailing loses −24.9%). This is **executed-trade only** and highly suspicious
> given the barbell book (real ask may be ~0.90, not 0.57 — the trades could be sellers hitting bids). And the
> snapshots are **March-only** (no Jan/Feb cross-period check). **Net: not a P(Hold) edge; a "leaders underpriced
> in trades" observation that is unproven for fillability and must be validated on the live /book.**

## The pipeline (4 scripts, honest guardrails)
| # | Script | Output | Result |
|---|---|---|---|
| 1 | `audit_hf_trades_token_mapping.py` | `token_map.parquet` | token→UP/DOWN via resolution + winner→1.0: **5,920/7,905 clean (75%)**; 25% low-liquidity quarantined. Separation median 0.919 (winner 0.966, loser 0.047). |
| 2 | `build_pm_hf_trade_snapshots.py` | `pm_hf_trade_snapshots.parquet` | **41,390 snapshots / 5,893 rounds** at fixed seconds-left checkpoints: UP/DOWN executed price (last + 30s VWAP), trade count/vol, BTC-vs-anchor, **P(Hold) backfilled with the EXACT live 5-feature persistence head**, settled side. Clean markets only. |
| 3 | (P(Hold) backfill, inline in #2) | — | `abs_distance_pct, seconds_left, vol_60s_pct, horizon, dist_vol_ratio` → `clf` → `iso`, identical to live serving (`2026-06-21-keeper-dual-perhorizon-iso`). |
| 4 | `analyze_pm_hf_trade_edge.py` | `HF_TRADE_EDGE_ANALYSIS` | **leader-only** edge (no fabricated symmetric prob), snapshot **and** round-level. |

**Integrity choices (from operator guardrails):** trade **only the currently-leading side** (`edge =
P(Hold_leader) − executed_price − buffer`) — the model predicts "the leader holds," so we never fabricate a
symmetric opposite probability; **round-level = first qualifying entry per round** (kills correlated-snapshot
fake confidence); clean token maps only; timestamp inside round; `seconds_left > 0`; causal (P(Hold) + price
use only past data, outcome is future).

## The headline (round-level @2c buffer — the honest number)
| n rounds | leader win | Wilson-LB | avg price | breakeven | ROI | PF |
|---|---|---|---|---|---|---|
| 5,269 | **59.6%** | **58.3%** | 0.470 | ~47% | **+27%** | 1.87 |

Buying the leading side when `P(Hold) − price ≥ 0.02` won ~60% at ~47¢, **Wilson-LB (58%) well above the 47%
breakeven**, over 5,269 rounds. Concentrated in **5m** (61.6%) and **early windows** (240s left ~60.6%); **15m
is much weaker** (54.1%, the 720s-left cut barely +EV). Robust to the buffer (1c–5c all similar).

## Why this is "THESIS ALIVE", not "edge found" — three hard caveats
1. **Fillability (the whole point of the caveat).** The leader traded at ~0.47, but the historical *orderbook*
   was a barbell (ask ~0.90). Trades occurred at ~0.5 so there *was* activity, but **we cannot prove you could
   BUY the leader at 0.47** rather than paying a higher resting ask. That alone can erase the edge. **Only the
   live /book recorder resolves it.**
2. **Train/serve gap.** The persistence model (trained ~June) is applied to Jan–Mar. It is the *filter*, not the
   outcome (the 59.6% win is empirical), but a shifted filter changes selection.
3. **Not uniform** — the signal is a 5m/early-window phenomenon; 15m is weak.

## The ONLY next step (no live trading)
Confirm the **same buckets** on **live /book ask + depth + edge-duration + settlement** via the recorder
(`start_recorder.bat` + `run_polymarket_l2_recorder.bat`). Positive there = executable edge; this HF result
merely says it is **worth** that confirmation. A trade price is not an executable resting ask.

## Status line (corrected by the null tests)
```
HF trades LEADER-PRICE ANOMALY:  POSITIVE historically (buy every leader = +14% ROI; leaders underpriced in trades)
P(Hold) model edge:              NOT PROVEN — shuffled-P(Hold) null returns +25.7% ≈ real +27.0%; P(Hold) adds ~nothing
Executable proof:                NOT PROVEN — trade price ≠ resting ask; barbell historical book; March-only
Real-money status:               PAPER ONLY
Next:                            live /book validation of the CHEAP-LEADER anomaly (every/cheap/P(Hold)/shuffle/trailing
                                 buckets) on actual ask + depth + edge-duration + settlement — do NOT trade
```
The P(Hold)-filtered result does not survive a shuffled-P(Hold) null as a model-specific edge. The edge-shaped result
is explained mostly by buying the current leader at low executed prices in the HF trades data. Treat as a
**leader-price anomaly requiring independent live /book validation, not a model edge.**
