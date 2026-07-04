# Prediction Heads: Data-Requirement Split + Complete Test Ledger (2026-07-02)

> **⚠️ 2026-07-03 RECONCILIATION — the Kaggle executable-ask results supersede parts of this doc.**
> The kachoio archive (per-second executable bid/ask, 14,226 settled BTC 5m rounds) unblocked the
> "Category B — needs forward data" executable-ask tests EARLY. Outcomes
> (`KAGGLE_SOURCES_AND_FROZEN_RULES_2026-07-02.md`, `KAGGLE_DATA_TEST_PLAN_2026-07-02.md`):
> 1. **Opinion #2 vindicated:** the +8–35pp trade-price gaps **collapse to +1–4c at real asks** — the
>    latency/trade-price confound was real. A small, late-concentrated edge survives: **+2.1c LB/share at
>    30s left**, adopted as frozen rule `LATE_LEADER_30S_V1` (paper-only, live forward ledger wired).
> 2. **The pathstate tests (archetype/timing/cross-market, HF_PATHSTATE_TESTS) are hold-stratifiers, NOT
>    entry gates:** the gated-lift test showed every BTC-side gate (≥$20 lead, TREND, 15m-agreement) raises
>    win% but the ask rises faster — within a fixed ask bucket they add nothing (shuffled-gate nulls p
>    0.74–0.97). **The ask is the sufficient statistic.** Do not wire them into the entry rule.
> 3. Killed with frozen specs: `EARLY_LEADER_SCALP_V1` (−4.1c), `TP_OR_SETTLE_5M_V1` (−1.8…−4.4c),
>    `STRADDLE_SCALP_5M_V1` (−10.7c), `FADE_V6_1S` (gate-fail 0.689<0.70, not saved).

**Purpose.** Answer, on the record, two questions for **every** proposed prediction head:
1. **Can it be tested from EXISTING data**, or does it **require FORWARD / live-recorder data**?
2. **What is my honest opinion** — is it worth building, likely to work, and what are the caveats?

Plus: a **complete ledger of every test we have actually run**, its method, its result, and the doc that
holds the evidence. This is the "document everything" record. It is a companion to — not a replacement for —
[APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md](APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md).

## Definitions

- **Category A — EXISTING DATA.** Testable now on what we already have: the 1m/1s BTC research matrix, HF
  Polymarket **trades** (executed prints, March), settled `price_to_beat` rounds, champion/persistence snapshots.
- **Category B — FORWARD DATA.** Needs the live recorders (`live_btc_updown_recorder` → `execution_layer.duckdb`
  + quote bridge; `l2_recorder` → `polymarket_l2.duckdb`) to accrue live `/book` ask/size/depth/spread, edge
  decay, and official settlement. **Both recorders are now running and collecting** (verified 2026-07-02).
- **Category C — COMPOSITION.** Not a standalone model; it combines other heads/gates into a score, panel, or
  routing decision. Testable only as well as its *components* are.

---

## The blunt verdict up front

Of the 30 newly-proposed heads: **~8 are Category A** (and 3 of those are already done), **~19 are Category B**
(forward/live), **~3 are Compositions**. **The pattern has not changed:** ~75% of the useful remaining work is
**execution/market-quality prediction that only live `/book` data can support.** The Category-A remainder is
mostly **calibration variants already flagged as latency-confounded** (see Test Ledger #14–#15), so their
marginal value is limited. The genuinely-worth-doing Category-A items are **round archetype (#14)**,
**entry-timing quality (#2)**, and **cross-market consistency (#23)** — clean classifiers on data we hold.
Everything else waits on the recorder, which is now accruing.

---

## Data-requirement matrix — all 30 heads

| # | Head | Category | Status | My opinion (1-line) |
|---|---|---|---|---|
| 1 | Best action type (WAIT/WATCH/PAPER/**TAKER/LIMIT**/AVOID/DISABLED) | C (+B for taker/limit) | partial live (WAIT/AVOID/PAPER exist) | Good UX; the taker-vs-limit split is pure Category B. Build as composition, not a model. |
| 2 | Entry timing quality (too early/good/too late/danger) | **A** | testable-now | **Worth it.** Clean test on seconds-left buckets over settled rounds. Low risk, real UX value. |
| 3 | Price target for entry (max_safe_entry) | B (+A for fair value) | forward | Fair value = A; "max buy price" needs the live ask. Trivial once live. |
| 4 | Required-buffer predictor (dynamic edge) | **B** | forward | High-value safety head, but spread/depth/staleness inputs are all live-book. Can't honestly build now. |
| 5 | Cheap-valid vs cheap-dangerous | **A** | **DONE** | Ran it. Partly "cheap for a reason" (SAFE +16pp/FRAGILE +5pp); high-vol/early = latency signature. |
| 6 | Book-freshness / stale-book detector | **B** | forward | Needs live book timestamps + ask-vs-BTC reaction. Mandatory safety head once live. |
| 7 | Market response lag (under/overreacting) | **B** | forward (partly tested) | Already tested read-only → **no reliable underreaction** (Ledger #7). Re-test only on richer live book. |
| 8 | Edge-decay curve (edge_half_life) | **B** | forward | Critical for real execution; decay is sub-second → impossible on history. Recorder-only. |
| 9 | Fillability probability (1/5/10/50 shares) | **B** | forward | The question that matters most. Needs ask **size** + ladder. Recorder-only. |
| 10 | Max safe size | **B** | forward | Same as #9 — VWAP-by-size from the live ladder. |
| 11 | Exitability score (exitable/hold-only/trapped) | **B** | forward | Needs live bid depth + refill. Recorder-only. |
| 12 | Hold-to-expiry safety | B (+A for flip/shock) | forward | Flip/shock parts exist (shadow); the exit-management part is live. |
| 13 | Invalidation probability (dominant risk) | C (+B) | partial | Can rank flip-vs-shock from existing heads now; book-pull/spread-widen need live. Composition. |
| 14 | **Round archetype** (stable/fragile/chop/trend/quiet/…) | **A** | testable-now | **Best new Category-A build.** Clean classifier from path+flip+shock+vol on settled rounds. STALE_BOOK/EXEC_TRAP archetypes need B. |
| 15 | Market-maker regime | **B** | forward | Needs spread/depth/refill/cancels — all live book. Recorder-only. |
| 16 | Liquidity-pull risk (1s/3s/5s) | **B** | forward | Sub-second live book. Recorder-only. |
| 17 | Ask-runaway risk | **B** | forward | Needs live ask trajectory. Recorder-only. |
| 18 | Bid-collapse risk | **B** | forward | Needs live bid trajectory. Recorder-only. |
| 19 | Market calibration by price bucket | **A** | **DONE** | Ran it. Leaders underpriced +8.2pp round-level — but **trade price, not ask**; latency-confounded. |
| 20 | Joint calibration by state | **A** | testable-now | Extends #19; still trade-price + latency-confounded → **low marginal value** until live ask exists. |
| 21 | Correct-side-bad-price | B | forward | Trivial logic; needs the live ask to fire. Composition once live. |
| 22 | Market-efficient / no-edge | B | forward | Needs live ask vs fair. Composition once live. |
| 23 | Cross-market consistency (5m vs 15m) | **A** | testable-now | **Worth it.** Compare existing 5m/15m rounds for dislocation. Context flag, not a trigger. |
| 24 | Best market to watch | A/C | partial | Opportunity heads exist; selector is a composition over them. Testable-ish now. |
| 25 | Opportunity drought | **A** | **DONE (head exists)** | `next_opportunity_within_3_rounds` shadow head (5m AUC 0.837). Wire the WAIT-mode UX. |
| 26 | Post-entry monitoring (hold/exit/reduce/invalidated) | **B** | forward | Full trade lifecycle on live per-second book. Recorder-only. |
| 27 | Expected EV if waiting | **B** | forward | Needs live edge-decay (#8). Recorder-only. |
| 28 | Expected regret (trade vs skip) | **B** | forward | Derived from EV (#27) → live. |
| 29 | Data-quality confidence | B (+A partial) | forward | BTC-feed freshness is checkable now; book freshness/settlement-join need live. Mandatory once live. |
| 30 | Live-vs-historical mismatch monitor | **B** | forward | By definition needs live distributions to compare to history. Recorder-only. |

**Tally:** Category A = #2, #5✓, #14, #19✓, #20, #23, #24(partial), #25✓ → **8** (3 done). Category B ≈ **19**.
Composition ≈ **3** (#1, #13, #21/#22 borderline).

---

## Category A — what is genuinely worth testing now (existing data)

1. **Round archetype (#14)** — one clean multiclass label per settled round from path-style + flip-risk +
   late-shock + volatility. Becomes the app's headline "what kind of round is this." *Highest new value.*
2. **Entry-timing quality (#2)** — win/hold rate by seconds-left bucket (240/180/120/60/30) to formalize the
   "works at 240s, fails at 60s" intuition the HF data already hints at.
3. **Cross-market consistency (#23)** — 5m-vs-15m leader-price / instability agreement as a context/warning flag.
4. **Already done, just surface in UI:** cheap-valid (#5), market calibration (#19), opportunity drought (#25).

**Caveat that governs all of them:** any calibration/price-bucket work (#19/#20) is **trade-price, not resting
ask**, and shows a **latency-artifact signature** (bigger in high-vol/early). Treat as characterization, not a
buyable edge. #2/#14/#23 are BTC-path-based and don't share that flaw.

## Category B — what the (now-running) recorder unlocks

Everything execution/microstructure: fillability (#9), max-size (#10), edge-decay (#8), exitability (#11),
book-freshness (#6), MM-regime (#15), liquidity-pull (#16), ask-runaway (#17), bid-collapse (#18), price-target
(#3), required-buffer (#4), correct-side-bad-price (#21), market-efficient (#22), post-entry monitor (#26),
EV-if-waiting (#27), regret (#28), data-quality (#29), live-vs-historical (#30), market-response-lag (#7).

**These cannot be built honestly from history** — the HF book is a barbell and the effects are sub-second. They
become buildable as `execution_layer.duckdb` + `polymarket_l2.duckdb` accrue several hundred clean
quote+settlement rounds. The new `round_state_snapshots` table (added 2026-07-02) will let us measure the
shadow heads' live calibration against outcomes.

---

## Complete Test Ledger — every test we ran, method, result

| # | Test | Method | Result / verdict | Evidence doc |
|---|---|---|---|---|
| 1 | Ensemble direction 5m/15m | live settled rounds | ⚫ **Coin-flip 49.8%** (8,039 rounds); at information ceiling | PROFITABILITY_AND_BETTING_VALIDATION; DUCKDB_METRICS_ANALYSIS |
| 2 | Per-model direction (8 models) | live log breakdown | ⚫ all ~50–51%; tracker 50.2%/50.5% | DUCKDB_METRICS_ANALYSIS |
| 3 | Anchor round-trip / fade 180d | causal backtest | ⚫ **41.97%** first-entry wins; below breakeven | ANCHOR_ROUNDTRIP_180D_RESULTS |
| 4 | Fade touch-candle leak | causal audit | ⚫ **80.6%** of $30 touches resolve same 1m candle → labels invalid; **retracted** | PROFITABILITY_AND_BETTING_VALIDATION |
| 5 | TP50/SL10 policy | 5-era walk-forward | 🟡 PF 2.08 stable, but **BTC-proxy only**, not PM share PnL → PAPER | TP50_SL10_WALKFORWARD_AUDIT |
| 6 | Shock share-replay (MOM/FADE) | causal quote replay | ⚫ short round-trips negative; nothing survives gates | POLYMARKET_SHOCK_SHARE_REPLAY_RESULTS |
| 7 | Market response / underreaction | read-only live | ⚫ **no reliable underreaction/arb**; only 29 trustworthy rounds | POLYMARKET_MARKET_RESPONSE_TEST |
| 8 | Binance bookDepth liquidity | causal probe vs rv | ⚫ no lift (big-move 0.747→0.747; drop 0.707→0.706) | BOOKDEPTH_LIQUIDITY_PROBE |
| 9 | bookDepth veto/regime | shadow on 12k snaps | ⚫ held% flat; VACUUM veto removes 90 bad vs 2,078 good | BOOKDEPTH_VETO_PROBE |
| 10 | HF orderbook audit | schema + spread gate | ⚫ **barbell 0.10/0.90, $0.80 spread → KILL** | HF_POLYMARKET_DATASET_AUDIT |
| 11 | HF trades token mapping | resolution join | 🟢 **75%** clean (5,920 markets); ambiguous quarantined | HF_TRADES_TOKEN_MAPPING |
| 12 | HF trade edge (leader-only) | P(Hold) − price − buffer | ⚠️ +27% headline → **corrected below** | HF_TRADE_EDGE_ANALYSIS |
| 13 | **HF edge robustness (nulls)** | shuffle/invert/baseline | ⚠️ **NOT a P(Hold) edge** (shuffle +25.7% ≈ real); leaders underpriced +14% baseline; invert −24.9% | HF_EDGE_ROBUSTNESS |
| 14 | **HF market calibration** | win% by leader price, round-level | ⚠️ leaders underpriced **+8.2pp** round-level; calibrated at extremes; **trade-price not ask** | HF_MARKET_CALIBRATION |
| 15 | **HF cheap-valid vs dangerous** | fragility split | ⚠️ SAFE +16pp / FRAGILE +5pp; **bigger in high-vol/early = latency signature** | HF_CHEAP_LEADER_DANGER |
| 16 | Candidate datasets (krish301/BrockMisner/Kaggle) | spread gate | ⚫ barbell / empty | PM_DATASET_HUNT_AUDIT |
| 17 | Decision heads (flip/shock/ttt/opportunity) | causal 180d, held-out | 🟢 flip 0.816/0.891, late-shock $50 0.851/0.845, ttt 0.83, opportunity 0.837 → **shadow** | DECISION_HEAD_RESEARCH_RESULTS |
| 18 | Round-state heads (deployable) | purged grouped split | 🟢 all targets **PASS** AUC gates (0.78–0.95) → **shadow bundle served** | ROUND_STATE_DECISION_PANEL |
| 19 | VWAP / Bollinger / ORB path | 360d causal | 🟡 VWAP flat; Bollinger small touch lift; ORB owns line-cross lift; **all P(Hold) vetoes rejected** | VWAP_BOLLINGER_PATH_RESEARCH |
| 20 | Round-ORB + systemic PCA absorption | 360d/180d causal | ⚫ ORB narrow 15m lift, fails as veto; absorption **worsens** every model | ROUND_ORB_AND_SYSTEMIC_ABSORPTION_RESULTS |
| 21 | Regime-gate shadow | replay logged rounds | 🟡 prefer-RANGE/LOW_VOL 53.5% full window, but **recent-250 dips <50%** → not confirmed | REGIME_GATE_SHADOW |
| 22 | P(Hold) calibration monitor | 13,972 resolved | 🟢 ECE 0.033 STABLE; ~2pt top-tier optimism → opt-in recal overlay | CALIBRATION_MONITOR |
| 23 | Impact/absorption reversion | corrected rebuild | ⚫ negative; effect lives sub-second → L2-gated | IMPACT_REVERSION_PROBE |
| 24 | Path→champion lift | matched pairs | 🟡 **WATCH** — real P(Hold)-independent signal (+4.2pp, p=0.000) but not a binary filter | PATH_CHAMPION_LIFT |
| 25 | Triple-barrier ceiling | 30d, 70/30 | ⚫ net-**negative** after 2bps spread at every horizon | CEILING_BREAK_EXPERIMENTS |
| 26 | Settlement ingestion | backlog recovery | 🟢 364/364 recovered; plumbing complete | SETTLEMENT_INGESTION |

Legend: ⚫ dead/negative/retracted · 🟡 partial/conditional/paper-gated · 🟢 usable/shadow · ⚠️ positive-but-caveated.

---

## My consolidated opinions (on the record)

1. **Direction is over. Permanently.** Every family lands ~51% OOS. This is the honest ceiling, not a tuning
   problem. Stop proposing UP/DOWN variants. (Ledger #1, #2, #25.)
2. **The one recurring "positive" — leaders underpriced — is most likely a Binance-leads-the-oracle latency
   race, not a calm mispricing.** It's trade-price, not ask; it grows in high-vol/early (the latency
   fingerprint). I do not believe it is a free, fillable edge, and I will not treat it as one until the live
   ask says so. (Ledger #13–#15.)
3. **~75% of every "next heads" list is forward-data-gated.** This has been true across three separate
   brainstorms. The bottleneck is not ideas — it's live `/book` data, which is now finally flowing.
4. **The shadow risk heads (flip/shock/ttt/opportunity) are real and worth keeping** — but their high AUCs are
   largely mechanical functions of time/distance/vol. They are decision *support*, not profit. Their live
   calibration is now being logged (`round_state_snapshots`) and must be measured before any trust. (Ledger #17,#18.)
5. **Best use of the next existing-data hour, if any:** round archetype (#14), entry-timing (#2), cross-market
   (#23). Everything else on the list should wait for the recorder rather than be faked on history.
6. **The most valuable thing we did this cycle was not a finding — it was killing false findings** (the fade
   leak, bookDepth, the HF barbell, the "P(Hold) edge" that wasn't). That discipline is the asset.
7. **Nothing is real until: live ask + fillability + edge-duration + fees + settlement replay** shows positive
   net EV with a lower confidence bound above zero. Until then: PAPER, shadow, information-only.

---

## Cross-references

Master catalog: [APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md](APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md) ·
Panel: [ROUND_STATE_DECISION_PANEL_2026-07-02.md](ROUND_STATE_DECISION_PANEL_2026-07-02.md) ·
Calibration/anomaly: HF_MARKET_CALIBRATION, HF_CHEAP_LEADER_DANGER, HF_EDGE_ROBUSTNESS ·
Recorder/execution: FREE_DATA_SOURCING_AND_RECORDER_STATE, POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION ·
Truth: PROFITABILITY_AND_BETTING_VALIDATION.
