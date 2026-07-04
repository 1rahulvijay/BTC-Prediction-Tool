# App Enhancements & Tests — Master Catalog (2026-07-02)

**Purpose.** One place that captures **every** recommendation, enhancement idea, and testing idea for the
Polymarket BTC 5m/15m up/down app — proposed by the operator, by ChatGPT, and by me — each honestly labeled
by whether it can be tested today, needs the live recorder, or is a composition of other heads. This is a
*blueprint*, not a promise: nothing here is wired live, and every "edge" is a hypothesis until the live
recorder proves it on real ask + fill + settlement.

**How to read the status tags:**
- 🟢 **DONE** — already built and/or tested this cycle (see linked doc).
- 🟡 **TESTABLE-NOW** — can be run on existing historical data (HF trades, 1s/1m BTC matrix). Not yet done.
- 🔴 **RECORDER-GATED** — needs live Polymarket `/book` (ask/size/depth/edge-duration). No historical dataset
  supports it; the HF book is a barbell and decay is sub-second.
- 🧩 **COMPOSITION** — not a standalone model; combines other heads into a score/panel/gate.
- ⚫ **DEAD / RETRACTED** — tested and killed; kept here so we don't rebuild it.

**The reframe that governs all of this:** stop shipping an *UP/DOWN predictor* (proven coin-flip, ~49.8%) and
build a **Polymarket bet-quality engine** — *is this bet cheap? cheap for a good or bad reason? can I fill it?
will the edge survive? can I exit? should I wait?* The BTC-direction question is dead; the market-behavior
questions around the bet are where any remaining edge lives.

---

## 1. Current honest state (what we actually know)

| Finding | Verdict | Source |
|---|---|---|
| BTC 5m/15m **direction** | ⚫ Coin-flip (~49.8%), at information ceiling | many; PROFITABILITY_AND_BETTING_VALIDATION |
| **Fade / round-trip** ($30 touch → revert) | ⚫ Look-ahead leak — 80.6% of touches resolve in the same 1m candle; retracted | ANCHOR_ROUNDTRIP_180D; my retraction |
| **Binance bookDepth** features/veto | ⚫ Dead 3 ways (no lift, redundant with rv, veto removes 90 bad vs 2,078 good) | BOOKDEPTH_LIQUIDITY_PROBE / _VETO |
| **HF orderbook** dataset | ⚫ Systematic 0.10/0.90 barbell, $0.80 spread — unusable | HF_POLYMARKET_DATASET_AUDIT |
| **HF trades "+27% P(Hold) edge"** | ⚠️ NOT a P(Hold) edge (shuffle-null still +25.7%); it's P(Hold)-independent **leader underpricing** (+14% baseline) | HF_EDGE_ROBUSTNESS |
| **Leader-price calibration** | ⚠️ Leaders trade ~8–14pp below their win rate (round-level +8.2pp) — real in *trade* data, not asks | HF_MARKET_CALIBRATION |
| **Cheap-valid vs dangerous** | ⚠️ Partly "cheap for a reason" (SAFE +16pp, FRAGILE +5pp); bigger in high-vol/early = **latency-artifact signature** | HF_CHEAP_LEADER_DANGER |
| **Risk/path heads** (flip, shock, time-to-touch, opportunity) | 🟢 Built as SHADOW at AUC 0.82–0.89 | DECISION_HEAD_RESEARCH; ROUND_STATE_DECISION_PANEL |
| **Executable edge on live book** | ❓ Unproven — the only thing that can prove it is the live recorder | — |

**One-sentence status:** the single positive signal (leaders underpriced in trade data) is most likely a
**Binance-leads-the-oracle latency race**, not a calm mispricing — and whether it is exploitable can *only*
be answered by the live recorder measuring real ask + edge-duration. History is exhausted.

---

## 2. Enhancement catalog — every prediction head / feature idea

Merged and de-duplicated from the operator's two lists (15 + 40), plus my own. Grouped by function.

### 2A. Price / mispricing heads (calibration family — mostly TESTABLE-NOW)

| # | Head | Predicts | Key inputs | Output | Status |
|---|---|---|---|---|---|
| P1 | Market calibration curve by leader price | actual win% vs traded price | leader price, settlement | fair-value curve | 🟢 DONE (HF_MARKET_CALIBRATION) |
| P2 | Joint calibration curve | win% by price × time × distance × horizon × round-type | those 5 axes | fair-value table | 🟡 TESTABLE-NOW |
| P3 | Cheap-valid vs cheap-dangerous | is a cheap leader mispriced or fragile | price, dist/vol, flip, shock, secs | VALID / DANGEROUS / ILLIQUID | 🟢 DONE (HF_CHEAP_LEADER_DANGER) |
| P4 | Mispricing-type classifier | *why* the edge exists | price, spread, depth, staleness, shock | stale / thin / genuine / panic / spread-trap / late-danger / ghost | 🔴 (needs book) partial 🟡 |
| P5 | Price-dislocation classifier | real vs stale/depth/spread dislocation | model-fair − market price, book state | dislocation type | 🔴 RECORDER-GATED |
| P6 | Anchor-distance sweet spot | win% by $ distance bucket ($5–10 … >$100) | distance, price | best distance zone | 🟡 TESTABLE-NOW |
| P7 | Entry-timing-within-round | which time-left zone the rule works in | seconds_left buckets | too-early / good / too-late | 🟡 TESTABLE-NOW (HF hints early 5m) |
| P8 | "Too far gone" / correct-side-bad-price | leader wins but price too high after fee+buffer | P(Hold), ask, fee | CORRECT-SIDE-BAD-PRICE | 🔴 (needs ask) partial 🟡 |
| P9 | "Market overpays for trailing side" | trailing side over-priced | trailing price, distance, flip, shock | trailing-trap flag | 🟡 TESTABLE-NOW |
| P10 | "Market efficient / no edge" | model fair ≈ market → no bet | fair vs price | MARKET-EFFICIENT | 🧩 / 🔴 |

### 2B. Execution / fillability heads (all RECORDER-GATED)

| # | Head | Predicts | Output | Status |
|---|---|---|---|---|
| E1 | Edge-duration model | does edge survive 1s/3s/5s | seconds edge persists | 🔴 |
| E2 | Max-safe-size / VWAP fillability | shares before edge gone (VWAP_1/5/10/25/50) | max_safe_size | 🔴 |
| E3 | Ask-runaway predictor | will ask reprice up if I wait | UP / STABLE / IMPROVE | 🔴 |
| E4 | Depth-disappear predictor | will available depth vanish on entry | STABLE / FRAGILE / VANISH | 🔴 |
| E5 | Book-toxicity score | is visible book dangerous to trade | CLEAN / TOXIC | 🔴 |
| E6 | Adverse-selection predictor | am I buying right before it moves against me | LOW / MED / HIGH | 🔴 |
| E7 | Passive-vs-taker decision | cross the spread now vs limit order | TAKER-OK / LIMIT-ONLY / DO-NOT-CHASE | 🔴 |
| E8 | Exit-risk / exitability | can I exit before settlement if state worsens | EXITABLE / HOLD-ONLY / TRAPPED | 🔴 |
| E9 | Bid-exit availability | enough bid depth to sell | GOOD / WEAK / TRAP | 🔴 |
| E10 | Hold-to-expiry vs exit-early | hold to settlement or take profit / cut | HOLD / TP+5c / EXIT-ON-recross / EXIT-ON-widen | 🔴 |
| E11 | Sweep-impact predictor | book move per 10/50 shares + recovery | impact + recovery time | 🔴 |
| E12 | Liquidity-refill half-life | how fast depth returns after a sweep | FAST / SLOW / NO-REFILL | 🔴 |
| E13 | Quote-cancel shock predictor | will liquidity pull before/during a burst | pull-risk HIGH | 🔴 |
| E14 | One-sided-book detector | enter but not exit, or vice-versa | ENTERABLE-NOT-EXITABLE / … / BOTH-OK / BOTH-BAD | 🔴 |
| E15 | Maker-defense level detector | is someone defending a price level | level HOLD / BREAK | 🔴 |
| E16 | MM-behavior / MM-regime | tight / wide / stale / panic / no-maker | book regime | 🔴 |
| E17 | Time-decay / theta model | expected leader price in 5/15/30s if no cross | expected price path | 🔴 |
| E18 | Share-price-path predictor | leader share +5c before −5c; reaches 0.70; 10c drawdown | share-path probabilities | 🔴 |
| E19 | EV-decay model | EV now vs wait 5/15/30s | EV curve → act/wait | 🔴 |
| E20 | Quote-reaction / PM-repricing speed | how fast PM reprices after a $10/$20/$50 BTC move | UNDER / INSTANT / OVER / STALE | 🔴 |
| E21 | Post-entry monitoring alert | after a paper entry: hold / reduce / exit / invalidated | live per-second state | 🔴 |
| E22 | Invalidation-condition predictor | which invalidation (cross / flip / edge-gone / depth / spread) is most likely | ranked invalidation | 🔴 |

### 2C. Book-quality / safety gates (RECORDER-GATED; some are trivial computes once live)

| # | Gate | Computes | Output | Status |
|---|---|---|---|---|
| G1 | Tradeable-book / complement sanity | UP ask + DOWN ask ≈ 1.00–1.05 | TRADEABLE / BAD-BOOK | 🔴 (would have killed HF orderbook instantly) |
| G2 | Stale-book detector | BTC moved but ask didn't; old ts; abnormal complement; repeated quote | FRESH / STALE / BROKEN / DO-NOT-TRUST | 🔴 |
| G3 | False-cheap detector | cheap because book stale/broken, not buyable | FALSE-CHEAP | 🔴 |
| G4 | Round kill switch | disable whole round | ROUND-DISABLED (broken book / macro event / shock / thin / disagreement) | 🧩 🔴 |
| G5 | PM-leads-BTC detector | is Polymarket price leading BTC (BTC-only model is late) | PM-LEADS / BTC-LEADS / SYNC / NOISE | 🔴 |
| G6 | Live-vs-historical mismatch monitor | does today's book match research regime | REGIME-VALID / REGIME-BROKEN | 🔴 |
| G7 | Data-quality confidence | feed health (BTC fresh? book fresh? both tokens? settlement join? P(Hold) warm?) | DATA-OK / DEGRADED / DO-NOT-TRADE | 🔴 (mandatory for production) |

### 2D. Risk / path heads (mostly already BUILT by Codex as shadow)

| # | Head | AUC (held-out) | Status |
|---|---|---|---|
| R1 | Future side-flip risk (in-round) | 0.816 / 0.891 | 🟢 shadow (DECISION_HEAD_RESEARCH) |
| R2 | Late-shock risk remaining $20/$50/$100 | 0.851 / 0.845 | 🟢 shadow |
| R3 | Time-to-touch | 0.83 | 🟢 shadow |
| R4 | Big-window / big-move probability | 0.84 / HIGH_VOL 0.943 | 🟢 built |
| R5 | Path type (CHOP / TREND / round type) | — | 🟢 built |
| R6 | Opportunity within next 1–3 rounds | 0.837 | 🟢 shadow |
| R7 | P(Hold) persistence (leader holds) | calibrated iso | 🟢 live (leader-only — never symmetric) |

### 2E. Composition / meta layers (build FROM the heads above — 🧩)

| # | Layer | Combines | Output |
|---|---|---|---|
| C1 | Bad-bet detector | P(Hold), flip, shock, spread, depth, secs, price, edge-dur | SAFE / FRAGILE / TRAP |
| C2 | Edge-quality score (0–100) | raw edge, edge-dur, depth, spread, flip, shock, freshness, calibration bucket | 90+ excellent … <50 avoid |
| C3 | Dynamic required-edge buffer | flip, shock, spread, depth, secs, vol, edge-dur | required_edge_cents (2c quiet … 10c thin) |
| C4 | Round-quality score (0–100) | opportunity − danger − execution-penalty | 82 watch / 45 wait / 18 avoid |
| C5 | No-trade-reason classifier | all gates | list of *why* (spread wide / not cheap / flip high / …) |
| C6 | Model-disagreement / conflicted-signal | head agreement | CONFLICTED-WAIT |
| C7 | Consensus-state classifier | all heads → one state | STABLE-LEADER / FRAGILE / CHOP / VOLATILE-BREAK / DEAD / STALE / EXEC-TRAP |
| C8 | Dominant-risk meta-controller | which head should dominate | HOLD / FLIP / SHOCK / EXECUTION / LIQUIDITY / STALE-BOOK |
| C9 | Trade-archetype classifier | opportunity type | cheap-stable / late-safe / stale-scalp / danger-discount / exec-trap / none |
| C10 | Expected-regret score | regret_trade vs regret_no_trade | act vs wait |
| C11 | Wait-for-pullback price | fair, vol, book move, secs | target_entry_price |
| C12 | Best-market-to-watch selector | 5m/15m current/next | WATCH-CURRENT-5M / … / IGNORE-15M |
| C13 | Cross-market disagreement (5m vs 15m) | implied prob 5m vs 15m + vol | context flag |
| C14 | Cross-round memory | prev-round vol/winner/close/flip/overreaction/spread | next-round active/choppy/wide |
| C15 | Do-nothing-but-monitor mode | opportunity timing + round-quality | "no bet, monitor — opportunity likely in 1–3 rounds" |

---

## 3. Testing catalog — every test, its status, and what it needs

### 3A. Tests already run this cycle (🟢 / ⚫)

| Test | Result | Doc |
|---|---|---|
| Direction predictability | ⚫ coin-flip | PROFITABILITY_AND_BETTING_VALIDATION |
| Fade round-trip backtest | ⚫ touch-candle look-ahead leak (80.6%) → retracted | ANCHOR_ROUNDTRIP_180D + retraction |
| bookDepth liquidity probe | ⚫ no lift vs rv | BOOKDEPTH_LIQUIDITY_PROBE |
| bookDepth veto/regime probe | ⚫ held% flat; veto net-negative | BOOKDEPTH_VETO_PROBE |
| HF orderbook audit | ⚫ barbell KILL | HF_POLYMARKET_DATASET_AUDIT |
| HF trades token mapping | 🟢 75% clean (5,920 markets) | HF_TRADES_TOKEN_MAPPING |
| HF trade-edge analysis | ⚠️ +27% headline (corrected below) | HF_TRADE_EDGE_ANALYSIS |
| **HF edge robustness (nulls)** | ⚠️ NOT P(Hold); leaders underpriced +14% baseline; invert −24.9% | HF_EDGE_ROBUSTNESS |
| **HF market calibration curve** | ⚠️ leaders underpriced +8.2pp round-level | HF_MARKET_CALIBRATION |
| **Cheap-valid vs cheap-dangerous** | ⚠️ SAFE +16pp / FRAGILE +5pp; latency signature | HF_CHEAP_LEADER_DANGER |
| Candidate dataset audits (krish301 / BrockMisner / Kaggle) | ⚫ barbell / empty | PM_DATASET_HUNT_AUDIT |
| Free-data sourcing + recorder state | 🟢 recorders built; gap is operational | FREE_DATA_SOURCING_AND_RECORDER_STATE |

### 3B. Tests runnable now on existing data (🟡 TESTABLE-NOW)

| Test | Hypothesis | Data | Priority |
|---|---|---|---|
| T1 Joint calibration curve (P2) | fair-value varies by price×time×distance×horizon×round | HF trades | Medium (still trade-price, latency-confounded) |
| T2 Anchor-distance sweet spot (P6) | leader attractiveness varies by $ distance | HF trades + BTC matrix | Medium |
| T3 Entry-timing zones (P7) | rule works only in certain seconds-left zones | HF trades | Medium |
| T4 Trailing-side overpricing (P9) | market over-pays trailing side in some states | HF trades | Low-Med |
| T5 Latency-vs-mispricing decomposition | is the leader edge Binance-leads-oracle (shrinks with matched feed) or real | HF trades + Binance 1s + (ideally Chainlink/Pyth history) | **High** (decides if the one edge is a race or real) |
| T6 Round-level stability across months | does +8.2pp hold outside March | more HF months if available | High |

*Note:* T1–T4 are refinements of a signal already flagged as a likely **latency artifact**, so their marginal
value is low until T5 settles the latency question or a live/ask dataset arrives. **T5 is the one worth doing on
history** because it can *characterize* (not prove) the mechanism.

### 3C. Tests that need the live recorder (🔴 RECORDER-GATED — the ones that matter)

| Test | What it finally proves |
|---|---|
| Live calibration by **ask** (not trade price) | whether the underpricing is *buyable* |
| Edge-duration 1s/3s/5s | whether the edge survives long enough to act |
| Fillability / VWAP by size | whether it survives past ~5 shares |
| Cheap-SAFE-leader live win rate vs live ask | the entire cheap-leader thesis, executably |
| Complement/overround live (G1) | whether the book is even tradeable that round |
| PM-repricing speed after BTC shock (E20) | whether there's time to act |
| Stale/false-cheap incidence (G2/G3) | how often "edges" are artifacts |
| Live-vs-historical regime match (G6) | whether March research still applies |

### 3D. Dataset audits still to run (🟡 on operator download)

Priority order (operator downloads; I run the inside-spread gate + null tests):
1. `kachoio/polymarket-5-minute-crypto-updown-markets` — 26.8M second-by-second top-of-book ← **selected**
2. `namz8888/polymarket-btc-5-minute-high-frequency-tick-data` — 100ms
3. `debayan31415/polymarket-5-minutes-btc-up-down-data` — 2s book states
4. `marvingozo/polymarket-tick-level-orderbook-dataset` — 65k markets (mixed; may not be BTC-5m)
5. BTC-microstructure (risk heads only, NOT the bottleneck): `krrdev1/binance-btcusdt-l3`, `eimadevyni/btcusdt-market-lake`, `marvingozo/hyperliquid-*`, `adamatractor/institutional-crypto-l2`

**Dataset audit checklist (run on every candidate before trusting it):**
1. Snapshots inside market open/close window?
2. UP/DOWN token mapping deterministic?
3. Every market joins to settlement?
4. Median spread realistic (often 1c–5c)?
5. UP ask + DOWN ask ≈ 1.00–1.05?
6. Real bids/asks near 0.40–0.60 (not 0.10/0.90 barbell)?
7. Ask **size** present?
8. Can VWAP_1/5/10 be computed?
9. Does the book tighten near expiry?
10. Does last trade price agree with nearby book?
11. One first-qualifying entry per round only (round-level).
12. Null tests: shuffled model, inverted side, random time, random side.

---

## 4. Target app design — the decision tree (the destination)

The app should stop showing "UP 54% / DOWN 46%" and instead walk a gated decision tree, showing *why* at each
step (this is what C1–C15 + the gates compose into):

```
1. Is data fresh?              (G7 data-quality)         → else DO-NOT-TRADE
2. Is the book tradeable?      (G1 complement, G2 stale) → else BAD-BOOK
3. Is the leader cheap?        (P1 calibration vs ask)   → else NO-EDGE
4. Cheap for a good/bad reason?(P3 cheap-valid, P4 type) → else CHEAP-DANGEROUS
5. Enough depth?               (E2 VWAP, E9 bid-exit)    → else CHEAP-ILLIQUID
6. Does edge survive 1s/3s/5s? (E1 edge-duration)        → else STALE-LATENCY
7. Flip/shock risk acceptable? (R1 flip, R2 shock)       → else FRAGILE
8. EV positive after fee/VWAP/buffer? (C2, C3)           → else NEGATIVE-EV
9. One of the few best rounds? (C4 round-quality, C12)   → else WAIT
10. Action: PAPER / WAIT / AVOID  (+ C5 no-trade reason, C8 dominant risk)
```

The existing **shadow round-state panel** (ROUND_STATE_DECISION_PANEL) already renders steps 7 & 9-ish
(future-cross, shocks, opportunity, path, quote-status). Steps 1–6 & 8 are the recorder-gated build-out.

---

## 5. Prioritized build order (my recommendation)

**Blocked on nothing but an operator download or the recorder — do these, in order:**
1. **Audit a real Polymarket book dataset** (kachoio first). If it passes the spread gate, it unblocks the
   entire execution layer (E1–E22, G1–G7) on history for the first time. *Selected — awaiting download.*
2. **Run the live recorders** (`start_recorder.bat` + `run_polymarket_l2_recorder.bat`). Ground truth for
   every 🔴 test; nothing beats it. Operator action.
3. **Wire cheap-valid classifier + calibration curve into the shadow panel** so it lights up when live data
   flows (uses only existing heads; PAPER/shadow only).

**Build only after data proves the premise:**
4. Book-quality gates first (G1, G2, G7) — cheapest to compute, highest safety value, would have killed HF.
5. Edge-duration (E1) + fillability (E2) — these decide whether *any* of the cheap-leader work is real.
6. The composition layer (C1–C5) — bad-bet, edge-quality, required-edge, round-quality, no-trade-reason.
7. The rest of the execution heads (E3–E22) as recorder data accrues.

**Do NOT do:** build the 40 heads speculatively on data we don't have; re-run direction/fade/bookDepth (dead);
trust any trade-price "edge" as if it were an ask; add more calibration dimensions before T5/live settles the
latency question.

---

## 6. Guardrails carried forward (lessons that cost us this cycle)

- **P(Hold) is leader-only.** Never fabricate symmetric `p_win_up`/`p_win_down`. It predicts only "current
  leader holds."
- **Look-ahead leaks are silent killers.** The fade "+EV" was 80.6% same-candle resolution. Always check that
  labels can't peek at the resolving bar.
- **Round-level + null tests before celebrating.** Snapshot-level inflates confidence via correlated rows;
  shuffle/invert/random-time/random-side nulls catch price-selection masquerading as model skill.
- **Trade price ≠ ask.** Every historical "edge" here is a trade-price result; buyability is unproven until the
  live `/book` ask says so.
- **Inside-spread gate on every dataset.** A parseable ladder can still be a 0.10/0.90 barbell (HF was).
- **Confirm before wiring; PAPER only.** Nothing changes live Champion behavior without explicit approval.
  See [[confirm-before-wiring]].
- **Treat every finding as a hypothesis to falsify**, not a headline to ship.

---

## 7. Cross-references

Calibration & anomaly: HF_MARKET_CALIBRATION, HF_CHEAP_LEADER_DANGER, HF_EDGE_ROBUSTNESS, HF_TRADE_EDGE_*.
Dead ends: HF_POLYMARKET_DATASET_AUDIT, BOOKDEPTH_LIQUIDITY_PROBE/_VETO, PM_DATASET_HUNT_AUDIT.
Risk heads & panel: DECISION_HEAD_RESEARCH, ROUND_STATE_DECISION_PANEL, ROUND_STATE_AND_STOPPING_RESULTS.
Recorders & execution: FREE_DATA_SOURCING_AND_RECORDER_STATE, POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION,
POLYMARKET_MARKET_RESPONSE_TEST, POLYMARKET_SHOCK_SHARE_REPLAY.
Master truth: PROFITABILITY_AND_BETTING_VALIDATION.
