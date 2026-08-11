# Oracle Data — Test Handoff for a Fresh Session

**Read this first, before touching anything.** You are inheriting ~21 days of continuous live
deployment data from an Oracle box plus its trained model artifacts. This document tells you what
is already settled (do not re-litigate), what is genuinely open, exactly how to run the existing
tooling, and what to test next in priority order.

Companion document: `EXECUTABLE_EVIDENCE_AND_ENHANCEMENTS_2026-07-25.md` (the measured evidence).
Operating rules: `.claude/skills/quant-ml-expert/SKILL.md`.

---

## 0. The 60-second orientation

This app predicts BTC 5m/15m Polymarket up/down rounds. **Its product is abstention, not
prediction.** Raw direction is a coin flip and has been proven so ~15 different ways. The only
things that have ever measured as real are (a) P(hold) late in a round and (b) a small structural
underpricing of the late leader — and as of 2026-07-25 even (b) **fails its own promotion gate**
on live data.

Three numbers that explain most of the project:

| fact | value |
|---|---|
| Taker fee at an 0.85 ask | **0.89c/share** |
| Measured live EV of the one alive rule | **+0.90c/share** |
| Therefore: the entire edge is | **one fee** |

A +6c gross price move nets **+2.525c** after both fee legs. Any analysis that evaluates outcomes
on gross movement will look profitable and be wrong.

---

## 1. Hard rules — violating these has caused real logged bugs

1. **Too-good = leakage.** Honest 5m direction AUC is 0.50–0.55. Anything ≥0.65 at 5m is presumed
   leaked until the feature↔label time alignment is audited.
2. **Never gate on an unproven signal.** Measure first, with a pre-declared threshold.
3. **A stratifier must stratify.** Any tier/grade/bucket must be *monotone* with n≥100 per level
   before it is surfaced. Non-monotone = noise. (The live ask-bucket EV is non-monotone: +3.06,
   +1.30, +1.25, −1.24, +1.52 — **do not carve a sub-rule from it**.)
4. **Executable prices only.** Entry at the ask, exit at the bid, fees on both legs, latency
   applied. Never midpoint, never best-hindsight exit, never assumed fill.
5. **Frozen gates.** Thresholds are declared before results are seen. A near-miss is a miss.
   Changing a threshold after seeing data resets the evidence clock to zero.
6. **One DuckDB writer.** Never run heavy research against a live writer DB. Snapshot it first.
7. **ASCII-only console output.** Windows cp1252 chokes on `→ ≥ δ`.
8. **A parallel session may be editing this repo.** Reconcile; never clobber.

---

## 2. What is CLOSED — do not reopen without fundamentally new data

| question | verdict | evidence |
|---|---|---|
| Can a model predict 5m/15m direction? | **No** | 13 model families all 0.50–0.535 AUC; 17 microstructure features all ≈0.50; depth AUC 0.53 |
| Does order-flow imbalance predict direction? | **No** | `edge_probe.py`, 10k minutes |
| Fair-value residual ML over the late leader? | **No** | complexity wall: 500–5000 features, ridge/RFF/poly — shuffled-label null scored ≈ the real model |
| 15m static TP-before-SL entry? | **No** | 2,880 cells, **0 positive**, week-flips 0, trailing control 0/1440 |
| Shock fade / shock momentum / rapid round-trips? | **No** | killed offline, replicated live |
| Maker entry (rest at bid)? | **No** | −9.53c, PF 0.57 — fills are adversely selected |
| Cheap-SAFE early entry? | **No** | −2.56c live; confirms BTC state is priced in |

**The offline kills replicated live to within ~0.5c on three independent strategies.** That is the
strongest evidence the research process is calibrated — trust the kills.

---

## 3. The data you are inheriting

### 3.1 Databases (`data/btc_duckdbs/`)

| db | table | rows | what it is |
|---|---|---|---|
| `analytics` | `rule_paper_trades` | 14,998 | **the forward paper ledger** — every strategy's real entries/exits/PnL |
| | `champion_snapshots` | 186,985 | champion decision + every head's output, per tick |
| | `round_state_snapshots` | 186,985 | flip risk, late-shock, round type, p_leader_holds |
| | `persistence_snapshot` | 186,985 | P(hold) inputs + output per tick |
| | `price_to_beat` | 14,372 | resolved rounds with outcomes |
| | `model_predictions` | 108,955 | per-model direction predictions + hit |
| | `forward_ev_ledger` | 7,794 | EV-accounted predictions incl. fees/slippage |
| | `setup_fingerprint` | 7,794 | grade/conviction/agreement per setup |
| `execution_layer` | `pm_round_snapshots` | 1,713,160 | **two-sided Polymarket quotes** + depth d1/d2/d5 |
| | `pm_round_settlements` | 7,860 | official settlement truth |
| `model_metrics` | `ptb_log` | 3,357,067 | P(hold)/tier/band per tick |
| | `direction_log` | 751,480 | direction + probs + verdict per tick |

Span: **2026-07-05 → 2026-07-25** (20.6 days, 4 calendar weeks).
Quote quality: 100% two-sided coverage at every seconds-left band, 1.76s median cadence.

### 3.2 ⚠ WHICH MODELS PRODUCED THIS EVIDENCE — read before any Tier-A result is written up

The Oracle box ran the **400-day** bundle (`full_retrain_400d_complete.json`) with heads that are
**older than the ones on the local disk**. Every live number describes THOSE weights:

| head | Oracle (produced the 21 days of evidence) | local disk |
|---|---|---|
| `persistence_model` — **P(hold), the core edge** | **2026-06-21** | 2026-07-03 (prodrefit) |
| `bigmove_keeper` | 2026-06-18 keeper4 (**$ thresholds**) | 2026-07-03 keeper5 (**bps labels**) |
| `path_forecaster` | v4 | v4.1 |
| `fade_model` | v5-causal | v5-gate-fail |
| bigdrop / directional / activity / champion_meta | — identical on both — | |

Consequences you must respect:

1. **Label every Tier-A result with the bundle it tested.** "P(hold) is calibrated" is a claim
   about the **June-21** model, not about anything on the local disk.
2. **The bps-label upgrade has never run in production.** It is unvalidated live. Do not describe
   it as proven.
3. Do not silently swap in local artifacts and continue scoring against the same live snapshots —
   that would join one model's predictions to another model's recorded outputs.

**Version-string collision (fixed 2026-07-25, but old artifacts still carry it):**
`round_state_heads.pkl`, `signed_quantile_model.pkl` and `selectivity_models.pkl` shipped with
*identical version strings but different weights* on the two boxes. The trainers now append the
training-window tag (e.g. `...-400d`) so future bundles are distinguishable. **Any artifact
produced before 2026-07-25 cannot be attributed by version string alone — compare file hashes.**

### 3.3 Known data limitations — state these in every output

- **No `top_bid_size` is recorded.** Exit capacity cannot be measured; every study so far assumed
  1 share on the top bid. Entry capacity *is* real (`top_ask_size` + cumulative d1/d2/d5).
- **`fee_rate_bps` in the L2 snapshot is all zeros** — unusable. Always compute
  `fee = 0.07·p·(1−p)` per leg.
- **4 calendar weeks < the 8-week gate.** Round counts pass; the calendar does not. Nothing from
  this data is promotable. It can **kill** a hypothesis, never promote one.

---

## 4. Do this FIRST — integrity before analysis

```bash
python backend/research/validate_l2_snapshot.py --db data/research_snapshots/polymarket_l2_2026-07-25.duckdb
```

Then for the incoming Oracle DBs, run the same class of checks before trusting anything:
row counts, timestamp monotonicity, duplicate keys, settlement join coverage, and **whether the
side encoding (`current_side` 1.0 = UP) still holds** — a silent flip would invert every result
while looking perfectly plausible. The existing guard pattern is
`verify_side_convention()` in `run_15m_executable_surface.py`; it passed at 91.2% agreement.

**Snapshot before heavy work:**
```bash
cp data/btc_duckdbs/analytics.duckdb data/research_snapshots/analytics_<date>.duckdb
```

---

## 5. Existing tooling — how to run it

| script | purpose |
|---|---|
| `backend/research/executable_surface_config.py` | the FROZEN grid + gate, hashed. Print it to see every declared parameter. **Changing this changes the hash and invalidates prior results.** |
| `backend/research/executable_fill_engine.py --selftest` | 13 selftests. **Run this first, always.** If it fails, nothing downstream is trustworthy. |
| `backend/research/validate_l2_snapshot.py` | Phase-2 data-quality gate |
| `backend/research/run_oracle_executable_surface.py --horizon 15` | powered surface + null battery on deployment data (~40s) |
| `backend/tests/test_paper_trading_integrity.py` | paper-ledger accounting invariants |

The fill engine is the **only** place a fill may be simulated. Do not write a second one — the
whole point is that all tests share identical semantics. Its fast path (`net_path` +
`first_barrier`) is proven equal to the reference (`simulate_trade`) on 640 randomized barrier
pairs, so use the fast path for grid work.

---

## 6. What to test next — ranked, with honest priors

### TIER A — validate the heads we already ship (highest value, cheapest, none done yet)

These 186,985-row live snapshot tables have **never been scored against outcomes**. This is the
single largest untapped value in the handoff, and every item is a read-only join.

**A1. P(hold) live calibration — the core product claim.**
Join `persistence_snapshot` / `ptb_log` → `price_to_beat` outcomes. Build a reliability diagram:
predicted P(hold) decile vs realized hold rate, split by `seconds_left` band and horizon.
*Prior: this is the one validated edge — expect it to hold, but calibration may have drifted.
If P(hold)=0.95 realizes at 0.88, every downstream gate is miscalibrated.* **Do this first.**

**A2. Champion decision scorecard.**
`champion_snapshots.champion_action` vs realized outcome, by `champion_confidence` and tier.
Does AVOID actually avoid losses? Does SETUP precision beat WAIT? Report Wilson-LB per tier and
check **monotonicity** — a non-monotone confidence scale must be rebuilt, not shipped.

**A3. Round-state heads: flip risk and late shock.**
`round_state_snapshots.flip_risk` vs realized flip; `late_shock_20/50/100` vs realized moves.
These were validated only in the final 30–120s window — verify that boundary holds and that they
are not being read outside it.
*Prior: flip risk was reported at AUC 0.891 at in-round checkpoints. Confirm or kill.*

**A4. Big-move / activity / drop heads.**
`champion_snapshots.p_big_move`, `p_activity`, `p_big_drop` vs realized. Offline AUCs were
0.73–0.75. *Prior: these are the most robust heads in the system; expect them to survive. If they
don't, suspect a serve-time feature parity break.*

**A5. Direction ceiling, live.**
`direction_log` (751k rows) and `model_predictions` (109k) → sign-truth on **committed votes only**
(UP/DOWN, NEUTRAL excluded — the neutral-poisoning fix). *Prior: ~50%. This is a confirmation, not
a search. If it reads ≫55%, look for leakage before celebrating.*

**A6. Calibration drift across the 21 days.**
Every head above, split week by week. Decay is the thing that kills deployed models silently.

**A7. Champion vs the market baseline.**
`forward_ev_ledger` has fee- and slippage-accounted net PnL. Compare champion decisions against
(i) never trade, (ii) always take the leader, (iii) the market's own implied probability. *The
market price is the baseline to beat — not 50%.*

### TIER B — genuinely open questions, build only after Tier A

**B1. Can any causal rule harvest the exit-timing opportunity?**
The surface study found a profitable executable exit exists in **~90%** of leader entries (median
7–40s) yet **no static barrier captures it**. The honest follow-up: predict
`P(profitable exit within next Δt)` causally, then test whether acting on it beats hold-to-settle
*after fees*. Δt ∈ {5s, 10s, 30s}.
*Prior: pessimistic. Static barriers failing is weak evidence that the signal is unpredictable,
not strong evidence a dynamic rule works. Pre-declare the gate and run the full null battery.*

**B2. Why did maker entry lose 9.53c? (adverse selection autopsy)**
Test whether maker fills cluster disproportionately on rounds the leader loses. This *explains an
existing kill*, which is more valuable than it sounds: if fills are adversely selected, then every
passive-execution idea in the backlog is dead too, not just this one.

**B3. Ask-side edge duration.**
`P(the ask stays ≤ X for N seconds)` from `pm_round_snapshots`. Testable today without bid size.
Feeds any future execution model and is a prerequisite for claiming any edge is capturable.

**B4. P(hold) as a *pricing* input rather than a trigger.**
We know P(hold) is calibrated and we know the market prices the leader near-efficiently. The
untested question is whether `P(hold) − ask` has any residual predictive content **at matched ask
and seconds-left**. Ask-matching is essential — without it you will rediscover the structural
intercept and mistake it for alpha.
*Prior: the complexity wall says no. But it was never tested with this much live data at matched
price.*

### TIER C — do NOT do these

- Another direction model, another TA indicator, another ensemble seat
- A transformer / deep model for direction
- Re-tuning any frozen threshold to make a result look better
- Any new 15m static-barrier entry strategy
- Promoting anything to real money

---

## 7. Model artifacts from Oracle

When the `.pkl` files arrive:

1. **Compare versions, don't assume identity.** Each bundle carries a `version` / `HEAD_VERSION`
   string and most carry `test_auc` / candidate metrics. Diff Oracle vs local for every head.
2. **If they differ, that is an A/B, not a bug.** The Oracle box may have trained on a different
   window. Score both against the same live outcomes (Tier A) and report which generalizes.
3. **Do not overwrite local artifacts.** Load Oracle bundles from a separate directory and compare.
   `data/saved_models_400d_backup/` is the existing rollback precedent.
4. Check `refit_on_all` / `n_train` flags — bundles from the validated-refit flow record whether
   production weights were refit after the gate, and the candidate test metrics are preserved
   verbatim as the honest record.

---

## 8. Reporting standard

Every result you produce must state, in the header:

```
n = <independent rounds>      weeks = <calendar weeks>
config/hash = <if a frozen grid was used>
STATUS = PILOT (may kill, may not promote)  |  PROMOTION-CAPABLE
```

And must include, where a claim of edge is made:
- bootstrap CI (**day-block**, not naive — trades within a day are not independent)
- profit factor
- week-by-week stability with sign-flip count
- a matched control (ask-matched and/or trailing-side)
- multiple-testing correction across the **whole** declared family, not the interesting cells

**Publish negative results.** They are the most reliable output this project produces.

---

## 9. Current open state

- `LATE_LEADER_30S_V1` is frozen and **failing its gate** (EV +0.90c vs +2c, block-LB −0.60c,
  PF 1.08). The 8-week calendar gate lands ≈ **2026-08-30**. **Do not touch its thresholds** —
  any edit resets the clock.
- Local recorders have been down since 2026-07-04; the local L2 archive is frozen at 3 days.
- A 1265-day retrain stalled mid-run on 2026-07-04 without writing its completion marker. It is
  resumable from cached daily files via `start.bat`.
- The Oracle box is the only source of gate-qualifying evidence. Keep it running, untouched.

---

## 10. The question to keep asking

Not:

> Which model predicts BTC direction best?

But:

> Which simple, frozen, executable policy produces repeatable positive post-cost expectancy that
> survives matched controls, latency, independent weeks, and untouched forward data?

As of 2026-07-25 the honest answer is **none yet** — and the system's value is that it can say so
with numbers instead of hope.
