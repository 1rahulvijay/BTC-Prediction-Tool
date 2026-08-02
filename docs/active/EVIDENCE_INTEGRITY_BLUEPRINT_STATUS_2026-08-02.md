# Evidence Integrity Blueprint Status

Date: 2026-08-02

## Decision

The repository has no measured profitable strategy and no real-money authority. The correct
operating mode is SHADOW/PAPER. The earlier Polymarket fair-value result is retracted because
93.5% of its quote/state pairs used later model state. Its causal reconstruction passed 0 of 3
windows and had a negative day-block lower bound in every window.

Profit, accuracy, and precision remain evaluation targets. They are not properties code review
can guarantee. Promotion requires untouched forward economic evidence after costs.

## Implemented In This Pass

1. The fair-value rule is named `PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1` everywhere and is
   explicitly a benchmark with `CAPITAL_AUTHORITY=False`.
2. ENTER and WAIT decisions require artifact, calibrator, policy, and feature hashes.
3. ENTER and WAIT also store the canonical context payload; the ledger recomputes the feature
   hash and refuses a mismatch.
4. WAIT retains side, ask, bid, fee, calibrated probability, context, and state snapshot. A
   declined opportunity is no longer an empty timestamp.
5. The recorder publishes the actual local receive timestamp for each UP/DOWN book response.
   Request duration remains a separate field and is not mislabelled as receive time.
6. The fair-value probability is paired with the quote for the same BTC-ahead contract. It no
   longer uses whichever token has the higher Polymarket bid.
7. The fully composed round state gets its own timestamp and is persisted before an evaluated
   decision. The slower shadow-head score timestamp is no longer used as the whole-state time.
8. The loaded P(hold) artifact hash is bound at deserialize time. A file swapped during the
   30-second reload throttle cannot be recorded as the identity of the old in-memory model.
9. Proxy and official settlements append immutable outcome events to the opportunity ledger.
   ENTER is valued from the recorded ask/fee; WAIT receives explicit zero PnL. Reprocessing is
   idempotent per outcome kind.
10. The ledger rejects outcomes that predate their decisions.
11. The serviceability gate distinguishes integrity-only sidecars from complete provenance.
12. Stale documentation that called the retracted result measured or a candidate is corrected.

## Validation

| Check | Result |
|---|---:|
| Python compile (`backend`, `research`) | PASS |
| Pytest | 100 passed |
| Opportunity-ledger self-test | 24 checks passed |
| Paper-trading integrity script | PASS |
| Fair-value wiring script | 7 checks passed |
| Vite production build | PASS |
| Local invariant workflow | 79 of 79 passed |
| `git diff --check` | PASS |

## Current Training State

The launcher remains configured for 1,000 historical days with a 98% chronological training
partition. The current saved fleet is not ready for promotion:

```text
artifact files                 25
serving-serviceable artifacts  0
```

The next full retrain must run through `backend/train_heads.py` and the normal launcher so full
provenance manifests are written. Running individual trainers writes integrity sidecars only and
does not make artifacts serviceable. After retraining, rerun `backend/test_artifact_serviceability.py`.

## Blueprint Reconciliation

| Blueprint item | Status |
|---|---|
| Retraction governance and causal-join guard | Implemented |
| Atomic causal ledger for fair-value benchmark | Implemented |
| Exact quote/state/context identities | Implemented for this benchmark |
| Automatic proxy/official settlement outcomes | Implemented for ledger rounds |
| Artifact serviceability measurement | Implemented; current result 0/25 |
| Frozen July 4 Oracle release bundle | Blocked: no immutable release directory/artifact set exists in this checkout |
| Oracle recorder coverage manifest | Not implemented |
| Evidence-class field on every research row | Not platform-wide |
| Canonical checkpoint/path/execution dataset | Not implemented |
| Oracle-versus-current doppelganger | Not implemented |
| W30/W60/W90/W120/W240/W400 transportability | Research utilities exist; canonical campaign not completed |
| Remaining-move, crossing, burst, flip heads | Partial existing heads; blueprint contracts not completed |
| Unified Polymarket action-value catalogue/state machine | Not implemented |
| Unified Binance event-conditioned opportunity engine | Partial paper strategies; not the blueprint engine |
| Residual-error engine and episodic memory | Not implemented |
| Capacity engine and portfolio risk tensor | Partial risk/sizing infrastructure only |
| Anytime-valid evidence dashboard | Partial; not promotion-authoritative |

## Required Next Order

1. Run the 1,000-day retrain through the launcher.
2. Require all serving artifacts to pass provenance and serviceability checks.
3. Refit calibrators only from verified source models.
4. Restart and continuously monitor recorders; record the existing collection gap.
5. Confirm causal-ledger ENTER/WAIT/UNAVAILABLE coverage and official outcomes.
6. Freeze one unchanged forward benchmark for at least eight weeks and 1,000 eligible rounds.
7. Build the Oracle data coverage manifest and canonical causal checkpoint dataset.
8. Only then evaluate market-prior residual and event-conditioned Binance challengers.

Do not start burst, tail, RL, foundation-model, or action-value promotion work before the data and
identity gates pass. More model complexity cannot repair missing causal evidence.
