# Implementation Status — what is built, what is not, and why

> **SUPERSEDED FOR CURRENT AGGREGATE STATUS.** This is the point-in-time audit associated with
> commit `0085498`, primarily documenting the research-runner coverage defect and the evidence
> known before the current documentation-contract gate was added. Preserve its research results,
> but use `active/CURRENT_IMPLEMENTATION_TEST_AND_GAP_LEDGER_2026-07-31.md` for current model,
> feature, runtime, test and readiness status. In particular, its `70/70` count predates the new
> documentation-contract step.

Generated `2026-07-31`. Every number here was produced by running the thing described, not by
reading a previous document. Commands are given so any line can be re-checked.

---

## 1. The wiring question: nothing from the research goes into the app

**Recommendation: wire none of it.** This is not caution — there is no candidate.

```bash
python research/run_all_sequence.py
```

| | |
|---|---:|
| research scripts executed | **39** |
| exited non-zero | **0** |
| reporting a real out-of-sample number | 16 |
| **positive out-of-sample** | **0** |

Plus five frontier studies, none of which produced a tradeable structure:

| study | result |
|---|---|
| path information | direction dead at settlement *and* along the path; magnitude real |
| breakout bracket | all 9 configurations lose; the control loses equally — structural |
| complete-set arbitrage | ~$200 per 2.17 days at ten-share size |
| funding carry | 12-day breakeven; unanswerable from 0.95 days of constant-rate data |
| cross-market coherence | no violation at measurable resolution |
| options surface | 0 of 2,079 no-arb violations; 15m magnitude killed by an upper bound |

Wiring a signal with no positive out-of-sample result would convert a research negative into a
live loss. The gates in `backend/trading_authority.py` exist precisely to make that require a
deliberate act, and none of this clears them.

**The one lane still open is `IV vs realized`, and it is open because of missing data, not
because of a promising result.** It cannot be wired; it can only be measured, and that needs the
Deribit recorder running for weeks.

Real orders remain disabled. Trading mode: PAPER / SHADOW ONLY.

---

## 2. What is implemented and verified

```bash
python backend/run_ci_locally.py
```

**70 of 70 gating steps pass** (389 s). Highlights, each an executable check rather than a claim:

| area | module | enforced by |
|---|---|---|
| content-addressed artifacts, verify-before-deserialize | `backend/model_artifacts.py` | steps 1–8 |
| TOCTOU-safe atomic publish (losing a race is not tampering) | `backend/model_artifacts.py` | step 6 |
| fail-closed control plane, admin token never logged | `backend/control_auth.py` | steps 39–40 |
| order lifecycle `UNKNOWN` state, double-fill guard | `backend/order_lifecycle.py` | step 50 |
| reduce-only authority separated from new-exposure gates | `backend/trading_authority.py` | step 41 |
| supervised task lifecycle | `backend/task_supervisor.py` | step 44 |
| feed writer drain / lifecycle / observability | `backend/feed_writer.py` | steps 38–39 |
| causal HMM forward filtering, fold-local fitting | `backend/regime.py` | step 43 |
| Kelly on empirical log-growth, day-block bootstrap | `backend/trading_simulator.py` | step 51 |
| launcher integrity (stray control bytes, unmatched gotos) | `backend/test_launcher_integrity.py` | step 69 |
| research-claim audit (4 disqualifying patterns) | `backend/research/audit_research_claims.py` | step 64 |
| docs match executable contracts | — | steps 59, 66, 67 |
| preregistration hashes unchanged | — | step 68 |

Not run locally, by design: dependency install, and the frontend lockfile/build/audit
(`--all` re-enables the second).

---

## 3. Defect found and fixed while answering this question

**The research runner did not run every research script.** Its docstring says "Run every
research script in sequence." Discovery was `research/v*.py` plus three hardcoded names, so the
five frontier studies — which produced every finding of the past week — were **silently
excluded**. The selftest could not detect it because it asserted only that the names it already
knew about existed.

That is the same defect class as the earlier extraction test that found 0 invocations and printed
`ALL PASS`: a check that can only confirm what it was told is vacuous.

Fixed by enumerating the directory and failing on anything unaccounted for. It immediately caught
a sixth uncovered file, `download_binance_l2_data.py`, now explicitly marked non-study — it
fetches ~120 days from `data.binance.vision` and must stay operator-invoked rather than run on
every suite invocation. Negative-tested: dropping any new `.py` into `research/` fails the
selftest with the filename.

```bash
python research/run_all_sequence.py --selftest
```

---

## 4. Not implemented — why, and how

### 4.1 Blocked on data collection (the dominant category)

| item | why not | how |
|---|---|---|
| `BINANCE_SEQUENCED_L2_RECORDER_V1` | `orderbook.1` stores 485,105 **top-of-book** rows — a price, not a place in a queue. Fill probability, queue delay and partial fills are underivable from it | record depth diffs with sequence numbers; replay into a book where a simulated resting order holds position; adverse selection = drift **after** a fill |
| Deribit surface time series | recorder built and provenance-clean (3/3 HTTP 200, 0 dropped, `response_sha256` stored) but **never scheduled** — 3 hand-run batches spanning 6.4 minutes | put it on the same supervised schedule as the other collectors |
| options → Polymarket lead-lag | archives do not overlap by **27 days** (PM ends 2026-07-04 09:23 UTC, chain begins 2026-07-31 08:01 UTC) | run both recorders concurrently; needs no new code |
| skew / term-structure transitions | 6.4 minutes of surface history cannot contain one transition | as above, weeks of data |
| funding carry | rate constant at 0.000100 across the whole 0.95-day sample — no dislocation to measure | collect across a funding regime change |
| Polymarket settlement join | residuals need **actual ask, depth and executable VWAP**; mid-price residuals would reproduce the taker-cost error | `POLYMARKET_SETTLEMENT_JOIN_V1` — record the reference price alongside each market |

**All six are blocked on collection, not on ideas — and every collector is currently down**
(last write `2026-07-29 19:18` UTC, all 8 streams stopping in the same second). That is the
single highest-value fix in this document, and the missing liveness alarm is the real defect.

### 4.2 Blocked on a prerequisite

| item | blocker |
|---|---|
| `STRICT_ARTIFACT_IDENTITY` default-on | **14 raw load calls** still deserialize before any hash check (`python backend/artifact_migration_status.py`). The flag cannot honestly default to 1 until that reaches 0. Heaviest: `polymarket_repricing_shadow_v1/live_shadow.py` (3), `event_execution_v1/run_campaign.py` (2), `train_360d_multitarget_forecaster.py` (2) |
| remaining artifact migration | 39 raw saves / 14 raw loads across 45 files; must be re-saved **with manifests** before strict mode can be enabled |
| independent CI | GitHub Actions has never executed a step — billing. `backend/run_ci_locally.py` is the only real gate, and it parses `invariants.yml` rather than duplicating commands so the two cannot drift |

### 4.3 Deliberately not done

| item | reason |
|---|---|
| live venue adapters (backlog 13–17) | require funded credentials. Standing constraint: **no funded credentials in testing** |
| any research signal wired into the app | 0 of 39 scripts produced a positive out-of-sample result |
| `model = candidate` at `server.py:2415`/`2463` | verified present and **left untouched** — a parallel session owns that file |

---

## 5. Results documentation map

| document | covers |
|---|---|
| `docs/RESEARCH_RESULTS_MASTER.md` | v1–v31, what each established, what is refused and why |
| `docs/PATH_INFORMATION_RESULTS.md` | path information, breakout bracket |
| `docs/OPTIONS_SURFACE_RESULTS.md` | options surface, no-arbitrage, IV vs realized, 3 blocked lanes |
| `docs/IMPLEMENTATION_STATUS.md` | this file |
| `research/sequence_results.json` | per-script sha256, exit code, runtime, IS/OOS — regenerated each run |

---

## 6. Corrections carried in, so they are not repeated

Errors caught by measurement rather than review, each now guarded in code:

- **`start.bat` could not launch** — two lines held a literal TAB byte where `\t` was intended.
  Guarded by `backend/test_launcher_integrity.py`.
- **Vacuous pass** — extraction found 0 invocations and printed `ALL PASS`. Guarded by
  `assert len(cmds) >= 20`, and now by the runner coverage check above.
- **Unbounded accounting** — `capital += 1000.0 * bps` on fixed notional produced −212%, −834%,
  −10032%. Fraction-of-capital sizing floored at zero turned those into −20.79%, −39.13%, −52.70%.
- **Fake 16.42% coherence violation** — a 60-second bar-reference error of mine, the same size as
  the 3 bps barrier gap being measured. Traced by stratification; the true rate at unambiguous
  gaps is 0.0%.
- **Round trip double-counted to 40 bps** — buying at ask and selling at bid *is* the round trip:
  `ask − bid = 20 bps`, charged once.
- **"Buy favoured" on 11 of 11 expiries**, rising monotonically to +1230 bps — overlapping
  windows gave a 329-day move ~one independent observation, and a day-block bootstrap over entry
  dates cannot fix windows that all overlap. Non-overlapping windows plus a 30-window floor cut
  11 evaluable tenors to 4.

---

## 7. Standing constraints

- Real orders: **DISABLED**. Trading mode: **PAPER / SHADOW ONLY**. No real-money routing.
- No funded credentials in testing. Secrets out of Git; admin token in the deployment
  environment, `chmod 600`, absent from application logs.
- One DuckDB writer. Never run heavy research against a live writer DB — snapshot first.
- Thresholds are declared before results are seen. A near-miss is a miss.
- Nothing is promoted without: preregistered protocol, fold-local fitting, untouched
  chronological evaluation, day-block lower confidence bounds, a complete cost model, forward
  shadow evidence, and no automatic promotion.
