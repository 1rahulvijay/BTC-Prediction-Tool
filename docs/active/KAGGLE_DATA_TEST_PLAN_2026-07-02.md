# Kaggle Data — Inventory & Test Plan (2026-07-02)

10 archives (~192 GB zipped) in `Kaggle Data/`. Inventoried WITHOUT extraction (zip listings + READMEs).
Gate rules from `PM_DATASET_HUNT_AUDIT_2026-07-02.md` apply to every file: audit token mapping, dedup by
round, check for the barbell/trade-price confound, Wilson-LB everything, leak-audit any feature vs label.

## Inventory

| Archive | Size (zip→raw) | Contents | What it is |
|---|---|---|---|
| `archive (1).zip` | 41→42 GB | `orderbook/` daily parquets (Mar 2026, 21 days), `snapshots/` L2 JSON, `features/ml_features_1m_v2.parquet`, `labels/market_targets.parquet`, `labels/trades.parquet` | **Polymarket tick-level orderbook** — 5.5B ticks, L2 depth, UMA-verified resolutions. ⚠️ **CC-BY-NC-4.0 (non-commercial only)** |
| `archive (2).zip` | 60→63 GB | `hl_book/hl_book_2026-05-XX.parquet` (May 2026 dailies) | Hyperliquid (perp) L2 book history |
| `archive.zip` | 83→87 GB | `book/book_2026-05-XX.parquet` + `data_quality_summary.json` | Order-book snapshots May 2026 (venue TBC in audit) |
| `archive (4).zip` | 7.9→22 GB | `raw/agg_trades/market=futures/symbol=BTCUSDT/...` (2025+, 232 files) | **Binance FUTURES aggTrades** — the feed geo-blocked live on this box |
| `archive (7).zip` | 0.5→0.7 GB | `btc_ticks.parquet`, `btc_markets.parquet` + bnb/doge/eth/sol/xrp | Polymarket up/down per-coin ticks + market metadata |
| `archive (5).zip` | 0.2 GB | orderbook diffs/snapshots/trades, single day 2026-04-18 | One-day L2 sample (schema scout for the big ones) |
| `archive (6).zip` | 0.1 GB | `markets.parquet` (12.5k markets), `market_features.parquet`, `market_snapshots_april.parquet` (4M rows, 15-min) | Polymarket engineered features, April 2026 |
| `archive (3).zip` | 0.05→0.2 GB | `{COIN}_1m_depth30.csv`, `_1m/_5m_ohlcv.csv` × 12 coins | Multi-coin 1m OHLCV + depth30 |
| `archive (9/10).zip` | <2 MB each | `markets.csv`, `prices_sample.csv` | Tiny samples |

## ⚠️ License flags (read before building on them)
- **`archive (1)` is CC-BY-NC-4.0** — academic/non-commercial ONLY. Fine for research probes and
  validating our own recorders' math; do NOT build the live betting pipeline on it. Our own recorders
  produce the same kind of data license-free.
- The others: check each README/Kaggle page before any production use; default to research-only.

## What each unlocks (mapped to our open questions)

1. **Executable-ask calibration (THE make-or-break)** — `archive (1)` orderbook + resolutions, cross-checked
   by `archive (7)` ticks: for BTC up/down rounds, the resting ASK (not trade price) vs realized win rate.
   This is the test the HF trade-price study could not answer (+8 to +35pp gaps were trade-price only).
   → extends the live recorder's answer from days to months of history.
2. **Maker-fill simulation (Lever 3, cost cut)** — `archive (1)`/`archive (5)` L2 diffs: if we rest a bid at
   the fee-capped price, how often does it fill, and how adverse are the fills?
3. **Futures-flow features with parity** — `archive (4)` Binance futures aggTrades: backfill perp CVD /
   large-trade features 2025→2026. Rule 3 applies: only useful for TIMING/selectivity heads (direction is
   dead); and live parity is impossible while the WS is geo-blocked — so research-only unless a proxy feed
   is found. Honest expectation: modest.
4. **HF study extension** — `archive (7)` ticks + `archive (6)` snapshots: re-run `probe_hf_pathstate_tests`
   + market calibration on months (vs March-only, 5.9k rounds). Watch for the same latency/barbell confounds.
5. **Cross-venue book (Hyperliquid)** — `archive (2)`: does perp book pressure lead Polymarket repricing?
   Research probe only; depth was already proven useless for 5m direction (AUC 0.53) — the question here is
   REPRICING SPEED, not direction.

## Test order (cheap → expensive, each gated before the next)

| # | Test | Data | Script to write | Gate to pass |
|---|---|---|---|---|
| 1 | Schema + integrity audit | (5), (6), (7), (9), (10) — small | `audit_kaggle_datasets.py --quick` | columns/timestamps/dedup sane |
| 2 | Token/market mapping audit | (7) btc_markets vs our HF mapping | reuse `audit_hf_trades_token_mapping.py` | mapping agrees |
| 3 | HF extension (calibration + path-state) | (7) + (6) | extend existing probes | replicates March findings |
| 4 | **Executable-ask calibration** | (1) orderbook + labels (stream members, NO full extract) | `probe_pm_book_ask_calibration.py` | ask-based gap ≥ 2c after fees at n≥1k rounds |
| 5 | Maker-fill simulation | (1)/(5) L2 diffs | `probe_maker_fill_sim.py` | fill≥40% with adverse-selection cost < taker fee |
| 6 | Futures-flow timing probe | (4) | extend `probe_futures_flow.py` | timing AUC lift > +0.01 OOS |
| 7 | Hyperliquid repricing lead | (2) | new probe | lead-lag > costs |

## ✅ FIRST RESULT (run 2026-07-02): executable-ask calibration — THE make-or-break, answered

`archive (7)` `btc_ticks.parquet` = per-second **executable top-of-book bid/ask for BOTH sides** of 14,226
settled BTC 5m rounds (2026-03-24 → 2026-05-18) + UMA outcomes. Test: buy the market's own leader
(higher-bid side) AT ITS ASK at a fixed checkpoint; EV = win% − ask − crypto taker fee (0.07·a·(1−a)).
Round-level, Wilson-LB:

| checkpoint | pooled 50–90c n | win% (LB) | conservative EV (LB basis) |
|---|---:|---|---|
| 120s left | 9,998 | 73.0% (72.1) | **−0.1c/share** (≈ efficient) |
| 60s left | 6,679 | 75.0% (74.0) | **+0.5c/share** |
| 30s left | 4,394 | 77.7% (76.5) | **+2.1c/share** (+3.1…+3.8c point-estimate across buckets) |

**Read (honest):**
1. The **+8–35pp HF trade-price gaps collapse to +1–4c at executable asks** — the latency/barbell confound
   was inflating them, as suspected. This is the honest baseline.
2. A real, positive, **late-concentrated** edge survives: ~+2–4c/share in the final 30–60s — structurally
   the SAME late-window edge as our P(hold) finding, now confirmed at executable prices on 14k rounds.
3. It is a **volume edge, not a jackpot**: ~2–4% per share per round; profit comes from frequency + the
   champion's SELECTION lifting win% above the naive pool.

**Caveats:** top-of-book only (no depth → size will slip); 1s cadence; no latency modeled; rounds missing
late quotes drop out (n shrinks toward 30s — possible selection effect); fee model = the app's 7% crypto
taker curve. License of archive (7) to be confirmed before any production reliance; our own recorders
reproduce this data going forward.

**Next test (the one that matters now): champion-gated lift.** Join these asks to our March HF snapshots /
research matrix (distance, keepers, P(hold) reconstruction) and measure: does the naive +2.1c LB EV rise
when filtered by our gates (P(hold)≥93%, ≥$20 lead, TREND archetype, 5m/15m agreement)? If the gates lift
EV to +4–6c LB, the paper agent has its entry rule; if not, naive-late-30s IS the rule.

## ✅ SECOND RESULT (2026-07-02): champion-gated lift test — the market already prices our gates

Joined the 14,226 rounds to `research_matrix_1m.parquet` BTC closes (anchor, distance at 180s/60s left,
15m-boundary anchor). Buy the market leader at its executable ask; gates evaluated at the 60s-left bar:

| filter | entry@30s n | win% | avg ask | EV | EV (Wilson-LB) |
|---|---:|---:|---:|---:|---:|
| BASELINE (all rounds) | 6,759 | 83.8% | 80.2c | **+2.5c** | **+1.6c** |
| lead ≥ $20 | 3,167 | 86.0% | 83.2c | +1.8c | +0.5c |
| + TREND (lead grew) | 1,497 | 86.0% | 84.0c | +1.1c | −0.8c |
| + 15m agrees | 1,192 | 85.9% | 84.1c | +0.9c | −1.2c |
| + market agrees w/ BTC | 1,142 | 86.2% | 84.7c | +0.6c | −1.5c |

**Within-ask-bucket check** (the decisive one): at a FIXED ask bucket, the $20-lead gate adds nothing
(70–90c: 85.4% vs 85.0%; 50–70c: gate is WORSE, 63.5% vs 66.7%).

**Honest conclusion — the hypothesis "our gates lift EV to +4–6c" is REJECTED:**
1. The gates DO raise win% (81→89%) — but **the ask rises faster** (79→88c). Polymarket traders see the
   same price path; BTC-derived state is already in the price. **The ask is the sufficient statistic.**
2. The best EV is the ask-conditioned naive rule: **≤30s left, buy the leader at ask ≤ ~0.90 → +2.5c
   point / +1.6c LB per share** (mid-priced asks 50–90c are where the EV lives; 90c+ adds little).
3. This is exactly the champion's existing form — `fair − ask − fee − buffer > 0` — NOT a setup-quality
   filter. The models' remaining live value for THIS trade: the fair-value anchor (P(hold)), risk vetoes,
   and abstention. Setup-quality gates should NOT be added to the entry rule; they select into pricier asks.
4. Paper-agent implication: speed + ask cap + fee math (already wired, incl. Kelly sizing) IS the rule.
   Open questions before micro-live: displayed size at those asks (tick `su/sau` size columns — next audit),
   execution latency inside 30s, and regime persistence beyond Mar–May.

## ✅ THIRD RESULT (2026-07-02): full lift protocol WITH MANDATORY NULLS — `probe_champion_ask_lift.py`

Frozen rule family: late-window leader / actual executable ask / fees included / one entry per round /
gates as selectors only / nulls mandatory. **Honest limit:** the live champion ledger starts 2026-06-18 —
zero overlap with these asks (end 2026-05-18) — so arms C–F (real P(hold)/SETUP) are NOT fakeable here
(rule 3: vol_60s_pct has no 1m-parity reconstruction); gates G–J are the 1m-parity reconstructions.
The TRUE ledger-joined test accumulates on the live recorders from 2026-07-02.

| arm (entry @30s) | n | win% (LB) | avg ask | EV | EV(LB) | PF | N1 shuffled-gate | N3 ask-matched |
|---|---:|---|---:|---:|---:|---:|---|---|
| **A. every leader** | 6,759 | 83.8 (82.9) | 80.2c | **+2.5c** | **+1.6c** | **1.22** | — | — |
| G. lead≥$20 | 3,167 | 86.0 (84.7) | 83.2c | +1.8c | +0.5c | 1.18 | p=0.95 **adds nothing** | explained |
| H. +TREND | 1,497 | 86.0 (84.2) | 84.0c | +1.1c | −0.8c | 1.12 | p=0.97 **adds nothing** | explained |
| I/J. +15m agree (all strict) | 1,192 | 85.9 (83.8) | 84.1c | +0.9c | −1.2c | 1.09 | p=0.96 **adds nothing** | explained |

Nulls: **N2 trailing-side** −6.0c EV (sanity holds: labels/joins are real). **N4 week stability: 8/8 weeks
EV>0** (+1.9…+3.7c). **N5 ask buckets @30s (LB):** 0.5–0.6 **−0.6c** / 0.6–0.7 +0.8c / 0.7–0.8 +1.3c /
0.8–0.9 +1.5c. Entry @60s baseline is weaker (+0.8c LB, PF 1.13, two ~0 weeks) — 30s is the window.

### RULING (per the pre-declared promotion thresholds)
- **The killer check FAILED for every gate**: shuffled-gate nulls (p=0.74–0.97) meet or beat the real
  gates; ask-matched controls explain the rest. The gates select common late-leader states the market
  already prices — they ADD NO INFORMATION and shrink EV by selecting into pricier asks.
- **The BASELINE @30s PASSES promotion**: n=6,759 ✓ · EV +2.5c ✓ · LB +1.6c>0 ✓ · PF 1.22 ✓ ·
  8/8 weeks positive ✓ · nulls behave ✓.

**Paper-agent entry rule (adopted, paper only):**
```
In the FINAL ~30s of a 5m round: buy the CURRENT LEADER at its executable ask,
skip asks below ~0.60 (negative-LB bucket), one entry per round, fees + 3c buffer in the price cap,
size by quarter-Kelly. NO BTC-side setup gates. Champion's role = risk veto + fee math + sizing only.
```
**Real-money remains gated on the live recorder**: same effect on June–July data (regime persistence),
displayed depth at the ask, sub-30s execution latency, VWAP for intended size, no threshold re-tuning.

## Practical constraints (this laptop)
- **Do NOT extract the 41/60/83 GB zips** — stream individual parquet members with `zipfile` +
  `pyarrow` (members are seekable), or extract single days to the scratchpad and delete after.
- 16 GB RAM: process day-by-day, columns-pruned (`columns=[...]`), never full-file `read_parquet`.
- Big-zip tests run only while the app/recorders are idle if RAM pressure appears.
