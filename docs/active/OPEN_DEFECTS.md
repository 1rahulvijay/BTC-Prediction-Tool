# OPEN DEFECTS AND FUTURE FIXES

Status of every known defect. Updated 2026-08-06. HEAD at time of writing: `9ed6d1d`.

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
PAST rounds. The Chainlink half (`getRoundData` against an archive node or provider) is
standard but has not been queried directly. Usable settlement history therefore starts months
ago, not the day a recorder runs. Live capture is necessary, not schedule-critical. `round_truth.py` is already source-agnostic: it does not
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

**P0-8 — FIXED 2026-08-06** (was: the backtest validates a different target).
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
*Still open:* walk-forward fits a `RandomForestClassifier` surrogate, not the real ensemble.

**P0-14 — two calibration systems disagree.**
`PrecisionEngine` defines correct as `sign(actual_move)` regardless of the prediction's
declared contract, so first-touch confidence is calibrated by endpoint sign. Old calibrators
are not cleared on bundle change, refresh can wait six hours, and zero rows for a new release
leaves the old map active.
*Fix:* one calibrator keyed by (release_id, target_contract, horizon); `bind_release()` clears
immediately and stays unavailable until enough new-release rows exist.

**P0-27 — unauthenticated reads.**
Read routes expose database paths, positions, orders, fills, equity, model and evidence state.
Acceptable on localhost; unsafe the moment anything is reachable beyond it.

**P0-16 / P0-18 — mixed and partial releases.**
Promotion copies manifest files but does not remove stale ones, and the loader scans the
directory — so a release can be new ensemble + old HMM + older calibrator. Partial loads still
mark a bundle trained.
*Fix:* immutable `releases/<id>/`, atomic pointer swap, load only manifest-declared components,
require a complete component matrix.

### CLAIMED — never checked against code

P0-4 candle-open used as resolution timestamp · P0-9 direction/magnitude incoherence ·
P0-10 training vs live neutral band · P0-13 decision identity not threaded ·
P0-15 restored ≠ live adaptive state · P0-17 incomplete composite release ·
P0-19 compatibility inconsistency · P0-20 bootstrap contradiction · P0-24 unsafe migrations ·
P0-26 fill-engine optimism.

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
