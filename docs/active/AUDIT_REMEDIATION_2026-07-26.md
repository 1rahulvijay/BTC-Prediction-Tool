# AUDIT REMEDIATION — `COMPLETE_TRADE_FORECAST_V1` (2026-07-26)

> **2026-07-27 COMPLETION ADDENDUM.** The serving/evidence gaps left after this audit are now
> implemented: immutable candidate eligibility, direct capacity q10 training, durable disk
> spool/replay, own-L2 outcome reconstruction, explicit evidence-run selection, both frozen
> protocol hashes, deterministic bundle inventories, and verified serving without the original
> raw training parquet. See
> [`SERVING_EVIDENCE_COMPLETION_2026-07-27.md`](SERVING_EVIDENCE_COMPLETION_2026-07-27.md).
> This closes code-path gaps only; M0 still requires fresh forward evidence and no profitability
> claim is made.
>
> **This addendum supersedes the historical status rows for defects 9 and 11 below.** Defect 9's
> portability problem is fixed by a self-contained, content-addressed bundle that serves without
> the raw parquet at its old path; external private-key signing remains optional hardening.
> Defect 11's authoritative path is replaced by manifest-verified promotion, a complete bundle
> inventory, evidence-mode refusal of legacy fallback, and a frozen no-violation evidence clock.
> The older table and "deliberately not done" paragraphs remain as the 2026-07-26 audit record.

> **SECOND REVIEW PASS APPLIED.** A follow-up review found four further issues, one of which was a
> bug introduced by the first pass. All four are fixed; see "Second review pass" at the end.
> **M0 is NOT ready to spend** — the lane now runs under
> [`PREREG_COMPLETE_TRADE_M0_V2.md`](PREREG_COMPLETE_TRADE_M0_V2.md) (`138616d3893c…`) and its
> promotion test requires forward data collected after that freeze.

External audit of commit `b7306cb` found 14 defects that let the Complete Trade Forecaster produce
overconfident economic estimates. This records what was fixed, what was found to be different from
the audit's description, and what remains.

**Regression suite: `python -m backend.trade_forecast.test_audit_fixes` — 60 checks, PASS.**
One test per defect, because every one of these failures is silent: the pipeline keeps running and
the report looks healthy while the damage shows up only as apparent selectivity.

---

## Status by defect

| # | Defect | Status |
|---|---|---|
| 1 | Future labels past contract expiry | **FIXED** — builder + serving |
| 2 | M0 evaluated candidates, not decisions | **FIXED** — `exposure_id` |
| 3 | M0 prediction and outcome misaligned | **FIXED** — frozen aligned pair |
| 4 | Scenario engine not a valid joint path model | **CONTAINED** — diagnostic-only; direct plan models NOT built |
| 5 | Future share models exclude illiquid states | **NOT DONE** — needs two-stage retrain |
| 6 | Quote-survival label incorrect | **FIXED** — size-aware |
| 7 | Missing live data became neutral values | **FIXED** — no forecast |
| 8 | Evidence silently lost or overwritten | **FIXED** — append-only + monitored |
| 9 | Deployment needs training data on Oracle | **NOT DONE** — needs signed bundle |
| 10 | Loaders reload mid-evidence-run | **FIXED** — `freeze_guard.py` |
| 11 | Strict artifact identity disabled | **UNCHANGED** — operator gate, correctly off in transition |
| 12 | Official settlements not filtered | **FIXED** — frozen allowlist |
| 13 | Capacity gated on median | **FIXED** — q10 |
| 14 | Head health not wired into all decisions | **FIXED** — extended to complete-trade lane |

---

## 1. Post-expiry targets (the first correction, as instructed)

`FUTURE_OFFSETS_S` reaches 120s while entry checkpoints go down to 30s, and the builder created
every offset unconditionally. A decision with 30s left carried BTC prices from **90 seconds after
settlement**.

One shared definition, `trade_schema.target_offset_valid(offset, seconds_left)`, is now used by
both the dataset builder and serving — so the two cannot drift apart, which is how the leak
appeared in the first place. Invalid targets are `NULL`, never `0.0`.

**Right-censoring is handled precisely rather than by blanket NULL.** For a crossing label, "did it
reach +3c by 120s?" at a 30s checkpoint is still answerable when it *already crossed* inside the
30s that existed — that is a genuine `1`. Only the non-crossing case is unknown, and only that
becomes `NULL`. Writing those as `0` would have taught the model that late-checkpoint trades
reliably fail to reach target, which is an artifact of the clock rather than the market. A
`target_valid_{offset}s` column records which is which.

Serving applies the same rule, so a path point the round cannot reach is never displayed.

## 2. `exposure_id` — one decision per market moment

```
exposure_id = round_id + "@" + seconds_left
```

The pilot is **395 rounds / 24,996 rows** — rows are *candidates* (checkpoint × side × quantity),
not trades. M0 now selects the single highest-scoring candidate per exposure, so a deployable
policy emits exactly one action. `candidates_considered`, `independent_exposures` and
`candidates_per_exposure` are reported in both the dataset manifest and the M0 result, making the
inflation factor impossible to quote past.

Before this, BUY UP and BUY DOWN on the same instant could both occupy Q5.

## 3. Aligned economics (frozen pair)

```
M0_SCORE_LABEL    = label_take_3c_before_stop_3c     # P(+3c before -3c)
M0_REALIZED_COLUMN = plan_take_3c_or_stop_3c_net     # PnL of that same plan
```

Previously the ranking used `P(ever profitable)` while the realized column settled
`TAKE_3C_OR_STOP_3C` — a head could genuinely predict a transient +0.1c tick and have no skill at
the plan actually being traded. `label_ever_profitable` is retained as a diagnostic bucket column
only. The label was already trained (it is in `CLASSIFICATION_TARGETS`), so no new model was added.

`evaluate_m0` now refuses a dataset lacking `exposure_id` rather than degrading to the old
behaviour.

## 12. Settlement provenance — one correction to the audit

The audit proposed:

```sql
AND resolution_source LIKE 'official:%'
```

**That would have matched zero rows.** `data/pm_export_settlements.parquet` stores bare venue
values — `polymarket_clob` (1,479) and `polymarket_gamma` (819); the `official:` prefix is applied
downstream in `database.py:1082`. The prefix match would have produced a well-formed, entirely
empty dataset with a confident-looking manifest.

Implemented as the audit's stated alternative, a **frozen explicit allowlist** accepting both
forms, plus a hard failure if the gate ever empties the result:

```
RuntimeError: no settled rounds passed the load gate.
allowed resolution_source=[...]; present in pm_export_settlements.parquet=[...]
```

## 6. Quote survival

The old definition was `entry_quote_survived = entry_eligible` — "some entry existed after
latency", which is true even when the size vanished and the price moved two cents against us.

Survival now requires **both** conditions, measured against a size-aware decision price:

```
entry_complete                                        (full quantity fillable)
AND entry_vwap <= decision_ask_vwap + 0.01            (not materially worse)
```

`decision_ask_vwap` did not exist and had to be added — the builder only stored top-of-book
`own_ask`, against which quote survival is undefinable, since a quote can "survive" at the top
while the size behind it disappears. New heads: `entry_worse_by_1c`, `entry_worse_by_2c`, plus
`entry_vwap_slippage` for quantile fitting.

## 7. No silent neutral imputation

`p_hold → 0.5`, `return → 0`, `volatility → 0`, `opposite bid → 0` all made a data failure
indistinguishable from a genuine reading — and the model, trained on real values, treats the
fabricated one with full confidence.

Missing **required** input now returns `None`, which the caller surfaces as NO_DATA / NO_TRADE.
Missing **optional** input is reported in `_missing_optional` using the real feature-column names,
alongside `_quote_age_s`.

## 8. Durable evidence

Four `INSERT OR REPLACE` became `INSERT`; the tables already have primary keys, so a duplicate now
raises instead of overwriting. `except Exception: pass` at the call site is replaced by
`log_forecast_monitored()`: retries, counts, classifies duplicates, dead-letters the payload
(bounded at 200), tracks last successful write, and raises a latched alert. The ticker still cannot
be taken down by a logging failure — but the failure is no longer invisible.

## 10. Freeze-aware loading

New `backend/trade_forecast/freeze_guard.py` (13 checks). Under `BTC_FREEZE_MODEL=1` each of the
three loaders pins the artifact hash on first load and **refuses** a changed file, keeping the
pinned bundle rather than falling back to "no model" — otherwise anyone could invalidate a running
evidence collection by touching a file. Violations are **latched**: reverting the file does not
erase the fact that the run was disturbed. Exposed in each loader's `status()`.

## 13. Conservative capacity

Sizing gated on `capacity q50`, so by construction the book fails to absorb the size roughly half
the time — and the shortfall concentrates in exactly the stressed books where the exit matters.
Now gates on `q10` (falling back q20 → q25 → q50 only if the lower quantile was not modelled).
Absent capacity means **zero**, never unlimited. Cost was already correctly on `q80`.

## 14. One permission registry

`head_permissions.py` existed and was wired into the champion by the parallel session. Extended to
the complete-trade lane: `p_hold_side` is a `FEATURE_COLUMNS` input to every complete-trade head,
so a P(Hold) that may not rank now blocks a complete-trade action too. A head marked
`DISABLED_NO_SKILL` has to be inert everywhere or it is inert nowhere.

---

## What was deliberately NOT done

**4 (positive half) — direct per-plan models.** The scenario engine is now tagged
`diagnostic_only: True` / `promotable: False` with a machine-readable reason on every plan, so its
approximations cannot back a promotion. Building the replacement — direct models for expected plan
PnL, plan PnL quantiles, P(plan profit) and expected holding time — is a **new training cycle**,
and the instruction was to complete correctness PRs before adding predictive models. The realized
`plan_*_net` labels those models need are already produced by the builder.

**5 — two-stage liquidity model.** Future bid targets exist only when the full quantity can be
exited, so training silently conditions on liquidity being available. The fix is
`P(full exit possible at t)` × `exit VWAP | full exit`, with the no-exit branch priced into EV.
That is a retrain, not an edit.

**9 — signed serving bundle.** Artifact verification still requires the training dataset and source
files to be present and hash-matched, so a locally-trained artifact may not load on Oracle. Needs a
self-contained signed bundle (model + schemas + dataset hash + source-manifest hash + code hash +
dependency versions + signature) verified without the source data.

**11 — `BTC_STRICT_ARTIFACT_IDENTITY=1`.** Left at `0` in `start.bat`. This is correct for the
local transition and is an operator gate: the final Oracle evidence run must not start until every
required model has an honest manifest and `verify_artifact_identity` passes.

---

## Required operator action before any of this produces evidence

**The dataset must be rebuilt.** `load_verified_dataset` already refuses the existing one:

```
RuntimeError: dataset rejected: dataset construction code mismatch
```

That is the integrity check working — the builder changed, so every label derived from it is stale.
Until the rebuild, no M0 number from this lane means anything.

```bash
python backend/trade_forecast/build_complete_trade_dataset.py
```

Then retrain in order (share path last, since M0 lives there):

```bash
python backend/trade_forecast/train_btc_path_model.py
python backend/trade_forecast/train_execution_heads.py
python backend/trade_forecast/train_share_path_model.py
```

Expect the corrected M0 to look **worse** than the previous one, and expect that to be the honest
number: it will be computed on roughly 63× fewer independent units, ranked on the plan actually
being settled, without post-expiry information, and gated on q10 capacity.

Per the frozen discipline: run it **once**, and if it fails, close the lane.

---

## start.bat wiring (2026-07-26)

### Invariant selftest gate

Eight offline suites now run before the app launches; any failure **stops startup** rather than
letting the run accrue data that will later have to be discarded. Bypass with
`BTC_SKIP_SELFTESTS=1` (never for an evidence run).

```
a  backend.trade_forecast.test_audit_fixes    label / M0 / execution correctness   (60 checks)
b  trade_forecast/freeze_guard.py             no model swap mid-evidence-run       (13 checks)
c  head_permissions.py                        a head that cannot price may not price
d  preflight_longwindow.py                    long-window disk classification      (15 checks)
e  venues/multi_venue_recorder.py             collector schema + episode accounting
f  venues/venue_admissibility.py              backlog / lead-lag / identity gates
g  venues/test_collector_integrity.py         D1-D5 evidence integrity
h  research/audit_strategy_registry.py        strategy registry consistency
```

Each check `goto`s to its own failure label instead of accumulating into a flag. `start.bat` has no
`setlocal enabledelayedexpansion`, so a `%VAR%` set inside a parenthesised block is expanded at
**parse** time and would read its pre-block value — an accumulator there would silently never fire,
which is precisely the class of bug these selftests exist to catch. **Verified both ways:** all
eight pass in the real batch, and deliberately breaking `target_offset_valid` made startup exit 1.

### Long-window preflight (`backend/preflight_longwindow.py`)

The old gate asked "are >= 1000 raw aggTrade CSVs cached?" and otherwise demanded 300 GB. That is
the wrong question once the bulk download has already been processed into derived parquets and the
cache pruned. Three modes, each with its own floor:

| mode | condition | free-disk floor |
|---|---|---|
| `REBUILD` | derived parquets already span the window | 80 GB |
| `RESUME` | >= 1000 daily CSVs cached | 80 GB |
| `FIRST_BUILD` | neither | 300 GB |

The weakest source decides coverage, so one long source cannot vouch for a short one. 2% tolerance
absorbs real archive gaps. Live verdict on this machine:

```
window=1265d free=150GB cached_csv=2608
  OK  crossvenue_flow.parquet          1286d
  OK  trade_features_backfill.parquet  1288d
mode=REBUILD requires>=80GB - no bulk download needed
```

**Correction to an earlier claim in this session:** the preflight was *not* blocking the retrain.
An initial check counted `backfill_cache/` at the repo root (empty) rather than
`data/backfill_cache/` (2,608 files), so the old gate already classified this machine as `RESUME`
and passed. The 1265d run simply had never been executed. The new module is retained as
defence-in-depth: it keeps the rebuild possible if the cache is ever pruned, and states its reasoning.

### Multi-venue collector

Added to `start_recorders_once.ps1` (skip with `BTC_SKIP_VENUE_COLLECTOR=1`). Public read-only
market data; the process holds no credentials and cannot trade.

**A laptop that sleeps produces mostly NON-QUALIFYING episodes**, and the episode ledger records
that honestly rather than hiding it. Local collection is useful for mechanics and monitoring; the
qualifying >= 4-continuous-week run belongs on the always-on box per
[`COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md`](COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md).

### Training window

`BTC_HISTORICAL_DAYS=1265` — unchanged, and already exceeds the 1000 days requested. Left at 1265
rather than lowered to 1000 because 1265 is the preregistered `W1265` expert window in
`target_windows.py`; training that expert on 1000 days while still calling it `W1265` would be
exactly the mislabelling this codebase guards against elsewhere.

No completion marker exists for 1265d (only 360d and 400d), so `start.bat` forces one full retrain.
Verified with `BTC_VALIDATE_STARTUP=1`.

---

# Second review pass (2026-07-26)

## R1 — `exposure_id` was still not independent

Collapsing to one action per exposure removed the side x quantity duplication but left **4-10
checkpoints per round**, all sharing one settlement outcome and overlapping price paths, counted as
independent trades.

The independent unit is now **`round_id`**. The policy chooses, per round: checkpoint + side +
quantity + exit plan, or `NO_TRADE`. On the real candidate shape (3 rounds x 4 checkpoints x 2
sides x 3 quantities):

```
raw candidates       72
per-exposure (old)   12   <- still 4 correlated trades per round
per-round   (new)     3   <- one trade per settlement outcome
```

The portfolio alternative (multiple entries, exposure cap, same-round correlation, combined PnL)
is explicitly **not** what is being scored; that is a separate, larger design.

## R2 — settlement is TERMINAL, not right-censoring (**a bug the first pass introduced**)

The first pass set non-crossing late-checkpoint crossing labels to `NULL`, reasoning that the
missing seconds were unknown. That was wrong, and wrong in the dangerous direction.

A position cannot exist past settlement, so "did +3c occur within 120s?" is fully answered by the
contract's own lifetime. `NULL` **dropped definite failures while retaining early successes** —
textbook upward selection bias. Arithmetic from the regression test: one crossing and three
definite failures is a true rate of `0.25`; NULLing the failures reports `1.00`.

```
1     event occurred before terminal settlement
0     event did NOT occur before terminal settlement   (terminal, not unknown)
NULL  evidence genuinely missing/corrupt BEFORE the terminal boundary
```

Exact future **price** targets past expiry stay `NULL` — no executable price exists 120s out when
the contract settled at 30s. An **event over the position's life** is not undefined. Pinned by
`test_a2b_terminal_settlement_labels`.

## R3 — economics now exact, not merely closer

`P(+3c before -3c)` was better than `P(ever profitable)` but still not the plan's economics: the
barrier event ignores rounds where neither barrier is struck and settlement decides, target/stop
overshoot, the entry price paid, fees, and quantity-dependent impact.

```
score      plan_take_3c_or_stop_3c_profitable  = P(plan net PnL > 0)
realized   plan_take_3c_or_stop_3c_net         = that plan's net PnL
```

Generated for every plan in `EXIT_PLANS` and registered as trainable targets. Both barrier-event
labels remain as diagnostics. Direct plan-PnL magnitude heads (expected PnL, q10) are still the
stated next step.

## R4 — the old M0 was not unspent

Correct. Selection unit, target, label semantics, settlement filter, capacity rule and logging
contract all changed, and **the 395-round pilot was used to design those fixes** — so it is
development data regardless of whether the corrected scorer ran against it.

[`PREREG_COMPLETE_TRADE_M0_V2.md`](PREREG_COMPLETE_TRADE_M0_V2.md) is frozen and hashed
(`138616d3893c…`, in `PREREG_HASH.txt`). The pilot has **kill authority only**; the promotion test
requires forward data collected after the freeze.

## R5 — scenario output can no longer authorize

`trade_plan_optimizer` now refuses any action on a plan not tagged `promotable`, and every
scenario-engine plan is tagged `promotable: False`. Promoting on those numbers requires deleting
the tag — a visible, reviewable code change rather than an accident.

## R6 — challenger-only long-window training

`train_heads.py` honours `BTC_MODEL_OUTPUT_DIR`; `start.bat` sets it to
`saved_models_challenger_<N>d` for any run >= 1200d, leaving the incumbent untouched. Promotion is
a separate gated step, `backend/promote_challenger.py`, which refuses unless: the challenger has
artifacts, **every artifact carries a manifest**, the matrix passed its monthly data-quality gate,
the admitted window matches the claimed window, and no promoted head is `DISABLED_NO_SKILL`. The
incumbent is snapshotted before any copy.

## R7 — CI

`.github/workflows/invariants.yml` now runs the complete-trade, serving, collector, promotion,
documentation and preregistration suites plus `compileall` on every push and PR. It also performs
the frontend lockfile install, production build and high-severity dependency audit. The Windows
job parses `start.bat` and reruns the launch-critical invariant set.

---

# 1265-day training: the honest status

**The 1265d matrix does not exist.** The build ran to completion and was **rejected by its own
monthly data-quality gate**:

```
Official OHLC parity: passed=True  overlap=4320  median_diff=0.0
Joined source coverage: trade_features=100.00%, crossvenue=100.00%
ERROR: monthly data-quality gate failed. Failed months=['2023-03']
The previous research matrix is preserved.
```

`2023-03` is clean on every dimension except one: **`max_contiguous_gap_minutes = 152.0`**
(coverage 99.66%, zero nulls, zero invalid OHLC rows). The gate refused to stamp it and kept the
360d matrix — the correct outcome.

Consequences, stated plainly:

- **`W1265` cannot be claimed.** No admitted 1265-day matrix exists, so no head may be named for
  that window. `promote_challenger.py --days 1265` refuses today, and its selftest asserts that.
- The two lanes stay separate. A completed long-window matrix would improve the **historical
  specialist heads**. It creates **zero** additional Polymarket L2, executable ladders,
  quantity-specific capacity or complete-trade outcomes — Complete Trade remains bounded by the
  Polymarket evidence and must never look promotion-ready because a BTC matrix finished.

To proceed at 1265d, the 2023-03 gap must be repaired or the window shortened to a range that
passes the gate on its own terms.

---

# Current status

```
Engineering correctness   substantially improved; second-pass bug (R2) found and fixed
1265-day matrix           BUILD REJECTED by its own monthly gate (2023-03, 152-min gap)
W1265 expert              CANNOT BE CLAIMED - no admitted matrix
Complete Trade M0         NOT ready to spend; V2 frozen, forward data required
Profitability evidence    ABSENT
Real trading              DISABLED
```
