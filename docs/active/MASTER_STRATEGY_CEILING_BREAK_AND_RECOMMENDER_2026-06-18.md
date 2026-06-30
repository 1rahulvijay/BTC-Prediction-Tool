# Master Strategy — Ceiling-Break & the Live Market Recommender (2026-06-18)

**This is THE forward strategy.** It merges four inputs into one disciplined, implementation-ready plan:
the Final Specialist-Head + Champion Plan, the "Current Truth 2026-06-18" note, the ceiling-break
analysis (new data / new labels / new policy), and the "Netflix-style Market Recommender" vision.

Companions (authoritative per topic): [V10_CONSOLIDATED](V10_CONSOLIDATED_MASTER_AND_PROPOSALS_2026-06-18.md)
(architecture + version lineage) · [Validation ledger](CLAUDE_ENHANCEMENTS_AND_VALIDATION_LEDGER_2026-06-17.md)
(evidence) · [150d retrain](OVERNIGHT_150D_RETRAIN_RESULTS_2026-06-18.md) (latest numbers) ·
[POLYMARKET_BOT_MASTER](POLYMARKET_BOT_MASTER.md) (the one trade rule) · [Calibration monitor](CALIBRATION_MONITOR_2026-06-18.md).

> **The discipline this doc inherits from the ledger:** every model claim needs a number, a parity check,
> a calibration curve, and a promotion gate. Nothing is promoted on a nicer backtest.

---

## 1. The one truth everything obeys
Raw BTC UP/DOWN at 5m/15m is a **coin-flip**, measured ~15 ways — tabular, boosted trees, deep
sequence (PatchTST/iTransformer), feature sweeps, and live (**49.8% over 8,039 resolved rounds**,
CI [48.7, 50.9]; the 150d retrain's OOS direction backtest is net-negative at every horizon). **You do
not beat this with more models on the same OHLCV.** The ceiling moves only three ways: **new
information, new targets, new action policy.**

**Redefine "better" before doing anything:**
```
all-round accuracy   : may stay 50–53%   (do not chase this)
ACTED-decision acc.  : target 65–80%+    (via abstention + bucket quality)
profitability        : only when the market ask is mispriced after costs
```
The win is not "every prediction accurate." It is **"only the acted predictions much better."**

---

## 2. Where we are today (built + validated — the floor we build on)
| Component | State |
|---|---|
| P(Hold) / price-to-beat | ✅ calibrated (P≥0.95 → 97.2% realized); **1m/3m drift flagged** by the monitor |
| Big-move / activity gate | ✅ 150d AUC 0.70–0.83, generalizes on the 2% holdout |
| **Big-drop risk** | ✅ strongest new risk head (held-out 0.60–0.69) |
| Directional up/down | ✅ confirmation-only (AUC rose with selective buckets, but top-5% precision stays low) |
| Quantile range (signed-quantile) | ✅ 80% CQR coverage |
| Champion validator | ✅ rules-first, strict, edge-gated; meta layer data-gated |
| Probability buckets | ✅ leak-free OOF, monotonic; the trust layer |
| Calibration monitor | ✅ live drift report (found ~2pt top-tier P(hold) optimism) |
| Recorder (btc-updown-5m/15m) | ✅ official settlement fixed (364 outcomes); **only 4 joined quote rounds → make-or-break still open** |

Auto-derived dollar buckets (p75/p90/p97, self-calibrating) + a 98/2 honest holdout are in. Direction
is demoted to confirmation everywhere. This is the validated base the strategy below extends.

---

## 3. The three ceiling-break levers (the only ones that can work)

### 3.1 New INFORMATION (data the current model does not already see)
More indicators on the same candles will not help. Add data the OHLCV doesn't contain:
- **L2 order book — RECORD-FORWARD ONLY** (no fake historical depth; the project already proved none
  exists free): microprice, OFI (order-flow imbalance), book imbalance, depth slope, spread, wall
  pull/refill. *Expectation: small on raw direction; useful on top-confidence entries + execution.*
- **Cross-exchange lead-lag** (Binance / Coinbase / Bybit / OKX / Deribit): who reprices first, venue
  spreads, 1s/3s/5s lead-lag. *More likely to help short-horizon than another RSI.*
- **Liquidations + Open Interest**: bursts, imbalance, OI acceleration, basis/funding shock. *Aligns
  with **big-drop**, our strongest working signal — fake-breakout + range-expansion detection.*
- **Polymarket ask history (THE bot data)**: UP/DOWN ask, spread, depth@1/2/3c, ask-change speed,
  fair-value-change speed, book staleness, edge duration. *This is where profit can actually exist.*

### 3.2 New TARGETS (better labels than close-direction)
- **Triple-barrier** (upper-first / lower-first / timeout): measures the **path**, not the close — BTC
  can dump-and-bounce; close-direction misses it. Per horizon (5m/15m).
- **Big-drop** (future low ≤ −T bps): the fat-left-tail; already the strongest new head.
- **Line-cross-from-now**: flip-risk of the side currently ahead.
- **Profitable-after-costs** (meta-label): the only label that matters for the *bot*.

### 3.3 New POLICY (abstention + bucket-quality + edge gate)
- **Abstain on 90–98% of weak cases** (WAIT/AVOID are successes, not failures).
- A head influences the champion **only in probability buckets with proven event-rate lift + monotonic
  deciles + calibration** (the bucket reports already exist).
- **Act only when bucket-quality AND fair-value edge agree** — never on a model output alone.

---

## 4. The Live Market Recommender (the "Netflix" architecture)
Reframe the product: not *"one model predicts direction"* but **a live recommendation system that learns
which ACTION works in which market context** — "markets like this usually did X," the trading analogue of
"people like you also watched." It **layers on top of the existing heads**, it does not replace them.

```
Layer 1  Live feature engine     multi-timeframe (1s…30m) via ring buffers; trend/vol/range/flow/L2/x-venue
Layer 2  Specialist heads        EXISTING: P(Hold), big-move, big-drop, directional(confirm), activity, band
Layer 3  Similar-setup memory    embed the live setup → kNN over history → "n=842, dropped 66%, Wilson-LB 88%"
                                  (seeded by the A10 setup_fingerprint recorder already in the DB)
Layer 4  Online CALIBRATION      adjust PROBABILITIES live by horizon/regime/bucket — NOT model weights
                                  (fixes the measured P(hold) 1m/3m drift)
Layer 5  Online head-weighting   low-vol→P(Hold)+range · high-vol→big-drop+line-cross · trend→momentum+activity · chop→avoid
Layer 6  Champion recommender    ACTION + direction(confirm) + expected zone + confidence + reason + invalidation + similar-setup stats
Layer 7  Record/learn loop       every recommendation→outcome → bucket stats, calibration, reliability, meta-skip data
(later)  Contextual bandit       LinUCB / Thompson, PAPER-ONLY: "which action gave best reward in this context"
```

**Update cadence (critical — markets are adversarial, not stable preferences):**
- **Fast (minutes/hours):** calibration, bucket event-rates, regime weights, thresholds.
- **Slow (nightly/weekly):** retrain the heads / quantile / meta-champion.
- **Never per tick** — that learns noise. *Observe fast, calibrate fast, retrain slow, promote on evidence.*

---

## 5. The make-or-break (unchanged — gates ALL execution)
```
BET ONLY WHEN:  calibrated_fair_value − market_ask − costs − safety_buffer  >  required_edge
```
Everything above sharpens the *inputs* to this gate; none of it is edge by itself. Answered only by the
**recorder** (`btc-updown-5m/15m`, now running). Required output: the fair/ask/win-rate/ROI table by
horizon × buffer (1¢/2¢/3¢) with Wilson lower bounds, after enough joined quote+outcome rounds.
- **Positive after costs →** paper agent → micro-live → scale (each gated).
- **Flat/negative →** ship the honest probability/risk dashboard; do **not** build live execution.
Both are acceptable, truthful outcomes.

---

## 6. Prioritized build sequence (realistic, gated)
1. **Recalibrate P(Hold) by horizon** — the monitor already flags 1m/3m drift. Cheap, no base retrain.
2. **Bucket-quality gate in the champion** — a head influences the decision only where its OOF bucket
   proves event-rate lift + monotonic deciles + calibration; else downgrade.
3. **Triple-barrier labels + model** (offline experiment; full validation gate before any wiring).
4. **Start the record-forward data clocks** — L2 order book + cross-exchange + liq/OI. Record NOW,
   model LATER (no backfill — historical depth isn't free/reliable).
5. **Market-lag / ask-underreaction analyzer** (the bot's real edge path) — on recorder data.
6. **Similar-setup memory engine** — kNN over `setup_fingerprint`; surface "n similar, win-rate, Wilson-LB."
7. **Edge replay** on recorder data — "what would the champion have made after costs?" Rank which
   filters improved ROI vs only cut trades.
8. **Meta-skip / contextual bandit** — ONLY after enough resolved champion snapshots (≥500).
9. **Paper → micro-live → scale** — ONLY after the recorder edge table is positive after costs.

---

## 7. Stop-list (do not rebuild)
More raw-direction ensembles · more deep-sequence promotion into live · exact-price-as-truth ·
big_up/big_down as direct trade triggers · 160-feature research models without live parity ·
L2 **historical** order-book models (no reliable free depth) · snapshot-pooled accuracy claims ·
**aggressive online learning** (markets decay/adapt — calibrate fast, retrain slow). Direction stays in
the UI as confirmation only.

---

## 8. Validation gate (every new head/label/data source, before it touches the champion)
1. leak-free temporal split · 2. unseen OOS score · 3. top-N precision · 4. calibration curve ·
5. probability-bucket monotonicity · 6. parity vs the live feature builder · 7. stability by horizon ·
8. stability by seconds-left / regime · 9. no degradation to the existing champion flow ·
10. plain-English UI meaning defined. **No promotion on a nicer backtest.**

---

## 9. Honest reality (read before getting excited)
Markets are **adversarial**: patterns decay, other traders adapt, noise is high, feedback is delayed,
costs are real. So the recommender must **observe fast, calibrate fast, change thresholds carefully,
retrain slowly, and promote only on evidence.** The expected result is **not** every prediction accurate —
it is the **acted** predictions much better, through abstention + new data + a market-price edge. If the
recorder edge is flat after costs, the honest product is a **world-class probability/risk dashboard**, not
a bot. That is a legitimate, truthful outcome — and still the most useful BTC decision tool on this data.

---

### Final shape
```
8 small validated specialist heads
+ similar-setup memory  (the "markets like this" layer)
+ live probability calibration + bucket-quality trust
+ regime-aware head weighting
+ 1 strict champion recommender (action + zone + reason + invalidation)
+ record/learn loop  (fast calibration, slow retrain)
+ the Polymarket fair-value-vs-ask edge gate  (the only path to a real bet)
```
The correct next version is **not a larger model** — it is **a stricter, self-calibrating decision system
that acts rarely and only on proven edge.**
