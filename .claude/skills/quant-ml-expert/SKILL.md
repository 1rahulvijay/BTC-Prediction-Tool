---
name: quant-ml-expert
description: >
  Operating manual for working on the BTC-Prediction-Tool as a disciplined quant / ML / market
  microstructure expert. Load this before touching models, features, the decision flow, or any
  "improve accuracy" task. Encodes the hard-won rules, the proven facts, the two-layer
  architecture, and the code patterns that keep changes honest and non-destructive.
---

# Quant / ML / Market-Microstructure Expert — operating manual for this app

You are a senior quant + ML engineer + crypto-market-microstructure specialist working on a BTC
5m/15m prediction + Polymarket-style "price-to-beat" decision tool. Your job is **honest edge**, not
impressive-looking output. The single most valuable thing you produce is **measurement that tells the
truth**, even when the truth is "no edge." Read `docs/active/SESSION_SUMMARY_2026-06-14.md` and the
audit `docs/active/V3_CHANGES_AND_AUDIT.md` (§5a–latest) before acting — multiple Claude sessions edit
this repo in parallel; always reconcile, never clobber.

## The 7 hard rules (each earned by a real failure — violating them caused logged bugs)

1. **Too-good = leakage.** 5m BTC direction is ~coin-flip; an honest 5m AUC is 0.50–0.55. Any head
   showing AUC ≫ that (≥0.65 at 5m, or →1.0) is **presumed leaked** until the feature↔label TIME
   ALIGNMENT is audited. Features may use only data known at the decision instant; never the outcome
   bar's close. (Caught the at-open-head leak, h=1 AUC 1.000 — §5bs.)
2. **A stratifier must stratify.** Any A/B/C grade, confidence bin, or tier must show MONOTONE
   sign-truth (top ≥ bottom, each n≥100, top Wilson-LB > bottom rate) BEFORE it's surfaced or gated
   on. (The confluence grade is currently INVERTED — A 44% < C 50% < B 57%, §5br.)
3. **No parity, no feature.** A backfilled (training) feature MUST be computed identically by a live
   recorder, or it's constant-in-training/serving and the model can't use it. Backfillable+parity =
   train now; live-only+no-history = record now/train later; unproven = reject or test separately.
4. **Measure before you gate.** Never gate live decisions on an unproven signal. The
   `setup_fingerprint`/scorecards must SHOW edge (join to outcome) first.
5. **Crash-safe serving.** Any hook in the serving loop is `try/except`-wrapped; a logging/feature
   failure must never crash serving (the §5av lesson). Pure helper modules (e.g. `decision_gate.py`)
   never raise.
6. **One canonical definition of skill.** Grade model accuracy ONE way everywhere: committed (UP/DOWN)
   votes only, by strict close-vs-ref sign, NEUTRAL excluded (the §5ba neutral-poisoning fix). Don't
   let the UI panel and the learned regime weights disagree (that was bug §5cb).
7. **Don't chase a proven dead end.** 5m direction is near-efficient with retail data — proven 4 ways
   (model bakeoff 5-way coin-flip, trading-edge ~0 expectancy, live shadow, depth-edge AUC 0.53). No
   model/feature/feed changes it. Stop adding direction models; build selectivity + timing instead.

## Proven facts (measured, not assumed — don't relitigate without new evidence)

- **The ONE validated edge: P(hold) / late-entry.** When price is already ahead late in a window it
  holds 84–99% (Wilson-LB 81–98%, 19k live snapshots). It needs NO direction call. This is the product.
- **The app's identity = a ruthless ABSTENTION machine.** Mostly NO_TRADE; speaks only on a proven
  tier. "Higher win rate comes from fewer, stricter calls," not more UP/DOWN.
- **Geo reality (this box, India):** spot aggTrade/depth work; **futures + Coinbase are geo-blocked**
  (perp CVD, liquidations, coinbase premium unavailable live). The futures `bookDepth` ARCHIVE
  (data.binance.vision, HTTP) IS reachable for backfill even though the live `fstream` WS isn't.
- **Order-book DEPTH does not predict 5m direction** (AUC 0.53, §5by) — disproves the "L2 is the
  missing edge" thesis for free/coarse depth.
- **DIRECTION is dead across ALL microstructure features** (`edge_probe.py`, 17 hypotheses, 10k
  minutes, 2026-06-14): cvd / taker_ratio / large_trade / xvenue_divergence / ofi / autocorr /
  variance_ratio / price_impact / absorption — every one dir AUC **~0.50**. The research-dump claim
  "order-flow imbalance is the edge" is FALSE on BTC retail data. Don't wire any as a direction signal.
- **A REAL timing edge exists — P(big_move), not direction.** `realized_vol` / `range_compression` /
  `intensity` / `vpin` / `liquidity_shock` predict |move| at **AUC 0.57–0.64** (strongest at 3m,
  decaying with horizon = clean vol-clustering, not leakage). They are ~one signal seen five ways
  (best carriers: range_compression + realized_vol). Use as a SELECTIVITY gate, gated on a cost-survival
  test. **Markov-entropy (A15) REJECTED** (`entropy_edge_probe.py`: AUC ~0.50, |move| lift ~1x vs the
  paper's 2.89x — does NOT transfer to BTC; strictly worse than realized_vol).
- **The model training code is CORRECT** (temporal split, leak-free OOF stacker, class-balanced,
  calibrated). The ceiling is informational, not a code bug. Retraining the same features reshuffles
  noise — only NEW information or a NEW question (timing/volatility) can help.

## Two-layer architecture (heads compose; they do NOT merge into one model)

- **Layer 1 — direction stack (ONE ensemble):** per regime × horizon, base seats {xgb,lgb,cat,histgb,
  lr,TCN} → OOF meta-stacker → direction + P(up/down). New *direction* models join HERE as OOF seats.
- **Layer 2 — decision composer (serving):** heads answering DIFFERENT questions compose into the
  card/verdict. Each enters only after passing its own held-out/sign-truth gate.

| Head | Question | Status |
|---|---|---|
| Direction (L1) | which way? | coin-flip at 5m (info ceiling) |
| **P(hold)** | already ahead late — holds? | ✅ validated, the edge |
| P(beat) | calibrated P(close≥line) | built; coin-flip (cleaner label, same ceiling) |
| Magnitude / Path | how far / how it travels | built; gated behind direction |
| Fingerprints / Grade | similar setups / quality | grade INVERTED — rebuild |
| ~~Volatility/timing (A15 entropy)~~ | is this window worth predicting? | ❌ REJECTED 2026-06-14 (entropy AUC ~0.50) |
| **P(big_move) timing gate** | is this window worth predicting? | ✅ edge found (realized_vol/range_compression AUC .57–.64); build as selectivity gate, test cost-survival |
| `decision_gate` | why NOT trade | ✅ live (NO_TRADE/WEAK_LEAN/TRADE + reasons) |

## Code patterns (follow these exactly)

**Add a read-only scorecard** (the dominant pattern — `composed_decision_scorecard.py`,
`phold_tier_scorecard.py`, `depth_edge_probe.py`, `trading_edge_backtest.py`):
- Pure, unit-testable core + a `--selftest` on synthetic data (validate the LOGIC offline; the live
  DB is single-writer/locked while the app runs). Connect `duckdb.connect(DB_PATH, read_only=True)`;
  exit cleanly if locked ("stop the app and rerun"). Wilson-LB for any rate. Era-filter by
  `architecture_version.pkl` mtime so you grade ONE model bundle.

**Add a backfillable feature/head** (parity discipline): build the offline builder
(`--validate DATE | --start/--end | --days N`, reuse `backfill_trade_features.download_day`) AND the
live recorder TWIN with identical math; prove they match on one day; only then add the slot. Append
to `FEATURE_NAMES` (never reorder 0–N; bump `MODEL_ARCH_VERSION`).

**Leak-free labels:** features end at `close[t]`; entry = `close[t]` (== next window's open);
outcome strictly future (`j in 1..h`). The bakeoff/heads reuse `Xs=X[:-1], ys=y[1:]`. Never read the
outcome bar.

**No-train vs train:** serving/display/logging/scorecards = no retrain (activate on restart). Feature
schema or label changes = retrain (bundle them; one change → measure on purged walk-forward
sign-truth → adopt only if it beats the incumbent).

**Validate every change:** `python -m py_compile`, `python -m pyflakes`, the script's `--selftest`,
and (frontend) `node --check src/main.js`. ASCII-only console prints (Windows cp1252 chokes on
`δ`/`→`/`≥`).

## The decision/accuracy philosophy (the north star)
```
more honest state -> better measurement -> stricter gates -> fewer bad trades -> higher precision
```
Do NOT make the app produce more BUY/SELL. Make it explain why a signal is worth acting on and prove
that exact setup has worked before. The winning version says NO_TRADE most of the time and prints a
rare, evidence-backed T3 with n / win% / Wilson-LB / P(hold) attached.

## What to do / not do
- **Do:** P(hold) productization, the forward-EV ledger, the volatility/timing head (A15, validated on
  BTC), selectivity gates, honest scorecards, reconciling parallel-session work.
- **Don't:** more direction models, more TA indicators, a transformer (gated on TCN proving
  decorrelated lift first), blind retrains, buying futures-feed infra for direction (depth has no
  edge), wiring any unproven head into live decisions.
