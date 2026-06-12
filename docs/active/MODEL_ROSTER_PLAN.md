# Model Roster & Precision Plan — the road to the 95% tier

**Status: PLAN ONLY (operator directive 2026-06-12). No code changes until today's
v5-classbal live run is measured. Every action below has a decision gate tied to
today's evidence.**

---

## 0. The 95% target — said honestly, aimed correctly

95% accuracy on EVERY 5-minute signal does not exist anywhere on earth. If 5m BTC
direction were 95% predictable, that information would be worth billions, would be
arbitraged within weeks, and the people holding it would not sell dashboards. Chasing
all-signal 95% guarantees self-deception — we have already lived this once (the
retracted hit-based "90%" era, §5z).

**But a 95%-precision TIER is a real, buildable thing** — and that is the sky we aim
at. The math: precision rises as you give up frequency. The plan below builds the
ladder deliberately:

| Tier | What qualifies | Honest target | Frequency |
|---|---|---|---|
| T0 — all raw leans | every model lean | 52–58% | ~all day |
| T1 — Grade A/B model leans | committed + confirmations | 60–70% | dozens/day |
| T2 — late-entry persistence | price ahead, <2min, model agrees | 80–90% | handful/day |
| **T3 — the 95% tier** | **late-entry + persistence MODEL agrees + p_up edge + magnitude band clear** | **90–95%+** | **a few/day** |

T3 is the world-class product: few bets, near-certain, sized with Kelly discipline.
That is what "95%, nothing mediocre" actually looks like when it's real. Everything
in this plan either raises a tier's precision or feeds bets up the ladder.

---

## 1. Evidence from tonight's run (2026-06-12, v5-classbal, 40 days)

The OOF table is the roster's report card. Selected (3-class accuracy; NB:
class-balanced training deliberately lowers raw OOF vs majority-class cheating —
judge RELATIVE standings, not absolutes; 1m RANGE ≈ base rate, not skill):

| bucket | xgb | histgb | lr | sgd | lgb | cat |
|---|---|---|---|---|---|---|
| 5m TREND | .476 | .461 | .406 | .470 | .454 | **.496** |
| 5m RANGE | .634 | .626 | .522 | .431 | **.636** | .633 |
| 5m GLOBAL | .416 | .407 | .396 | **.228** | .406 | **.422** |
| 7m GLOBAL | .399 | .389 | .404 | **.136** | .406 | .399 |
| 10m GLOBAL | .402 | .409 | .421 | **.124** | .408 | .407 |

Key findings:
1. **SGD is broken cargo**: 0.124–0.23 in the buckets that matter (vs ~0.33 random
   3-class). It is anti-signal the stacker must learn to suppress, and it costs
   ~35–45 min/train.
2. **The tree quartet (xgb/lgb/cat/histgb) moves as one block** — their OOF values
   are nearly identical everywhere. Four near-clones add robustness, not diversity.
3. **TCN trains 18× but has only half a seat**: it votes in the probability blend
   (weight 0.15–0.20) and agreement, but is EXCLUDED from the stacker features
   (['xgb','histgb','lr','sgd','lgb','cat']). Either it earns a full seat or none.
4. **LightGBM ran on GPU** ("LightGBM(gpu)") — proof this box has a working GPU that
   XGBoost/CatBoost/TCN are NOT using. Free speed sitting idle.
5. Class weights behaved exactly as designed (5m [1.17, 0.58, 1.25] = UP boosted;
   15m [0.70, 1.58, 0.73] = NEUTRAL boosted — adapts per horizon). Held-out
   conformal confirmed live ("conformal[held-out]").

---

> **STATUS UPDATE (2026-06-12 late): R1 (Kronos), R2 (SGD), R3 (FSR-PPO), A6 (TCN
> seat+budget), F1 (GPU — XGBoost CUDA CONFIRMED on this machine), A2-lite (p_up fair
> value), and A13 (exit hints) are IMPLEMENTED ON DISK as the v6 bundle
> (`v6-classbal-roster-130`). They activate + auto-retrain at the operator's next
> natural restart. Run record: V3_CHANGES_AND_AUDIT.md §5ar. Retrain-#2 items
> (path labels, time features, quantile bands) remain code-pending by design.**

## 2. REMOVE plan (each with decision gate)

### R1 — Kronos: remove entirely  *(gate: none — it's already dead)*
The module isn't even installed ("Kronos unavailable; fallback forecast active").
The fallback emits ~45% directional noise that we maintain a verifier, DB table,
payload fields, and UI panels for. Removal scope when actioned:
- `kronos_model.py`, `kronos_verifier.py` retired; `kronos_predictions` table stops
  being written (janitor already cleans orphans).
- server: drop poller/verify calls, `kronos_*` payload fields, scoreboard kronos
  columns; price_to_beat's `kronos_direction` field → "" (column kept, additive
  schema, no migration needed).
- UI: remove Kronos panels/columns from Decision Center + Models tab; guide updated.
- **Accuracy impact: zero** (already gated out of all decisions). Value: less code,
  less payload, no misleading panel.

### R2 — SGD: retire from the roster  *(gate: stacker confirms ≈0 weight, expected)*
Evidence above. Removal = drop from base names, stacker features, agreement votes,
per-model verifier. Saves ~35–45 min/train AND removes an anti-signal vote from
agreement (today a wrong-way SGD vote suppresses agreement scores).
**Expected effect: small accuracy GAIN + meaningful speed gain.**

### R3 — FSR-PPO challenger: mothball  *(gate: operator confirmation)*
Isolated paper strategy; never touches the live signal; costs a per-loop compute
pass + DB writes + UI panel. The core model must earn an edge before a challenger
strategy layer is interesting. Action: stop invoking (config flag), keep code +
tables (cheap to revive). Revisit when T1 ≥ 60% is established.

### R4 — In-loop CalibratedClassifierCV on XGBoost  *(gate: A/B scorecard-neutral)*
Trains XGBoost 3× for probability calibration that the live isotonic engine now
does on real outcomes (and on clean sign-truth labels). Risk: in-loop calibration
helps until the live engine activates (150 leans/horizon). Action: A/B one retrain
without it once calibrators are ACTIVE for 5m/15m. If sign-truth is neutral →
permanent removal (−2/3 of XGB cost ≈ −35 min).

### R5 — Tree-clone consolidation  *(gate: stacker weights after 1 week)*
If the stacker consistently assigns ~equal weight to xgb/lgb/cat/histgb (clones),
drop histgb (slowest of the four per point of OOF) and keep xgb+lgb+cat.
**Do not act before a week of stacker-weight evidence.**

### Succession map — every removal's ROLE has a named successor
Removals are not holes; each component held a function, and the function moves to
something better (usually already built or already planned):

| Removed | Role it held | Successor |
|---|---|---|
| SGD | linear diversity vote | LR keeps the linear seat (it was always better); the DIVERSITY seat goes to **TCN with a real budget + stacker seat (A6)** — actual architectural diversity instead of a worse linear clone |
| Kronos | independent "second opinion" forecaster | **A9 crowd prior** (the market's own probability — a real external opinion) + **A1 persistence/path engine** (a second view trained on our own data, measurable) |
| FSR-PPO | challenger decision policy | the **T-tier gate + Kelly sizing** (already live in the simulator); later the A11/T3 strategy modules — strategy layers gated on proof, which FSR-PPO never had |
| CalibratedClassifierCV | probability calibration | **already replaced**: the live isotonic PrecisionEngine (calibration.py) — out-of-sample by construction, sign-truth labels, per-era resets. Removal gate = its calibrators ACTIVE (150+ leans/horizon) |
| HistGB (only if R5 fires) | 4th tree clone | none needed — xgb/lgb/cat hold the role; freed time funds **Optuna (A7)** |

Budget view: the removals free ~1.5-2h per train; that compute is REINVESTED into
TCN epochs (A6), the Optuna search (A7), and faster daily retrains — fresher models
are themselves an accuracy lever. Roster quality ≠ roster size.

### TCN — full seat or no seat  *(decide with R5 evidence)*
Option A: add `dl` to the stacker features (costs OOF refits for TCN — cheap, 7s
fits) and let the stacker decide its worth. Option B: if its blend weight
contribution shows no lift, remove (saves little time but simplifies).
Recommendation: **Option A first** — it's the only architecturally-different
learner in the roster (sequence model); diversity is exactly what the tree block
lacks. Give it a real chance: GPU + more epochs (see F1/A6) before judging.

---

## 3. ADD plan — what actually buys precision

### A1 — Late-entry PERSISTENCE model ⭐ (the T3 / 95%-tier engine)
The single highest-value addition. A dedicated binary classifier:
**"price is $X past the line with S seconds left — will it HOLD to close?"**
- Trained on intra-window snapshots reconstructed from 1m klines (decades of
  labeled examples per month of data; every window yields multiple snapshots).
- This is an INHERENTLY high-precision learning problem (persistence over seconds
  is far more learnable than direction over minutes) — the structural edge the
  late-entry tier already exploits, but MEASURED per-setup instead of flat odds.
- Output: P(hold | distance, seconds_left, vol, flow, book) → replaces the static
  late-entry heuristic; T3 bets only when P(hold) ≥ 0.93 AND share price < P(hold).
- This is where "95%" becomes an engineering target instead of a wish.

### A2 — p_up distributional pricing (V5 §3)
Conformal band → P(close ≥ beat) → fair share price. Converts every lean into an
EDGE number. T3 requires p_up edge ≥ X¢. (Already planned; unchanged.)

### A3 — Conditional quantile magnitude (V5 §2.5b-ii)
Pinball-loss q10/q50/q90 so the band breathes with volatility. Feeds A2's math and
fixes the "always $40" flatness at the root.

### A4 — Feature batch 2 (V5 §2a-d, one bundle, one retrain)
Multi-depth OBI, cross-venue lead-lag (Coinbase/Bybit lead bps), funding×momentum
interaction, L2 microstructure maturation (auto-improves with uptime).

### A5 — Focal loss / class-balanced focal (CONTINGENT)
Only if today's run shows classbal balanced the leans but accuracy stalls at ~52%:
focal loss focuses the gradient on hard examples instead of just reweighting
classes. One change, one retrain, scorecard-judged.

### A6 — Give TCN a real training budget (with F1's GPU)
3 epochs on CPU is a token effort for the only sequence learner. On GPU: 15–20
epochs with early stopping costs ~2 min and could finally produce a decorrelated
vote. Do together with stacker inclusion (TCN Option A).
**A6 is also the cheap gate for the transformer question**: if a properly-trained
TCN shows decorrelated stacker lift → a small transformer (patch-style attention
encoder) becomes the next sequence candidate (only after the 60–90d window + A4,
transformers are data-hungry at 46k samples). If TCN-with-budget shows nothing →
sequence models don't pay at this data scale; no transformer, no RNNs (LSTM/GRU
would be TCN-flavored clones — rejected for the same reason as a 5th tree model).

### A7 — Hyperparameter tuning per horizon (the overlooked big lever)
The boosters run near-default configs (XGB: 100 trees, depth 4, lr 0.03). A proper
Optuna search per horizon, scored on PURGED WALK-FORWARD SIGN-TRUTH (never raw OOF),
is historically worth more than any new architecture. The automl scaffold exists;
optuna isn't installed. Run as an offline overnight job; adopt a config only when
it beats the incumbent on the held-out scorecard. Highest expected-value item in
the ADD list after A1/A2.

### A8 — Session/time features (nearly free)
Asia/EU/US session flags, minutes-to-next-funding, weekend flag, top/bottom-of-hour
proximity. 5m BTC microstructure is strongly session-dependent; the model is
currently time-blind. A handful of feature slots, joins feature batch A4.

### A1-ext — PATH LABELS for the persistence/anchor engine (adopted from external review)
Extend A1's label space from binary hold/fail to the SHAPE of the move
(first-passage classes): `LONG_DIRECT / SHORT_DIRECT / UP_THEN_DOWN / DOWN_THEN_UP /
BOTH_HIT / CHOP_AVOID`. The triple-barrier scan already computes first-touch order —
the labels are nearly free to derive. This turns the heuristic `_path_outlook`
(CROSS/HOLD/CHOP, currently rule-based) into a LEARNED path classifier, and
"WAIT → SHORT" (fade the fake-out) becomes a first-class predicted signal instead
of advice text. Powers both the Binance long/short entries and Polymarket timing.

### A10-pre — GRADE INVERSION watch (live evidence, 2026-06-12 day 1 of v5)
Observed on the v5 mirror (n=18): **Grade A 43% (3/7) < Grade B 67% (2/3) < Grade C
88% (7/8)** — the confluence grade stratifies BACKWARDS. Mechanism hypothesis: the
grade counts flow AGREEMENT with the lean, and full agreement clusters at move
EXHAUSTION (when CVD/whales/book all confirm, the move is mature → reversal risk
peaks). In 5m mean-reverting tape, *less* confirmation can genuinely be better —
the grade's "more confirmation = better" intuition is a trend-following assumption.
**UPDATE (n=20 rounds, 03:20 ET):** the inversion largely DISSOLVED as n grew —
A 44% (4/9) · B 60% (3/5) · C 50% (3/6). The morning's C-dominance (88%) was itself
small-sample noise. Current honest statement: grades show NO stratification yet in
either direction. The mechanism hypothesis stands as a hypothesis; the trigger below
is unchanged and the per-bucket counts (9/5/6) are still far from it.
**TRIGGER:** if the per-grade split still shows A ≤ C at n ≥ 20 per bucket
(read from the persisted `confluence_grade` column), the grade is REBUILT from
measured fingerprints (A10) with maturity/path inputs (distance already traveled,
streak length, time-in-move) — possibly regime-conditioned (agreement bullish in
TREND, bearish in RANGE). Until resolved: grade letters are UNVALIDATED metadata;
betting discipline = model leans + late-entry persistence structure, not letters.

### A10-voter — k-NN fingerprint VOTER (optional successor seat, after A10)
Once fingerprints exist, the same machinery can cast an ensemble vote: k-nearest-
neighbors over the setup space — "the last 200 times the market looked like this,
61% went UP." Memory-based inductive bias = genuinely decorrelated from the tree
block and linear models (unlike SGD, which was a worse LR). Cheap at predict time,
fully interpretable, and it IS the model form of the similar-setups display.
Candidate for the roster ONLY via the standard gate: decorrelated + lifts the
stacker on held-out data. Sequence: A10 evidence layer first, voter later.

### A10 — Setup fingerprints + "similar setup success" (adopted from external review)
Extend calibration.py's Stage-2 bins (currently regime × conviction only) into a
richer SETUP FINGERPRINT: horizon × path-class × regime × agreement-bucket ×
CVD-sign × book-tilt × vol-bucket. Store per-signal; resolve outcomes; surface in
the UI: "Similar setups: 214 · success 87% · avg favorable $64 · avg adverse $18."
Same Laplace-shrinkage discipline so thin cells can't lie. This is the evidence
layer the T3 gate reads.

### T3 entry rule — FORMALIZED (adopted, with statistics)
A signal enters the 95% tier ONLY when ALL hold:
```
similar-setup sample count  >= 100
observed success            >= 90%
WILSON LOWER BOUND (95% CI) >= 80%       ← the anti-small-sample-lie clause
engines agree (ensemble + path/persistence) or path gives high-confidence timing
p_up edge vs share price    > fees
magnitude band clear of the line; regime not VOLATILE-unstable
```
Below the bar → T2/T1/WATCH/AVOID. No exceptions, no manual overrides.

### A11 — Tail-value ("penny sniper") strategy module — PAPER-TRACK first
Observed in the wild (X, 2026-06: ~10k trades, ~$22k, 1-3¢ entries on 5m windows):
buy the LOSING side near expiry when its price underprices the true reversal
probability (forced sellers + no natural buyers = despair-liquidity vacuum below
fair value). Break-even at 1¢ is a 1% win rate; if true P(reversal) ≈ 5-8% and the
ask is 1-2¢, EV is strongly positive despite a ~90-95% LOSS RATE per trade.

Why it fits us 1:1: the A1 persistence model's output flipped — `1 − P(hold) =
P(reversal)` — IS the fair value of the cheap side. One model, two strategies:
T3 buys the expensive side when P(hold) > price; A11 buys the cheap side when
P(reversal) > k × ask (k ≈ 2-3 safety multiple). Crucially A11 needs NO directional
edge — only calibrated tail probabilities — so it can be the tool's first
profitable mode while directional accuracy is still being built.

Plan: (1) after A1/A2 exist, log PAPER entries whenever P(reversal) ≥ k × Polymarket
ask (needs the A9 CLOB price feed) with full outcome accounting (win rate, avg
payoff, max losing streak); (2) promote only on ≥300 paper trades with positive
expectancy net of spread; (3) capacity honesty: $5-35/trade niche — it prints
lunch money reliably, not wealth; that smallness is WHY the edge survives.
Risks to measure: fill availability at 1-2¢, adverse selection on fills (the
informed sometimes know that side is dead), losing-streak psychology (automation
or nothing). The X post's "9,900% win" = return per winning trade, NOT win rate —
expect 20-40-trade losing streaks as NORMAL operation.

### A12 — Complementary-pair arbitrage DETECTOR (from repo scan, 2026-06-12)
The one structurally riskless edge found in the external bot repos (VoidSignals):
when `ask(UP) + ask(DOWN) < $1 − fees` on the SAME window, buying both legs locks a
guaranteed profit (one side always pays $1; matched pairs can even be merged back to
USDC on-chain). This needs NO model — it's a microstructure scanner. We already plan
the CLOB order-book feed (A9); adding a detector is nearly free.
Plan: DETECT + LOG ONLY (no execution): record every occurrence (depth, duration,
spread sum, time-in-window) to DuckDB + UI alert chip. After 2 weeks of evidence we
know whether the opportunity exists often enough/deep enough to ever justify
execution infrastructure. NOTE: the cross-ASSET variant (BTC-Up + ETH-Down, leshuuuk
repo) is NOT arbitrage — it's correlation betting dressed up; rejected.

### A13 — Early-exit modeling (from repo scan)
Every external bot SELLS positions mid-window (e.g. buy 87¢ → sell at 99¢ or stop at
85¢) — they monetize the probability PATH, not just resolution. Our advice engine
assumes hold-to-resolution. Add exit guidance to the advice layer: when a held side
trades ≥97-98¢ before the close, selling captures ~all the win and removes 100% of
the tail risk (the 2:00-AM-whipsaw losses both round-tripped through ≥90¢ territory).
Pure decision-support text + p_up math; no execution.

### A14 — TRUE Chainlink anchor verification via Polygon RPC (from repo scan)
FrondEnt's assistant reads the actual Chainlink BTC/USD aggregator directly from
Polygon RPC (free, no key). Our §5u rejected "Chainlink via CoinGecko" (stale,
rate-limited) and chose Pyth as proxy — but DIRECT on-chain reads are the real
settlement stream. Plan: poll it alongside Pyth and LOG the per-resolution deltas
(pyth_vs_chainlink at window close). If Pyth tracks within noise → keep Pyth
(faster updates); if systematic gaps appear → switch the anchor. Measurement first.

### A9 — The Polymarket crowd prior (unique edge candidate)
We already subscribe to Polymarket's CLOB WS. The live UP-share price IS the
crowd's probability estimate for the current window. Feed it (and its 30s delta)
as features: the model learns when to trust vs fade the crowd — and
model-vs-crowd DISAGREEMENT is exactly where betting edge lives (p_up vs share
price is the trade). No competing tool feeds the market's own prior back into the
model. Caution: only the CURRENT window's market applies; requires careful
window-alignment in the recorder before it can be trained on (record now, train
when coverage suffices — same pattern as L2).

---

## 4. FASTER cycles — accuracy-neutral only (operator constraint stands)

| Lever | Saves | Risk | Gate |
|---|---|---|---|
| **F1 — GPU for XGBoost (`device='cuda'`), CatBoost (`task_type='GPU'`), TCN (`.to('cuda')`)** — LightGBM already proves the GPU works | XGB 235s→~40s/bucket; total **−1.5h+** | numerical determinism differs slightly from CPU | one A/B retrain, scorecard-neutral |
| F2 — R2 (SGD gone) | −40 min | accuracy likely UP | stacker weights |
| F3 — R4 (single-fit XGB) | −35 min | see R4 | A/B |
| F4 — Stacker folds 5→3 | −2.5 min × 18 ≈ −45 min | small OOF-noise increase | A/B |
| F5 — Horizon-scoped regime rosters (TREND/RANGE only for 5m/15m) | −1h+ | low (non-bet horizons fall to GLOBAL) | A/B |

**Endgame: full retrain in ~1.5–2h** (vs 4.7h today) with equal-or-better accuracy —
which means a DAILY fresh model becomes painless, which itself helps accuracy
(fresher regime fit every day).

---

## 5. Sequencing (strict one-lever-at-a-time)

1. **Today**: measure v5-classbal (sign-truth scorecard before ~02:00 tonight).
2. **If leans balanced + any horizon ≥53%**: ship R1 (Kronos) + R2 (SGD) + F1 (GPU)
   together — all three are accuracy-safe-or-positive — retrain, measure.
3. **Next**: A2+A3 (pricing layer — no retrain needed for A2's math, A3 retrains).
4. **Then**: A1 (persistence model) — the T3 engine. 1–2 days of build, its own
   validation set, shipped alone.
5. **Then**: A4 feature batch, R4/R5/F4/F5 as A/B's allow.
6. **Contingency**: classbal didn't break the bias → A5 focal loss before anything else.

Every step: one change → retrain → 24h → scorecard → docs. No exceptions — that
discipline is *why* the numbers will be real when we get there, and the reason
"together we reach the sky" lands as a measured fact instead of a slogan.

---

## 6. External review cross-check (ChatGPT, 2026-06-12) — adopted / exists / rejected

An independent ChatGPT review of these docs CONVERGED on the same core conclusion
(95%-per-signal impossible; gated 95%-precision tier buildable; selectivity over
frequency). Disposition of its proposals:

**ADOPTED (genuinely additive):** path labels (→ A1-ext), setup fingerprints +
similar-setup-success UI (→ A10), Wilson-lower-bound T3 entry rule (→ formalized
above), "every new component enters as a challenger and earns promotion with live
proof" as a standing rule.

**ALREADY EXISTS (review didn't know):** the "precision meta-gate" = TrainedMetaModel
+ expectedPrecision + grades + isotonic calibration (live since v3, sign-truth-clean
since 2026-06-11); the "5m anchor engine" core = price_to_beat tracker (anchor, line,
path outlook, late-entry) + the Binance tab; WAIT/AVOID success tracking =
avoid_success panels; DuckDB proof layer = the entire §5-audited persistence stack.

**REJECTED:** its V1→V5 history is partly INVENTED — it describes V4 as "challenger
ensembles, path-aware prediction, model debate," which never happened. Actual V4 =
trend-persistence features + P4.3 regime alignment + 130 features (V3_CHANGES §5w).
Do not let a fictional history enter the docs. Also rejected: building "Challenger 1/
Challenger 2" as separate parallel engines — the stacker already IS the stacked
ensemble; new engines (path/persistence) join the EXISTING debate via agreement +
the meta-gate, not as parallel apps.

**NOTED (housekeeping):** `docs/reference/system_architecture.md` is stale (117/126-
feature era) — flagged with a header banner; full rewrite queued AFTER the v5
measurement settles the roster (no point documenting an architecture about to change).

### CONSENSUS (operator + Claude + ChatGPT, 2026-06-12) — the agreed target architecture

NOT parallel challenger engines. ONE decision stack, extended:

```
Single decision stack (existing ensemble + stacker + meta-gate + calibration)
  + path-label head            (A1-ext: learned move-shape, not heuristic outlook)
  + setup-fingerprint evidence (A10: "similar setups: n · success% · MFE/MAE")
  + anchor/persistence module  (A1: the specialized 5m hold/path task)
  + T3 tier gate               (Wilson lower bound ≥80%, n≥100, all-engines-agree)
```

And the agreed sequence, binding:
```
v5 scorecard → roster surgery (Kronos/SGD/FSR-PPO/GPU) → path labels
→ setup fingerprints → Wilson T3 gate → the rare 90–95% precision tier
```

Standing rule (all parties): nothing is added because it sounds advanced; every
piece enters as a measured candidate and earns its seat with sign-truth precision.
ChatGPT's retraction of its invented V4 history is on record — the docs stay forensic.
