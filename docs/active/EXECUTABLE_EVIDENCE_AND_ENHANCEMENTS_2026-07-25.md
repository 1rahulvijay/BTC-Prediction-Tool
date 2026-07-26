# Executable Evidence & Enhancement Plan — 2026-07-25

**Status: canonical.** This document supersedes prior speculation about 15m entry strategies and
about the promotion readiness of `LATE_LEADER_30S_V1`. Everything below is measured, with the
sample size, window and config hash attached. Where a result is underpowered it says so.

---

## 0. One-paragraph summary

Twenty-one continuous days of live deployment data (Oracle box, 2026-07-05 → 2026-07-25) plus a
powered offline surface test produced three findings. **(1)** The one alive rule,
`LATE_LEADER_30S_V1`, is *positive but fails its own pre-declared promotion gate* — EV +0.90c
against a +2c requirement, lower bound negative, PF 1.08 against 1.20. **(2)** The 15m
TP-before-SL hypothesis is **dead**: 2,880 tested cells, **zero** positive, every week negative on
both sides. **(3)** A genuine and reproducible structure exists — a profitable executable exit
appears in ~90% of leader entries within seconds — but **no static barrier rule captures it**.
Opportunity is not capturability. No strategy is promotable; the app's job remains abstention.

---

## 1. Data provenance

| source | span | content |
|---|---|---|
| `data/btc_duckdbs/analytics.duckdb` | 2026-07-05 → 07-25 (20.6d) | 14,998 paper trades, 14,372 rounds, 186,985 champion/round-state snapshots |
| `data/btc_duckdbs/execution_layer.duckdb` | same | 1,713,160 two-sided quote snapshots; 7,860 official settlements |
| `data/btc_duckdbs/model_metrics.duckdb` | same | 3.36M ptb_log, 751k direction_log |
| `data/research_snapshots/polymarket_l2_2026-07-25.duckdb` | 2026-07-02 → 07-04 (3d) | 25.8M L2 book levels, 133-deep ladders (immutable snapshot) |

Quote quality (Oracle): **100% two-sided coverage at every seconds-left band**, 1.76s median
cadence, median 198 shares at the top ask, cumulative depth (d1/d2/d5) verified monotone.

L2 snapshot integrity: 0 crossed books, 0 duplicate seq, 0 non-monotonic timestamps, 0 orphan
assets, 449/449 rounds joined to official settlement.

---

## 2. `LATE_LEADER_30S_V1` — live verdict: FAILS ITS GATE

n = **2,145** settled paper trades over 20.6 days.

| gate criterion | required | measured | |
|---|---|---|---|
| independent trades | ≥ 500 | **2,145** | PASS |
| EV per share | ≥ +2.0c | **+0.90c** | FAIL |
| lower bound (day-block bootstrap) | > 0 | **−0.60c** | FAIL |
| profit factor | ≥ 1.20 | **1.08** | FAIL |

Supporting detail:

- Offline estimate was **+2.5c** (14,226 Kaggle rounds). Live delivered **+0.90c** — a **64%
  degradation**. "Historically profitable" ≠ "currently executable".
- Weekly EV: **−2.05c, +0.56c, +3.26c, −1.04c** — two of four weeks negative, with sign reversals.
- **Median +10.31c vs mean +0.90c** with an 84.8% win rate: the classic pick-up-pennies profile.
  Frequent small wins, rare ~−85c losses when the leader flips. This is why PF is 1.08 despite
  the flattering win rate.
- Ask-bucket EV is **non-monotone** (+3.06, +1.30, +1.25, −1.24, +1.52). Per the stratifier rule,
  that is noise. **Do not carve a sub-rule from it.**
- **The edge is the size of one fee.** At an 0.85 ask the taker fee is 0.89c; measured EV is
  +0.90c.

**Ruling:** remain paper-only. Do not re-tune. The 8-week calendar gate lands ≈ **2026-08-30**.

---

## 3. Offline kills replicated live — the methodology validates

| strategy | offline prediction | 20-day live | |
|---|---|---|---|
| Mid-round scalp | −4.1c | **−3.90c** | replicated |
| TP-or-settle | −1.8 … −4.4c | **−3.54c** | replicated |
| Straddle | −10.7c | **−9.10c** | replicated |

Three independent predictions, three near-exact hits. **The negative results transfer.** This is
the strongest evidence available that the research process is calibrated, and it is the reason
the kills below should be trusted.

### New kills earned in this window

| rule | live EV | PF | reading |
|---|---|---|---|
| `LATE_LEADER_MAKER_V1` | −9.53c | 0.57 | passive fills are **adversely selected** — you get filled when wrong |
| `CHEAP_SAFE_EARLY_V1` | −2.56c | 0.90 | confirms the shuffled-gate nulls: BTC state is already priced in |
| `LATE_LEADER_60S_V1` | −2.93c | 0.81 | the EV-vs-expiry ladder: 30s is not merely best, it is the only non-negative rung |
| `LATE_LEADER_15S_V1` | −2.99c | 0.80 | same |
| `SHOCK_SNIPER_LIVE_V1` | −1.45c | 0.92 | the stale-ask hypothesis is dead at 1s resolution |
| `LATE_LEADER_15M_SHADOW_V1` | −0.20c | 0.98 | 15m late-leader ≈ zero (n=85, slow) |

---

## 4. The 15m executable surface — powered KILL

Run: `backend/research/run_oracle_executable_surface.py`, config hash **`964ec1145c8baa51`**,
1,947 rounds, 4 calendar weeks, qty=1.

```
cells tested (n>=100): 2,880     positive EV: 0  (0.0%)
survive Benjamini-Hochberg:  0
best cell in the entire grid:  LEADER cp=60 tp=10 sl=10 lat=2000  ->  -0.88c
```

| control | result |
|---|---|
| week stability | **flips = 0** on all 20 side×checkpoint rows; every week negative |
| trailing-side control | **0 of 1,440** matched pairs positive on both sides |
| two-sided sum | mean(LEADER + TRAILER) = **−5.94c** ≈ pure round-trip cost drag |

Buying both sides at any checkpoint, any barrier, any latency loses almost exactly the spread
plus both fee legs. That is a clean statement of efficiency on this axis.

### The paradox — and what it actually means

| entry | LEADER: profitable exit ever appeared | median wait |
|---|---|---|
| 720s left | **90.0%** | 39s |
| 240s left | **92.6%** | 22s |
| 60s left | **89.2%** | 9s |

~90% of the time a profitable executable exit exists, typically within 10–40s — **and no static
rule captures it.** The hazard curve is hindsight: it says *some* moment was profitable, not that
a rule could know which. Committing TP/SL in advance means the stop side collects you on paths
where the profitable moment never arrives first.

**Do not build further 15m static-barrier entry strategies. This question is answered.**

### Methodology note worth preserving

The 3-day L2 pilot reported `LEADER @30s = +12c EV, 88% win` at n=59. It was flagged as
suspicious *before* validation on two grounds: it was ~5× the known offline value for the same
structural effect, and its latency ordering was incoherent. With 17× more data it went
**negative**. The instinct to distrust an extraordinary in-sample result was correct, and the
pre-declared gate prevented it from ever being promoted.

---

## 5. Enhancements implemented (2026-07-25)

### 5.1 Gate math → day-block bootstrap · `backend/database.py`

The normal-approximation lower bound assumes independent trades. They are not: trades inside one
day share a volatility/liquidity regime, so the effective sample is closer to the number of days
than the number of trades. `rule_paper_summary` now resamples **whole days** (2000 draws, fixed
seed → reproducible tile) and returns `ev_lb_block_c` as the **gate-bearing** number.
`ev_lb_c` (naive) is retained only for comparison and must never be gated on.

Measured on the live ledger: naive −0.56c vs block −0.60c. Both fail today; the naive figure is
systematically over-confident, which is the exact direction that promotes a dead rule.

### 5.2 Win rate demoted · `src/main.js`

The RULE STATUS tile headlined **84.8% win rate** directly above a 💀 panel whose entire lesson is
"high win rate ≠ profit". The tile now leads with a **verdict chip** (`FAILS GATE (3/4 unmet)`),
shows EV / LB_blk / PF as the gate-bearing trio, and demotes win rate to a de-emphasised context
line. A **negative-skew warning** appears automatically when win rate > 60% and |median| > 3×|mean|
— the pick-up-pennies signature.

### 5.3 Unmeasurable triggers labelled · `backend/database.py` + `src/main.js`

A rule that fires too rarely is not "collecting" — it can never reach a gate-qualifying n. Firing
rate is now derived from the **observation window** (ledger span), not days-on-which-it-fired,
and classified:

| rule | fires/day | days to n=500 | label |
|---|---|---|---|
| `LATE_LEADER_30S_V1` | 104.3 | 5 | OK |
| `CHEAP_SAFE_EARLY_V1` | 49.5 | 10 | OK |
| `LATE_LEADER_15M_SHADOW_V1` | 4.1 | 121 | **SLOW** |
| `MODEL_STRADDLE_LIVE_V1` | 3.2 | 158 | **SLOW** |
| `MODEL_RIDE_LIVE_V1` | 0.05 | **10,288** | **UNMEASURABLE** |
| `MODEL_FADE_LIVE_V1` | 0.00 | never | **NEVER_FIRES** |

The UI now renders `⊘ UNMEASURABLE (1 fires in 21d)` instead of implying a pending verdict. The
label is derived from observed rate, so it self-corrects if a trigger revives.

### 5.4 Research infrastructure added

| file | role |
|---|---|
| `backend/research/executable_surface_config.py` | the FROZEN grid + gate, hashed (`964ec1145c8baa51`); 18,432 declared cells; fee model |
| `backend/research/executable_fill_engine.py` | one deterministic fill engine; 13 selftests; fast path proven equal to reference on 640 randomized barrier pairs |
| `backend/research/validate_l2_snapshot.py` | Phase-2 data-quality gate; aborts research on incomplete execution fields |
| `backend/research/run_15m_executable_surface.py` | L2-snapshot surface runner (pilot) |
| `backend/research/run_oracle_executable_surface.py` | powered runner + null battery on deployment data |

**Added later the same day (second session).** Analyses:

| file | role | result doc |
|---|---|---|
| `reconcile_late_leader_ledger.py` | round-by-round ledger vs replay split (selection vs accounting) | `LATE_LEADER_RECONCILIATION` |
| `test_oracle_capacity.py` | EV by intended size, walking the recorded ask ladder | `ORACLE_CAPACITY_TEST` |
| `test_head_calibration.py` | live reliability/Brier/ECE/skill for every deployed head | `HEAD_CALIBRATION` |
| `test_stopping_baselines.py` | the pre-declared gate on the dynamic-exit lane (7 frozen policies) | `STOPPING_BASELINES` |
| `test_complement_and_opening_drift.py` | complement arbitrage + next-round opening drift | `STRUCTURAL_EDGE_HUNT` |
| `test_virtue_complexity_late_leader.py` | Kelly/Malamud/Zhou complexity wall on late-leader mispricing | `VIRTUE_COMPLEXITY_LATE_LEADER` |

### Recorder provenance upgrade (2026-07-25, second session) — 62 columns

Closes the recording gaps listed in Tier 1 of the backlog. `live_btc_updown_recorder.py` now
persists, per snapshot:

| field | why it was missing before |
|---|---|
| `up_top_bid_size`, `up_b1/b2/b5` (+ down) | **exit capacity was unmeasurable** - every study assumed a 1-share exit |
| `up_ladder`, `down_ladder` | full bid+ask ladders (JSON, top 12 levels/side) - lets a replay compute exit VWAP at any size |
| `decision_ts`, `book_age_s` | *when* the row was assembled, and how stale the older book already was - the reconciliation showed EV moves ~0.6-0.8c per second, so this is load-bearing |
| `up_recv_ms`, `down_recv_ms` | per-fetch round-trip latency |
| `up_book_ts`, `down_book_ts`, `*_book_hash` | venue timestamp + book hash for dedupe/gap detection |
| `artifact_hash` | **content** hash of the served weights, stamped on every row - version *strings* collided across boxes (same string, different weights), making live results unattributable |

Migration is additive (`ADD COLUMN IF NOT EXISTS` for every column), so an existing recorder DB
gains all of them without a rebuild; pre-upgrade rows keep NULL, which is correct - that data was
never observed. Verified: fresh-DB creation, correct VARCHAR/DOUBLE typing, full-row insert,
ladder JSON round-trip, and legacy-DB migration to all 62 columns.

**Standing guards** - each is self-testing and fails loudly if its specific bug class returns.
Run all three after any change to strategies, the recorder, or docs:

| guard | command | the bug it prevents |
|---|---|---|
| `audit_strategy_registry.py` | `python backend/research/audit_strategy_registry.py` | a strategy that trades but is invisible (missing from the server payload or the UI name map). Uses an **AST parse, not regex** - an earlier regex version raised a false positive on rules registered through a loop variable. `--selftest` reproduces that exact pattern. |
| `test_oracle_capacity.py --selftest` | `python backend/research/test_oracle_capacity.py --selftest` | silent ladder-walk arithmetic errors. Two of the original ad-hoc assertions were themselves wrong (`d1 == top` means no depth in the 1c band; float `==`), which briefly cast doubt on a correct result. |
| `check_doc_tables.py` | `python backend/research/check_doc_tables.py` | markdown tables whose rows do not match their header width (cells silently dropped when rendered). Handles code-span and escaped pipes; the naive first version produced two false positives and crashed on cp1252. |
| `live_btc_updown_recorder.py --selftest` | `python backend/polymarket/live_btc_updown_recorder.py --selftest` | the row-literal/`COLS` mismatch that would break **every insert** and silently end evidence collection. Counts the row with an **ast parse** - an ad-hoc comma counter miscounted a ternary and reported a false mismatch. Also checks text-column declaration and artifact-hash determinism. |

> Three separate false alarms on 2026-07-25 (regex vs loop variables, `==` on floats, comma-counting
> a ternary) all came from ad-hoc checks written in the moment. That is why each guard above is a
> committed file with its own `--selftest`, rather than a snippet in a terminal.

**Fee rule (non-negotiable):** every `fee_rate_bps` in the L2 snapshot is `0.0` and is therefore
unusable. Costs use the documented taker formula `0.07·p·(1−p)` on both legs. Trusting the
recorded zero would manufacture a fake ≈1.7c/share edge in every cell.

**A selftest worth keeping:** a +6c gross move nets only **+2.525c** after both-leg taker fees.
The engine correctly refused to trigger a 3c TP on it; the original test assertion was wrong and
was fixed. Roughly **3.5c of every round-trip is fees alone** at mid prices, before spread. Any
surface evaluating barriers on *gross* movement will look profitable and be wrong.

---

### 5.5 Production hardening merged FROM the Oracle deployment

The Oracle box had been running publicly for 21 days and its `server.py` / `database.py` were five
days newer than local. Those are fixes **earned by production**, not by analysis. Merged
file-by-file (neither tree copied wholesale — they had diverged in *both* directions):

| fix | file | why it matters |
|---|---|---|
| `ADMIN_TOKEN` + `_require_admin()` | `server.py` | local had **no auth** on `/api/relearn`, `/api/backtest`, `/api/historical-replay/run`. A viewer with dashboard access could trigger a multi-hour retrain or a CPU-bound replay. Gated by `X-Admin-Token` header. **Unset = no gate**, so local dev is unchanged; set `BTC_ADMIN_TOKEN` before exposing the box. |
| `_accuracy_alltime()` + `_ptb_alltime_accuracy()` | `server.py` | the tracker keeps a capped ring buffer, so on a long-running box the headline win rate silently becomes "accuracy over the last N rounds" while presenting itself as the overall figure. Now sourced from DuckDB (all resolved rounds), cached 60s, falls back to last-good on error. |
| `DISABLED_PAPER_RULES` | `database.py` | env kill-switch (`BTC_DISABLED_PAPER_RULES=A,B`) that stops a rule being logged at all. **This is the only sanctioned way to silence a rule**: it changes no threshold, so it cannot re-tune a frozen spec, and the disabling is visible in the environment rather than buried in a diff. Complements §5.3 — the label keeps the honest record, the switch stops the noise. |
| NaN/inf sanitisation | `database.py` | pandas turns SQL NULL floats into NaN; NaN/Inf is not JSON-compliant and crashes response rendering **outside** the endpoint's try/except — a 500 on a fresh install with zero resolved rows. Applied at both `to_dict("records")` sites. |
| buffer sizes 500→1000, 250→1000 | `price_to_beat.py` | more retained history for the UI feed |

Verified: admin gate rejects a bad token (403) and accepts a good one; the kill-switch blocks a
disabled rule while an enabled rule still logs.

### 5.6 Version-string collision fixed

`round_state_heads.pkl`, `signed_quantile_model.pkl` and `selectivity_models.pkl` shipped with
**identical version strings but different weights** on the two boxes. The app's version-aware
retrain logic keys off that string, so it could not tell them apart — and no live result could be
attributed to a specific set of weights.

The keeper heads already avoided this by embedding a training-window tag. The three trainers now
do the same:

```
2026-07-02-round-state-shadow-v1-400d
2026-06-21-cqr-recency-h5-15-400d
2026-06-15-lr+rf-ensemble-400d
```

Files: `train_round_state_heads.py`, `train_signed_quantiles.py`,
`decision/train_selectivity_models.py`.

⚠ **Artifacts produced before 2026-07-25 still carry the collision.** For those, compare file
hashes — never version strings alone.

### 5.7 Deployment topology (reference, from the Oracle extract)

Three systemd units, all `Restart=always`, under `deploy/`:

| unit | role |
|---|---|
| `btc-backend.service` | uvicorn on `0.0.0.0:8000`, with `ExecStartPre=check_model_compatibility.py` |
| `btc-recorder.service` | `live_btc_updown_recorder.py --settle-batch 100` (the quote bridge + settlements) |
| `btc-frontend.service` | static UI |

Serving env is correctly **frozen**: `BTC_FREEZE_MODEL=1`, `BTC_SKIP_BACKFILL=1`,
`BTC_HISTORICAL_DAYS=3`, `BTC_RUN_STARTUP_BACKTEST=0`, `BTC_MAIN_LOOP_SEC=3`. The box **serves and
records; it does not retrain.** That separation is what makes its 21 days usable as evidence — the
models never changed underneath the measurements.

**Missing from the deployment and worth adding:** `BTC_ADMIN_TOKEN` (now supported, see §5.5).

## 6. Enhancement backlog — ranked by evidence

### Tier 1 — do next

1. **Keep the Oracle box running, untouched.** It is the only source of gate-qualifying evidence
   and is 21 days into an 8-week clock (≈ 2026-08-30). *Any threshold change resets it to zero.*
2. **Restart the local recorders.** They have been down since 2026-07-04; the local L2 archive is
   frozen at 3 days. `start.bat` also resumes the stalled 1265d retrain from cache.
3. **Record capacity honestly.** The compact recorder stores `top_ask_size` but **no
   `top_bid_size`**, so every exit in this study assumed 1 share. Adding bid size is the single
   cheapest upgrade that would let size-aware exit VWAP — and therefore real capacity — be
   measured at all.
4. **Deploy the merged fixes to Oracle and set `BTC_ADMIN_TOKEN` there.** The box is publicly
   reachable and currently ungated (§5.5). Redeploying restarts the process — acceptable, since
   the paper ledger and recorder state are durable in DuckDB — but **do not change any rule
   threshold in the same deploy**, or the 8-week evidence clock resets.

**Done 2026-07-25:** §5.1 block-bootstrap gate · §5.2 win-rate demotion · §5.3 unmeasurable-trigger
labels · §5.4 research infrastructure · §5.5 Oracle production merge · §5.6 version-collision fix.

### Tier 2 — worth doing, evidence-supported

4. **Retire or explicitly mark the unmeasurable triggers** in the strategy lab (done in UI; the
   underlying triggers remain wired so the record stays honest).
5. **Re-run the powered surface on 5m** (5,838 rounds available) for symmetry with 15m. Expect a
   kill; cheap to confirm.
6. **Apply the day-block bootstrap to every scorecard**, not only the rule tile — the same
   independence error exists wherever trades are pooled.

### Tier 3 — open questions, not yet actionable

7. **Dynamic exit rather than static barriers.** The hazard result says opportunity exists ~90% of
   the time but static rules cannot harvest it. The honest follow-up is whether *any* causal rule
   can — and the prior after this study should be pessimistic. Any attempt must be pre-declared and
   run against the same null battery.
8. **Capacity/size study** — blocked until (3) is fixed.

### Explicitly closed — do not reopen without new data

- 15m static TP-before-SL entry strategies (killed, §4)
- Fair-value residual ML over the late leader (complexity wall found no conditional lift)
- L2 / order-flow directional models (AUC ≈ 0.50–0.53)
- Shock fade, shock momentum, rapid round trips, maker entry, cheap-SAFE, straddles

---

## 7. Reading discipline

1. **n < 100 per cell is noise.** Gate-qualifying means ≥500 independent rounds **and** ≥8
   calendar weeks. Round count alone is not sufficient — 4 weeks is still 4 weeks.
2. Cents ≈ % of stake (1 share of a $1 market).
3. A win rate is context, never a verdict. EV, block-bootstrap LB and PF decide.
4. A pilot may **kill** a hypothesis; it may **never promote** one.
5. Ranking cells is not selection. Only the null battery plus BH correction across the whole
   declared family decides whether a cell means anything.
6. **No strategy in this system is real-money approved.** Promotion is a separate, explicit
   operator decision after its frozen gate passes on untouched forward data.
