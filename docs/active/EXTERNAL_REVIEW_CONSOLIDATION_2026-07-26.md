# External Review Consolidation - 2026-07-26

## Purpose

This is the source-of-truth reconciliation for the external GitHub reviews supplied on
2026-07-26. Those reviews inspected commit `8998d5b`. The local working tree contains newer,
uncommitted collector, calibration, decision-safety and documentation work. A recommendation is
not current merely because it appears in an external review or an older research note.

The governing order is:

1. executable, fee-aware and time-causal evidence;
2. current local code and tests;
3. frozen preregistrations;
4. canonical active documents;
5. older proposals and conversation summaries.

## Current factual state

| Item | Current state |
|---|---|
| Repository used by the app | `C:\Users\rahul\Documents\BTC-Prediction-Tool` |
| Git base | `master`, commit `8998d5b`, plus the documented local changes |
| Multi-venue evidence DB | `data/multi_venue.duckdb`, zero production rows |
| Frozen Binance M0 | Not runnable; requires four continuous qualifying weeks |
| Required venue streams | 9/9 |
| Current research matrix | 360 days, 518,400 one-minute rows |
| Current launcher request | 1,265 historical days |
| Proven 1,265/1,500-day bundle | None; no completion marker exists |
| Real-money strategy approved | None |
| Champion P(Hold) betting | Disabled by default |
| Kelly sizing | Disabled by default |

The 1,500-day runbook is a historical target contract, not proof that a 1,500-day model is
currently serving.

## Implemented and validated

### Evidence collector

- Bybit `publicTrade` is a required Class-A stream; health is 9/9.
- Qualification uses persisted rows, not parsed rows.
- The evidence clock starts only after a successful persistent insert.
- Writer/database failure propagates to the process so systemd can restart it.
- Required-stream age is split into WebSocket and REST limits.
- Feed silence and tail silence are measured; one fresh row cannot mask a dead stream.
- Episode rollover happens before the first event in the new clock window is counted.
- REST HTTP work no longer blocks the async WebSocket event loop.
- Natural event identity is global across the full history.
- REST revisions use publication time plus canonical payload hash.
- REST connection generation and poll number are separate provenance.
- The four-week gate requires a continuous qualifying run, not count plus wall-clock span.

### Prediction and decision safety

- Raw P(Hold) cannot authorize `PAPER_BET` by default.
- Kelly sizing is off by default; the optional paper override uses one share unless separately
  enabled.
- The five mtime-reloaded price-to-beat artifacts are pinned for the process when
  `BTC_FREEZE_MODEL=1`; load-once specialist/meta artifacts are already process-immutable.
- P(Hold calibration and head-health reports use official settlements only.
- Recent drift windows are chronological.
- Unknown execution mode/cost fails closed in the shared decision composer.
- Production can require an admin token and refuses startup if that required token is absent.
- The challenger lives at `backend/phold_challenger.py`; it is deliberately not a
  `backend/calibration/` package because that package name shadows the serving
  `backend/calibration.py` and prevents `server.py` from importing `PrecisionEngine`.

### Current calibration evidence

| Horizon | n | Raw | Shadow challenger | Verdict |
|---|---:|---|---|---|
| 5m | 5,079 | Brier .11151, ECE .08833 | isotonic: Brier .10263, ECE .01361 | wins, not applied |
| 15m | 1,646 | Brier .03612, ECE .02668 | logistic: Brier .03429, ECE .00498 | wins, not applied |

P(Hold) and flip-risk are `CALIBRATION_ONLY`: they may rank but may not price. Champion action
tiers are not monotone and may not be displayed as a confidence scale.

## Already tested and rejected

The following attached proposals are not open implementation items. New information or a new
venue mechanism would be required to reopen them:

| Family | Current executable verdict |
|---|---|
| `LATE_LEADER_30S_V1` | +0.90c mean, block LB -0.60c, PF 1.08; fails promotion |
| 15m static TP-before-SL | 0 of 2,880 cells positive; closed |
| Dynamic stopping policies | all seven worse than hold; conditional M0 closed |
| Complement arbitrage | apparent crossings are stale/collapsed-book artifacts |
| Next-round opening drift | negative after executable spread/fees |
| Maker late leader | delay/queue economics destroy the sub-cent edge |
| Shock sniper / straddles / fade variants | no approved executable edge |
| More generic direction models | direction remains around coin-flip live; not the bottleneck |

Do not rerun these merely with more thresholds. That would be repeated multiple testing, not new
evidence.

## Valid remaining work

### P0 - protect the forward experiment

1. Deploy the current collector with the 9/9 runbook.
2. Set `BTC_DEPLOYMENT_ENV=production`, `BTC_REQUIRE_ADMIN_TOKEN=1` and a secret
   `BTC_ADMIN_TOKEN` on any exposed host.
3. Verify DuckDB writes, excluded episodes, service restarts and the collection start timestamp.
4. Accrue four continuous qualifying weeks; do not score M0 early.
5. Keep all Polymarket actions paper-only.

### P1 - honest decision evaluation

1. Build an immutable policy registry and log each policy's output on the same frozen snapshot.
2. Evaluate gate changes as shadow policies, not by mutating Champion thresholds.
3. Add a canonical `DecisionEnvelope` separating:
   prediction, calibration state, executable quote and fill mode, expected costs, abstention
   reasons, and action.
4. Separate predictive promotion from economic promotion:
   predictive means skill, calibration, drift and stability; economic means executable EV, lower
   bound, capacity, latency and regime stability.
5. Forward-validate the P(Hold) calibrator before any serving adoption.

### P2 - only after recorder evidence exists

1. Build the frozen Binance five-minute episode table from admissible events.
2. Run the preregistered M0 mechanism and its null/control family exactly once.
3. If M0 passes, test execution-cost, quote-survival and capacity heads.
4. If M0 fails, close that lane; do not rescue it with a larger model.
5. Use full L2 only for fillability, VWAP, queue and capacity unless a separate predictive
   preregistration proves otherwise.

### Recorder-gated rather than currently testable

- exact maker fill probability and queue position;
- quote survival after one, three and five seconds;
- current 15m executable bid/ask strategy replay;
- cross-horizon 5m-to-15m execution effects;
- edge duration and capacity by live depth;
- model-versus-no-model execution improvement.

## External proposals not implemented

These may be useful, but are not present in the current serving path:

- Shadow Gate Laboratory and policy registry;
- unified `DecisionEnvelope`;
- explicit predictive/economic promotion service;
- Binance episode builder and M0 scorer;
- CI workflow covering all safety suites;
- calibrated P(Hold) serving adoption;
- regime-duration, options and relative-value research lanes.

They are intentionally below evidence collection and calibration. Adding them now cannot create a
profitable strategy and may make attribution harder.

## Validation completed

```text
python -m compileall -q backend                           PASS
python backend/decision/cost_gate.py                     PASS
python backend/decision/decision_composer.py             PASS
python backend/venues/multi_venue_recorder.py --selftest PASS
python backend/venues/test_collector_integrity.py        PASS
python backend/phold_challenger.py --selftest             PASS
python backend/monitoring/head_health.py --selftest      PASS
python backend/test_paper_trading_integrity.py           PASS
npm run build                                            PASS
git diff --check                                         PASS
development server module import                         PASS
production import without admin token                    PASS (fails closed)
production import with admin token                       PASS
```

## Operational verdict

The software is materially safer than commit `8998d5b`, but it is not a proven profitable bot.
The strongest next contribution is reliable forward evidence plus calibrated, executable
selection. More model families, indicator variants or retrospective threshold searches would
increase complexity without addressing the binding constraints: calibration, latency, fillability,
capacity and independent live replication.
