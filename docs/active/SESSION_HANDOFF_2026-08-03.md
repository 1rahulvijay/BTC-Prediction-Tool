# Session handoff — 2026-08-03

**Read this first if you are picking up this repository cold.** It records what was done, what
was measured, what was *not* measured, what remains, and the working rules that made the results
trustworthy. Written to be sufficient on its own.

Head at handoff: `a10a9be`. Local CI: **PASS**, 117 steps. Sealed protocols: **22/22 verify**.

> **Superseded in part, `2026-08-05`.** The CI figure above is stale — local CI is now **134
> checks / 160 OK**, with 2 known failures (the oracle release freeze and the Windows aggregate
> that depends on it). More importantly, the audit that followed this handoff found the
> **truth-measurement layer** unsound: grading, calibration, promotion and the per-model panel
> were each measuring something other than what they reported. Eight defects are fixed and
> documented in
> [`TRUTH_LAYER_REMEDIATION_2026-08-05.md`](TRUTH_LAYER_REMEDIATION_2026-08-05.md) — **read that
> before trusting any accuracy, calibration or A/B number recorded before this date.**
>
> The 0.6715 crossing result below still stands; it was re-verified through the label rename and
> reproduces exactly.

---

## 1. The one-paragraph summary

The prediction side finally produced one real result — anchor-crossing **reversion** is
predictable at AUC 0.6715 against a clock baseline of 0.5196 — and the cost side closed every
route to monetising it. Direction is weakly predictable (AUC ~0.51, worth ~1 bps gross) against
costs of 14 bps taker or 2 bps maker. Passive execution loses on the best data available.
**The binding constraint is cost, not prediction, and it has been measured from four independent
directions.**

---

## 2. Working rules that produced these results

These are not ceremony. Each was added because its absence produced a wrong answer here.

1. **Freeze and hash the protocol before writing the code.** 22 protocols in
   `docs/active/PREREG_HASH.txt`, verified in CI by `backend/research/verify_prereg_hashes.py`.
   Editing a frozen protocol invalidates every result scored under it.
2. **The baseline is the incumbent, never zero.** A regime taxonomy looked separable until it
   was compared against current volatility, which explained **84%** of it. Every head since is
   scored against the thing that already exists.
3. **Declare materiality before seeing the number.** Significance on 100k rows is cheap. A
   +1.11 bps effect with a CI excluding zero is still worthless against a 14 bps cost.
4. **Day-block bootstrap, never per-row.** Rows within a day share regime and volatility.
5. **Negative-test every guard.** A check that has never rejected a planted offender is not a
   check. This caught a vacuous gate, a leaked join, and a fill model that could not fail.
6. **"Not yet known" must never read as "known false".** NULL, not zero. Separate tables when
   facts are known at different times.
7. **Scored once.** A second look needs a new hash.

---

## 3. What was tested, and what it said

Every row below is a sealed protocol, scored once, committed with its result document.

| # | test | verdict | the number that matters |
|---|---|---|---|
| 1 | Regime labeler | `REGIME_SEPARABLE` | THIN < RANGE < TRENDING, stable across split |
| 2 | **Regime volatility control** | `REGIME_ADDS_WEAKLY` | **84% of it was volatility**; +1.11 bps survives |
| 3 | Tradability head | `ADDS` (Binance) | +2.67 pts over `rv_60m`, CI [+1.79, +4.08] |
| 3b | Tradability head | `IS_VOLATILITY` (Polymarket) | **0.1%** of 15m windows clear 149 bps |
| 4 | Conditional direction | `NOT_PREDICTABLE` | AUC 0.498 gated; **worse than a coin** |
| 5 | Exit timing | `ADDS_NOTHING` | ceiling +52.51 bps, captured **−11.5%** |
| 6 | Direction ensemble (7 families) | `NO_BETTER_THAN_SINGLE` | real signal, worth **0.28 bps** |
| 7 | Multi-horizon (7 pairs, 2 exchanges) | `NO_TRADABLE_EDGE` | best gross **+1.97 bps** at 240m |
| 8 | Binance maker (top-of-book) | `LOST_TO_ADVERSE_SELECTION` | adverse **1.53 bps**, spread 0.02 |
| 9 | Altcoin maker (4 symbols) | all not viable | **r = +0.916** spread↔adverse selection |
| 10 | **Crossing heads** | **`ADDS` on all three** | **reversion 30s: 0.5196 → 0.6715** |
| 11 | Bybit 200-level L2 maker | `SAVES_BUT_NOT_ENOUGH` | passive fill worth **+0.08 bps**, fee 1.0 |

### The one positive result, stated precisely

```
target              base    clock    candidate   gain      95% CI            null floor
is_final_crossing   37.1%   0.6755   0.7144     +0.0389   [+0.0243,+0.0556]  <=0.6102
reverted_30s        18.0%   0.5196   0.6715     +0.1519   [+0.1175,+0.1840]  <=0.5912
reverted_60s        29.6%   0.5061   0.6373     +0.1312   [+0.1121,+0.1517]  <=0.5664
```

On reversion **the clock is worth nothing** (0.52, 0.51 — at chance) and market state is worth a
great deal. It is not a mechanical artifact.

**Why it worked where direction failed: the target, not the model.** The same features
contributed nothing to direction. Unconditional direction asks the market something already
priced; whether a *specific observed event* reverts is a conditional path question on a
population selected by something that just happened.

### The cost wall, from four directions

```
Polymarket taker    ~149 bps floor; 0.1% of 15m windows move that far
Binance taker         14 bps against a 0.97-1.97 bps measured gross edge
Binance maker          2 bps round trip (fees alone) > 1.97 bps best gross edge
passive fill value    -1.33 bps (Binance, adverse) / +0.08 bps (Bybit, optimistic)
```

---

## 4. What was NOT tested — and why that matters

Do not assume these are open questions with no prior. Each is unmeasured for a stated reason.

| not tested | why | prior |
|---|---|---|
| Crossing heads at **5s / 15s** | 15s snapshot cadence made labels unresolvable (6 cases at 15s, 0 at 5s) | Strong — 30s/60s both ADD |
| **Calibration** of the crossing heads | AUC is discrimination; EV use needs calibration | Unknown |
| Whether a crossing head **improves a specific action's post-cost value** | needs an open action lane; none exists | This is *the* question |
| Protocols **B / C / D** | sealed, gates unmet, unscorable | — |
| Trade-driven fill model on Bybit L2 | needs tick trades joined | Would correct an optimistic bias |
| Bybit L2 beyond one day / one symbol | 1,293 days × many symbols available | — |
| Frontend | never examined | — |

### Two results that are weaker than they look

- **Bybit adverse selection (−0.072 bps)** is optimistic. The fill rule counts *queue depletion*,
  and levels shrink on **cancellation** as well as trade. The **99.5% fill rate is the tell** —
  real passive orders do not fill 99.5% of the time at 60s. The Binance +1.526 figure is more
  trustworthy.
- **Crossing-head artifact numbers are in-sample.** The deployed artifact is trained on all 21
  days; the scored result held out 7. They are different objects and the in-sample numbers are
  quoted as evidence nowhere.

---

## 5. Defects found and fixed

| defect | consequence had it stood |
|---|---|
| **P0-02** round-state trainer joined the bar *containing* each decision | every training row saw ~44s of its own future |
| **P0-01** trainer stamped `<schema>-1000d`, loader required `<schema>` | a successful retrain produced artifacts serving always rejected |
| **P0-06** C gate filtered `settlement_floor_net IS NULL` on a `NOT NULL` column | 1,000 rounds could complete the gate with **zero** realized outcomes |
| Protocol B hardcoded zeros | "not built" was indistinguishable from "measured zero" |
| `COLLECTING` unreachable | every numeric state returned before it |
| Blindness check top-level only | and its matcher rejected `ledger` for containing *"edge"* |
| Datastore: default store ends **07-04**, live archive runs **07-05→07-25** | the metrics doc was missing **97.6%** of paper trades |
| `coalesce(pair_skew_ms, 0)` | unmeasured skew scored as perfect |
| Bybit markouts never accrued | every net was **exactly −1.000** = the fee |

**The recurring defect class:** a check that passes while the property it guarantees is false.
Six instances. The tell is always a number that is *too clean* — exactly 0, exactly the fee,
exactly the base rate.

---

## 6. Yet to implement

**Ordered by what unblocks the most.**

1. **Retrain via `train_heads.py`** — 0/25 artifacts serviceable. Blocks the calibrator →
   positions → Protocols B and C. The main ensemble is *incompatible*
   (`2026-06-15-v11` vs `2026-07-31-v14`), so a manifest fix alone will not load it.
   Round-state schema was bumped to `v2`, so v1 artifacts are correctly refused.
2. **Run the recorders.** Round recorders dark since **2026-07-25**.
   `binance_l2_recorder` is wired, selftests pass, and has **NEVER RUN** (0 rows).
   `crossing_recorder_hf` needs ~3 weeks for a comparable sample.
3. **Decide the canonical datastore.** `datastore_identity.py --strict` refuses to guess; the
   decision is unmade. Two stores, disjoint spans.
4. **Protocol B crossing recorder → positions.** `post_entry_crossing_outcomes` has a writer but
   the table does not exist in the live DB and there are 0 position snapshots.
5. `BTC_ALLOWED_ORIGINS`, dedicated virtualenv — 2 of 17 production-readiness failures, trivial.
6. Websocket feed for `crossing_recorder_hf` (1s polling can miss sub-second round trips).

**Not wired, deliberately:** `crossing_heads` is registered with **every authority flag False**,
published, loadable, and called from nothing. Granting authority is a visible one-line edit to
`backend/model_registry.py`.

---

## 7. What to run with the Bybit L2 data

The archive is the most valuable asset uncovered this session:
`quote-saver.bycsi.com/orderbook/linear/{SYMBOL}/` — **1,293 daily files, 2023-01-18 →
2026-08-02, 200 levels, update IDs, ~106 MB/day.** Note `public.bybit.com` has **no** orderbook
path; the obvious place to look does not have it.

Ordered by value, each needing its own frozen protocol:

1. **Trade-driven fill model** *(highest value — corrects a known bias)*
   Join `public.bybit.com/trading/{SYMBOL}/` ticks; require depletion to be **trade-driven**,
   not cancellation. Fixes the 99.5% fill rate and gives a trustworthy adverse-selection number
   on real depth. Directly comparable to Binance's +1.526.

2. **`LIQUIDITY_VACUUM_HEAD`** — P(depth disappears in 1/5/15s), P(spread widens), expected move
   after depletion. Needs full depth; now available. Tells the app **when not to quote**.

3. **`BURST_HAZARD_HEAD`** — P($10/$25/$50 move in 5/15/30s). Same family as the crossing heads
   (conditional path, not direction), which is the only family that has worked.

4. **`BOOK_RESILIENCE_HEAD`** — replenishment time after depletion, P(reverse | replenished).

5. **Queue-position study** — with 200 levels, measure how queue position at posting predicts
   fill probability and markout. Answers whether *posting deeper* changes the arithmetic.

6. **Multi-day / multi-symbol maker replay** — one day and one symbol is the current limit; the
   archive removes it. Day-clustered inference becomes possible.

7. **Cross-venue L2 dislocation** — Bybit L2 against the Binance data already fetched.

**Order-book invariants are already implemented** in `research/bybit_l2_maker_v1.py` (`Book`,
`ReplayInvalid`): crossed book raises, backwards update ID raises, `size "0"` removes, later
snapshot resets. Reuse it rather than rewriting — and keep the rule that a violated invariant
produces **no number**.

---

## 8. Discussed but deliberately not built

| proposal | disposition |
|---|---|
| Strategy Router over regimes | **Not built** — regime separability was 84% volatility |
| 17 algodesk agents as a live strategy | Tested; all lost. FUND/CONTRA/SENT **cannot fire** — real funding never exceeded 0.000256 vs a 0.0015 threshold |
| More model families on direction | Closed by kill rule — 7 families converge on 0.28 bps |
| Per-second up/down labels | Shorter horizon = smaller move against a fixed fee. Strictly worse |
| FGI / CoinMarketCap slow regime | Reasonable as *context only*; FGI already contains BTC volatility, so it is partly transformed price. Exclude interpolated dominance columns |
| Market-prior residual | Already tested as Protocol A: **retired**, `NO_DETECTABLE_INCREMENTAL_RESOLUTION` |
| SCALP / market making | Blocked on sequenced L2 — now partly unblocked by the Bybit archive |

---

## 9. Traps specific to this repository

- **`recv_ts` in `multi_venue.duckdb` is in SECONDS.** Converting as ms sent 56,467 rows to 1970.
- **Two `analytics.duckdb` stores with disjoint spans.** Default ends 07-04; live runs 07-05→07-25.
- **`data/` in `.gitignore` is a bare pattern** — it matches *any* directory named `data` at any
  depth.
- **`.git` is 6.5 GB**, `size-pack: 0` — never garbage collected.
- **~1,971 leaked temp directories** from `ignore_cleanup_errors=True` in binance_paper selftests.
- **GitHub Actions has never run.** `run_ci_locally.py` is the only real gate.
- A **parallel session** edits this repo. Reconcile; never clobber.
- **ASCII-only console output** — Windows cp1252 chokes on `→ ≥ δ`.

---

## 10. If you are a fresh session, start here

```
1. python backend/tests/run_ci_locally.py            # must be green before anything
2. python backend/research/verify_prereg_hashes.py   # 22/22 or stop
3. python backend/tests/test_artifact_serviceability.py    # currently 0/25
4. python backend/audit/recorder_evidence_check.py   # wired vs ever-ran
5. python backend/datastore_identity.py --scan       # which store you are about to read
```

Then read, in order: `docs/RESEARCH_LEDGER.md` §11–12, `CROSSING_HEADS_RESULT_2026-08-03.md`,
`BYBIT_L2_MAKER_RESULT_2026-08-03.md`.

**The single most useful thing you can do is not another model.** It is a retrain that produces
serviceable artifacts, and recorders that actually run — because every sealed protocol in this
repository is waiting on forward evidence, and none of it is being collected.

**Capital position: unchanged.** Real orders disabled, paper/shadow only, 0 promotable
strategies, no lane open on cost.
