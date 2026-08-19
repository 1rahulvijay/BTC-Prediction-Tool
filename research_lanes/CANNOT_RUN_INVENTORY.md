# Lanes That Cannot Currently Run — And Exactly What Unblocks Each

Date: 2026-08-14
Companion to `TEST_INVENTORY.md` (lanes already run) and `BATCH_4_REPORT.md` (latest batch).

The purpose of this file is to stop blocked lanes from being re-proposed, re-scoped and
half-run on data that cannot support them. A lane run on insufficient data does not return
"no result" — it returns a *number*, and that number is indistinguishable from a finding.

Every entry names the blocker and the specific thing that removes it.

---

## Measured data ceilings (2026-08-14)

These numbers, not the raw row counts, decide what is runnable.

| Source | Volume | **Independence units** | Span |
|---|---|---|---|
| Binance L2 events (`binance_l2.duckdb`) | 1,153,715 diffs | **31 sessions / 31.4 recorded hours** | 4 calendar days, ~32% duty cycle |
| PM round snapshots | 178,481 obs | **10 UTC days**, 1,053 rounds | 11 dates in 2 clusters, 5-week hole |
| PM settlements | 3,336 rounds | 58 days | 2026-06-16 → 2026-08-13 |
| Deribit options | 854 MB | not yet assessed | — |
| `capture_app` archive | **zero** | **zero** | never run |

The recurring trap: row counts look ample while independence units do not. 1.15M L2 events is
**n≈31** for anything book-related, inside what is effectively one regime.

---

## Category A — blocked on data that does not exist yet

`capture_app` was built to collect exactly this and **has never run**. It is blocked on GCP
billing, not on engineering. These lanes stay closed until it has produced weeks of data.

| Lane | Blocker |
|---|---|
| `LIQUIDITY_WITHDRAWAL_V1` | Sub-second depth-collapse labels need continuous event-level L2. 31 sessions over 4 days is one regime. Separately: **Binance diffs cannot distinguish a cancel from a fill** — a shrinking level is either. Requires a trade-stream join to infer, which is approximate. |
| `QUEUE_SURVIVAL_V1` | Same L2 ceiling, plus queue *position* is not public anywhere. Only scenario-bounded estimates are possible (`QueueEstimator` already implements the conservative/base/optimistic scenarios). A real number needs authenticated own-order fills. |
| `FLOW_CASCADE_V1` | Hawkes/point-process intensity estimation on 31.4 hours of a single regime will fit noise. |
| `PRICE_IMPACT_ASYMMETRY_V1` | Impact is *computable* mechanically from a book snapshot today. That it **predicts** forward returns is not testable at n≈31. Half-runnable; the half that matters is blocked. |
| `EDGE_SURVIVAL_V1` (new) | Requires 50ms–30s edge-decay labels. Same ceiling as above. |
| `JUMP_HAZARD_V1` (new) | Needs many jump events across regimes; 4 days contains too few. |
| `SLIPPAGE_TAIL_V1` (new) | q99 execution cost cannot be estimated from 31 sessions and zero own-order fills. |
| `LIQUIDITY_POCKET_V1` (new) | Needs sustained OI/liquidation history. `futures_liquidations` and `futures_open_interest` exist only in `capture_app`, which has never run. |
| `VOL_OF_VOL_V1` (new) | Vol-of-vol over 4 days of L2 is not identifiable; a longer bar series could support a coarser version. |

**Unblocked by:** `capture_app` running continuously for 4–8 weeks. Enable GCP billing, then
`bash capture_app/deploy_gcp.sh create`.

---

## Category B — structurally blocked: they need a profitable strategy to exist first

These are not data problems. They take a live positive-EV strategy as *input*, and no strategy
has qualified in 23 lanes. Running them now would mean labelling the failures of a strategy that
was never established.

| Lane | Needs |
|---|---|
| `ALPHA_FAILURE_DETECTOR_V1` / `CALIBRATION_DECAY_V1` (new) | A live strategy with EV to lose |
| `CAPACITY_DYNAMIC_V1` (new) / `CAPACITY_CURVE_V1` | A positive-EV strategy to measure capacity of |
| `NEGATIVE_ALPHA_V1` / `ERROR_PREDICTOR_V1` | A live strategy whose failures can be labelled |
| `OPTIMAL_STOPPING_V1` / `REENTRY_V1` / partial-exit (new) | Real positions with real fills |
| `ALPHA_CONFLICT_V1` (new) | Two or more strategies that each have edge |
| `PROFIT_CONCENTRATION_V1` (new) | A profit series to test concentration of |

**Unblocked by:** any lane producing a positive lower bound. Nothing else.

---

## Category C — instrument or venue does not exist here

| Lane | Reason |
|---|---|
| `PM_PROBABILITY_SURFACE_V1` (ladder form) | **No strike ladder.** PM BTC up/down rounds are single-strike. Only the 2-point same-expiry structure exists — run in Batch 4, not established, and **not riskless** because the 5m and 15m legs settle on different TWAPs (30s vs 60s). |
| `BASIS_CURVE_V1` / `FUTURES_CURVE_V1` (new) | Quarterly/delivery futures were never collected. `capture_app` records perp only. Needs a new stream before the lane is even definable. |
| `BTC_BETA_RESIDUAL_V1` (new) | Needs synchronized ETH/SOL/BNB series. Not collected. `capture_app` is BTC-only today. |
| `STABLECOIN_STRESS_V1` (new) | USDT/USDC/USD series not collected anywhere. |
| `OPTIONS_GAMMA_PRESSURE_V1` (new) | `deribit_options.duckdb` is 854 MB and **not yet assessed** — this is the one Category-C entry that may be wrong. Worth an inventory pass before assuming it is blocked. |

---

## Category D — already built, not a research lane

| Proposed | Status |
|---|---|
| `DATA_HEALTH_SCORE` (new #20) | Largely exists: `evidence_health.duckdb`, `DO_NOT_TRUST` blockers, recorder health, forward-readiness snapshot, and the `polymarket_quotes` quote-source health surface added 2026-08-14. Gap is the graded 100/80/60/40 sizing response, not the measurement. |
| Conformal abstention (#9, prior batch) | Methodological wrapper over an existing model, not a standalone data lane. Runnable whenever wanted. |

---

## Runnable now, not yet run

Ordered by independence units available, not by expected payoff.

| Lane | Units | Note |
|---|---|---|
| `NEXT_ROUND_OPENING_V1` | 10 PM days | `pm_round_snapshots.seconds_elapsed` supports the opening-convergence test |
| `ROUND_TO_ROUND_TRANSFER_V1` | 58 PM days | Uses settlements only, so it escapes the snapshot hole — the **best-powered** lane available |
| `COMPETING_RISKS_V1` (new) | 58 PM days | TP/SL/timeout is definable on PM rounds from settlement + path |
| `PATH_ASYMMETRY_V1` (new) | 58 PM days | MFE/MAE already computed in a prior batch (7.97 vs 7.99, symmetric) — extend rather than restart |
| `TRADE_ELIGIBILITY_V1` (new) | 10 PM days | Is a supervised wrapper over Batch 4's realized-PnL target; build only if some action first shows a positive bound |
| `EXIT_EDGE_DECAY_V1` | 10 PM days | 178k in-round snapshots, 5m/15m only |
| `REGIME_EXIT_HAZARD_V1` (new) | depends | Runnable at 1m bar resolution on the research matrix; **not** runnable at the 10s–60s resolution proposed |

`ROUND_TO_ROUND_TRANSFER_V1` is the standout: it needs only settled rounds, so it gets 58 day-blocks
instead of 10.

---

## Standing rule

Before adding a lane to the queue, state its **independence unit count**, not its row count. If
that number is below roughly 30, the lane produces a number and not evidence, and it belongs in
Category A until the data exists.
