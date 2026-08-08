# OPEN DEFECTS AND FUTURE FIXES

Status of every known defect. Updated 2026-08-08. HEAD at time of writing: `34ee2d6`.

**2026-08-08 sweep.** Three entries below were STALE — P0-4, P0-10 and the P0-14
blocker were all fixed while this file still listed them open. A defect register that is
wrong in that direction is its own defect: it aims work at solved problems and implies
coverage that does not exist. Corrected inline. Full account:
`SCAN5_CLOSEOUT_AND_OPEN_DEFECTS_2026-08-08.md`.

**How to read this.** `FIXED` means a specific measurement or mutation run backs it, and a test
is registered in CI. `VERIFIED OPEN` means the code was read and the defect is real. `CLAIMED`
means an audit asserted it and nobody has checked.

That last distinction matters. Of the eight items checked so far, all eight were real — but
**two were real for a different reason than stated**, and one (P0-1/P0-2) was a single bug an
audit had split into two. An unchecked item is not "probably fine", and it is also not
"probably as described".

---

## THE ONE THAT BLOCKS A SETTLEMENT RETRAIN

`build_sequences` compares the horizon end to the DECISION-time price. The venue compares the
round end to the round ANCHOR. Measured over 40k simulated rounds, disagreement with the
venue's own outcome:

| time left | 15m | 12m | 9m | 6m | 3m |
|---|---|---|---|---|---|
| label inverted | 0.0% | 14.8% | 21.5% | 28.2% | **35.3%** |

Worst exactly where late-round information is worth most. No retraining or recalibration
repairs a backwards label.

- **FIXED** — the label logic: `backend/polymarket/round_truth.py` (21 checks, 7/0 mutation).
- **OPEN** — the capture/backfill path. Nothing writes those rows yet.

### Backfill is probably possible (correction, 2026-08-06)

An earlier claim in this work was that anchors cannot be backfilled, so usable history starts
the day a recorder runs. **That is probably wrong**, and it was costly to get wrong because it
made live capture look schedule-critical.

Both halves plausibly exist as external history:

- Polymarket's public API exposes RESOLVED markets — condition id, official outcome, token ids,
  round open/close timestamps.
- Chainlink feeds are on-chain; historical rounds (roundId, answer, updatedAt) are queryable
  from an archive node or a data provider.

If both hold, `RoundSettlementTruth` rows can be reconstructed for past rounds and usable
history starts months ago.

**VERIFIED 2026-08-06.** `gamma-api.polymarket.com/markets?closed=true` returns resolved
markets carrying `conditionId`, `startDate`, `endDate`, `closed`, `umaResolutionStatus`,
`outcomes` and `outcomePrices` — official outcome and round boundaries are recoverable for
PAST rounds. **CORRECTION on the Chainlink half.** An earlier note here said `getRoundData` against an
archive node. That is the classic push-based Aggregator product. BTC Up/Down resolves from the
Chainlink **Data Streams** feed (`data.chain.link/streams/btc-usd`) — pull-based, with
authenticated historical REST (`getReportByTimestamp`, `getReportsPage`, `getReportsBulk`).
Free unrestricted access is NOT established; credentials are required.

    Polymarket resolved metadata         CONFIRMED via /events?slug=
    BTC 15m round discovery              SOLVED (see below)
    via archive-node getRoundData        WRONG PRODUCT
    via Data Streams REST                REACHABLE, AUTH MANDATORY, NO anonymous access

`https://api.dataengine.chain.link/api/v1/reports?feedID=...&timestamp=...` answered HTTP 400
to an unauthenticated request - the service exists and responded, but every route requires
three headers (`Authorization` UUID, `X-Authorization-Timestamp` within 5s, and an
`X-Authorization-Signature-SHA256` HMAC). There is no anonymous tier; `401 Unauthorized User`
is returned for both bad auth and missing stream permission. **Backfill is gated on obtaining
Data Streams credentials** - a commercial/access question before an engineering one. If access
proves unavailable, live capture becomes the only route and history does start when the
recorder starts, for a different reason than originally claimed.

**Discovery query — SOLVED and verified 2026-08-06.** Use `/events?slug=<slug>`, not
`/markets?slug=`: the identifier is an EVENT slug and the tradeable market is nested inside.
`/markets?slug=btc-updown-15m-1778437800` returns EMPTY. `slug_contains` is NOT a supported
filter — Gamma silently ignores it and returns unrelated markets, which is the worst failure
mode for discovery because it answers confidently with the wrong rounds.

Verified shape (`btc-updown-15m-1778437800`, "Bitcoin Up or Down - May 10, 2:30PM-2:45PM ET"):
`conditionId`, `clobTokenIds`, `umaResolutionStatus="resolved"`, `outcomes=["Up","Down"]`
(NOT Yes/No), `outcomePrices=["0","1"]` → the side priced 1 won.

**Do not use Gamma `startDate` as the round anchor — MEASURED.** For recurring 5m/15m markets the interval
start is encoded in the slug (`btc-updown-15m-1778437800`); `startDate` is when the market was
LISTED and can be a day earlier. Three distinct timestamps are needed: `market_created_at`,
`round_start_ts` (from the slug), `round_end_ts` (= start + duration).
For that market `startDate` was **23.8 hours before** the interval it would have anchored.
`round_start_from_slug()` derives the anchor from the slug; `round_bounds_from_event()`
cross-checks the implied close against `event.endDate` and RAISES on disagreement rather than
absorbing a wrong duration into every label. `official_outcome_from_prices()` refuses zero
winners, two winners, or unexpected labels. 4/0 mutation.

**The boundary-report selection rule is an empirical contract question**, not a preference.
Data Streams reports carry `validFromTimestamp` and `observationsTimestamp`; a report 5s after
a boundary may contain observations unavailable at it. The current
`abs(source_ts - boundary) <= 5000` tolerance is too naive to be the final rule. Candidate
policies are listed in `BOUNDARY_POLICIES` and must be tested against the venue's displayed
Price to Beat and resolved outcomes, then frozen.

**Acceptance gate before training on backfilled rows:** reconstruct 500–1,000 resolved markets
and require **>= 99.9%** derived-vs-official agreement, reported by month and by policy. Any
systematic disagreement means the boundary or source policy is still wrong — do not absorb it
as label noise. `round_truth.py` is already source-agnostic: it does not
care whether values arrive live or from an archive, and the reconciler quarantines anything
that does not tie out against the official outcome.

---

## P0 — ranked by what they actually cost

### Fixed, with a test in CI

| # | Defect | Evidence |
|---|---|---|
| 1+2 | A denied forward gate ACTIVATED the candidate and trained into the live serving directory | one bug, two exits; 4/0 mutation |
| 3 | Binary contract fell through to first-touch grading | 3/0; adding it to KNOWN_CONTRACTS turned a safe refusal into a wrong grade |
| 5 (half) | Contract claimed a Chainlink source it never read | renamed `ROLLING_EXCHANGE_RETURN_SIGN_V1`; market-aligned rows still open |
| 6 | Settlement calibration used integer K-fold over overlapping sequences | purged chronological; refuses rather than substituting |
| 7 | `prior_brier` scored the train prior on TRAIN rows | now scored on the holdout rows |
| 21 | Preflight lived only in the .bat; a direct uvicorn start bypassed it | runs in lifespan, before init_db, raises |
| 22 | An ADVANCING recorder blocked readiness that required HEALTHY | typed health; unknown state = unhealthy |
| 23 | `ping_interval=None` at 4 sites — a dead socket looked like a quiet market | 20s/20s keepalive |
| 25 | `<target>.tmp.<pid>` collided between writers in one process | UUID-suffixed |

### VERIFIED OPEN — highest priority

**P0-8A — FIXED 2026-08-06** — wrong target evaluator.
`backend/backtester.py` contained **zero** references to `target_contract`, and walk-forward
fits a `RandomForestClassifier` surrogate — no stacker, no HMM routing, no policy. A backtest
result therefore describes a different model answering a different question. A good number
would be edge you do not have; a bad one might discard a model that was fine. Neither
announces itself.
*Fixed:* `run()` now takes `target_contract` (defaulting to the training contract) and grades
through `target_contract.label()`. Grading a PATH contract on FABRICATED bars raises — the
fallback invents a 0.2% range, and inventing barriers is as wrong as ignoring them. Proven by
requiring the two contracts to give DIFFERENT confusion matrices on the same data; a
refusal-only test let a "grade by endpoint sign regardless of contract" mutant survive.
`run()` had always accepted highs/lows — the path was passed and ignored.
**P0-8B — OPEN — wrong model under evaluation.** A contract-correct RandomForest backtest is
still a backtest of a RandomForest, not of the seven seats + OOF stacker + regime routing +
bundle-bound HMM + calibrators + conformal objects + decision policy. Needs a `BacktestSpec`
declaring `model_kind` (PRODUCTION_BUNDLE_REPLAY / PRODUCTION_PIPELINE_WALK_FORWARD /
SURROGATE_RESEARCH_ONLY), with surrogate results printing
`THIS DOES NOT EVALUATE THE SERVED ENSEMBLE`.

**P0-8C — OPEN — execution-policy parity.** Neither path replays the decision policy.

**P0-14 — PARTIALLY FIXED 2026-08-06 — two calibration systems disagree.**
`PrecisionEngine` defines correct as `sign(actual_move)` regardless of the prediction's
declared contract, so first-touch confidence is calibrated by endpoint sign. Old calibrators
are not cleared on bundle change, refresh can wait six hours, and zero rows for a new release
leaves the old map active.
*Fixed:* `bind_release()` clears every map on release change, resets the fit timestamp and
reports unavailable until refitted. `is_admissible_for()` refuses while provenance is
UNRECORDED — "we do not know" must not read as "yes".

**The stated blocker is GONE (corrected 2026-08-08).** This entry said `predictions_{h}m`
has no `target_contract` column. It has one, along with `release_id`, `resolution_basis`,
`resolution_event_ts` and `resolution_price`, and `log_prediction` REQUIRES the first two
explicitly — a new row cannot inherit the `UNKNOWN_LEGACY` default, which exists only
for rows written before the column did. A contract filter is now writable.

What remains is the consumer work: `PrecisionEngine` still declares
`contract_provenance=UNRECORDED` and refuses for contract-sensitive consumers rather than
silently answering. That refusal is correct until it is taught to read the new column.

**P0-27 — unauthenticated reads.**
Read routes expose database paths, positions, orders, fills, equity, model and evidence state.
Acceptable on localhost; unsafe the moment anything is reachable beyond it.

**P0-16 / P0-18 — mixed and partial releases.**
Promotion copies manifest files but does not remove stale ones, and the loader scans the
directory — so a release can be new ensemble + old HMM + older calibrator. Partial loads still
mark a bundle trained.
*Fix:* immutable `releases/<id>/`, atomic pointer swap, load only manifest-declared components,
require a complete component matrix.

**P0-4 — FIXED. The history below is kept because the two failed attempts are the
reason the third one is shaped the way it is.**

`as_of_close()` selects bars by `open_ms <= at_ms` and returns the OPEN time as the resolution
event. A one-minute bar that OPENED at the boundary has not closed until 60s later, so its
close price is FUTURE data relative to the graded moment — admitted while looking like an
exact-boundary observation, and stamped with a timestamp that was never the resolution moment.

A fix (require `open_ms + interval <= at_ms`, infer the interval from the series, refuse when
closure is unprovable) worked correctly in isolation:

    boundary at bar-3 OPEN        -> bar 2's close  (was: bar 3's, future data)
    1ms before bar-3 closes       -> still bar 2
    boundary at bar-3 CLOSE       -> bar 3
    single bar, closure unprovable-> refuses

but it broke `target_contract --selftest` and `test_target_contract_parity`, whose fixtures set
`verify_ts` to the final bar's OPEN — behaviour that only passes under the old rule. Either
those fixtures encode the incorrect semantics or the change has a real regression, and that was
not resolvable with the context remaining, so it was REVERTED rather than shipped or papered
over by editing the tests to match.

**RECONCILED 2026-08-06. The fixtures are not the problem, and P0-4 is not fixable inside
`as_of_close()`.** Two attempts, both reverted, and each taught something:

*Attempt 1 — change SELECTION to require `open_ms + interval <= at_ms`.* Broke the P0-11
fixture, and that fixture is RIGHT: it sets `verify_at` equal to the horizon bar's OPEN and
expects that bar to settle the round. Callers use `at_ms` to NAME the horizon-end bar, not to
mark a wall-clock instant. Under that convention settlement legitimately occurs at the named
bar's close and no future data is admitted. Changing selection would silently redefine every
horizon by one bar. **The audit's framing of P0-4 as "future data admitted" is wrong under the
convention actually in use.**

*Attempt 2 — keep selection, correct only the returned event timestamp from the bar's OPEN to
its CLOSE.* Unambiguously right in principle: a bar's close does not occur at its open, so
every consumer recording `resolution_event_ts` has been stamping the observation one interval
early. But it requires the bar interval, and inferring it from the kline list is unsafe: the
P0-11 fixture's bars are irregular (+60s/+300s/+540s), so `min(diffs)` yields 240s, which is
not the real cadence. On any filtered or sparse list the corrected timestamp would be wrong in
a new way.

**The real fix is a data-shape change, exactly as the audit's own remedy section says:** klines
must carry `close_ts_ms` (and `is_closed`, `source_event_ts_ms`) rather than having the grader
guess a duration. Then `as_of_close` returns a recorded close time and nothing is inferred.
That is a change to the ingestion path and every kline producer, not a patch to this function.

**DONE, exactly that way.** `backend/kline_schema.py` defines `canonical_kline()`,
`close_ts_ms()` and `is_closed_at()` under one rule — closure must be PROVEN, never
inferred, and unknown means OPEN. Producers record `close_ts_ms` from the exchange on both
transports; `as_of_close` returns the recorded close where it exists and the open time where
it does not, so nothing regresses while the schema propagates. Selection semantics are
untouched, and the docstring now carries the reason.

Related, from the 2026-08-08 sweep: the FIRST-TOUCH path has the mirror-image issue and it is
NOT fixable the same way. Its bar window is shifted forward by up to 60s rather than
mis-stamped, and tightening the selection would grade a 5-minute contract over four minutes.
Every graded row now carries `observed_start_ms` / `observed_end_ms` / `window_shift_ms`
instead. See 5.2 in the closeout document.

### CLAIMED — never checked against code

**Swept 2026-08-08.** Four are resolved and the rest have a verdict:

| # | verdict |
|---|---|
| P0-9 direction/magnitude incoherence | **CONFIRMED-ADJUSTED, recorded.** The served object cannot be incoherent — the target is derived from the direction and `exp_move` is unsigned on all three paths. What was silently discarded is the magnitude head's own SIGN; `magnitudeSignAgrees` now carries it, three-valued |
| P0-10 training vs live neutral band | **FIXED** by `causal_neutral_band` — the live value is the training threshold series, not an instantaneous recomputation |
| P0-13 decision identity not threaded | OPEN. This is the `DecisionEnvelope` work (3.15), tracked as architecture |
| P0-15 restored ≠ live adaptive state | **FIXED.** The percentile window is rehydrated at boot, scoped to the serving release and in the same calibrated/raw namespace the gate compares against |
| P0-17 incomplete composite release | OPEN, and it is one job with P0-16/P0-18 |
| P0-19 compatibility inconsistency | read, not resolved |
| P0-20 bootstrap contradiction | read, not resolved |
| P0-24 unsafe migrations | **CONFIRMED, and it is the real one.** `ALTER TABLE ... except: pass` appears 13 times in `database.py`, so a migration that fails for a genuine reason is indistinguishable from one already applied. The 5.29 funding migration uses `PRAGMA table_info` instead — that is the pattern the rest should adopt, and it matters most wherever an INSERT is positional |
| P0-26 fill-engine optimism | addressed by the `fd46d51` work ("a defaulted gate is a disabled gate"); not re-verified end to end |

P0-11/12 are partially evidenced: the complementarity study exposed the endpoint-vs-first-touch
grading mismatch in the archive (47% of seat predictions are NEUTRAL; `actual_direction` never
is), which is the same defect family.

### P1 — entire set unexamined

A training/model lifecycle · B promotion gates (the Brier ceiling of 0.80 admits models worse
than uniform, ~0.667) · C A/B testing (unbounded memory, shared adaptive state, per-prediction
bootstrap) · D verification and metrics · E API and event-loop reliability · F feed and feature
quality · G deployment and frontend.

---

## Recommended order

1. **Check whether Polymarket + Chainlink history is queryable.** One afternoon. It decides
   whether live capture is urgent or merely necessary.
2. **P0-8 and P0-14** — both corrupt the forward-evidence record a retrain would be judged by.
3. **Backfill or capture round truth**, then retrain.
4. **P0-27** before anything leaves localhost.
5. **P0-16 / P0-18** before the first promotion.
6. Everything else.

---

## Standing constraints

Paper/shadow only; no real-money authority exists. `check_feature_contract` fails until a
retrain, and that retrain should wait for round-aligned labels.

Nothing in this repository is evidence of profitability. The complementarity study argues the
current ensemble has algorithm count rather than information diversity: six of seven seats are
near-duplicates whose errors are positively correlated, and `dl` — the only architecturally
distinct seat — nets −17 once damage is counted against rescue.

---

## THE APP IS NOT SERVING PREDICTIONS, AND HAS NOT SINCE 2026-07-04 — `2026-08-08`

Found by following a data anomaly, not by reading code. Every model-side table stops at the
same instant while the round ticker kept running for another 35 days:

```text
predictions_5m      n=  2,514   last row 2026-07-04 10:44
predictions_15m     n=    861   last row 2026-07-04 10:44
model_predictions   n= 98,195   last row 2026-07-04 10:44
ab_results          n= 14,692   last row 2026-07-04 10:44
price_to_beat       n= 19,122   last row 2026-08-08 08:10   <- still recording
```

Of the 9 5m rounds recorded since, **zero** carry a directional lean.

### Cause, reproduced

```text
$ MultiModelEnsemble(horizons=[5,15]).load_models()
[MODEL LOAD] Rejecting legacy bundle without identity manifest: data/saved_models
-> False        is_trained = False
```

**This is correct behaviour.** A bundle whose provenance cannot be proven must not serve —
that is what `BTC_STRICT_ARTIFACT_IDENTITY` is for, and the refusal is the gate working. The
defect is not the refusal.

### The defect: the refusal had no cause attached

Readiness reported `main_ensemble: UNAVAILABLE` and nothing more. That single word covers
four situations needing four different actions:

```text
joblib_unavailable                    install a dependency
model_dir_absent:<path>               fix configuration
no_identity_manifest                  regenerate provenance   <- the live one
incompatible_bundle:<what differs>    retrain
feature_schema_mismatch               retrain
```

`load_models` computed the exact reason at every refusal and discarded it, and **one path
returned False with no log at all** — a misconfigured directory was indistinguishable from an
untrained model. Every path now records `self.load_refusal`, cleared at entry so a reload
cannot report the previous attempt's cause, and readiness carries it beside the state.

`backend/test_model_load_refusal_reason.py` — 8 checks, 5/5 mutation.

### What to do about the underlying state

The bundle in `data/saved_models` has no identity manifest, so it cannot be served under
strict identity. Two options, and they are not equivalent:

1. **Retrain a bundle.** Also required independently: `check_feature_contract` reports 12 of
   12 artifacts UNKNOWN, and the VWAP formula changed from cumulative to trailing
   time-anchored, so any v1-trained model is being fed a materially different column.
2. **Regenerate a manifest for the existing bundle.** Cheaper, but it would attest provenance
   for a bundle whose feature semantics are already known to be stale. That is signing a
   claim that is not true.

Option 1 is the only honest one, and it was already the standing requirement.

**Read the research in this light.** `LIVE_ROUND_EDGE_AUDIT_2026-08-08.md` covers 2026-06-12
to 2026-07-04 — which is not an arbitrary window, it is the model's entire live serving life.
The finding stands and its span is now explained.
