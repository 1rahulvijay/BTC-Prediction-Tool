# Test inventory — everything proposed, and what happened to it

Written 2026-08-13. Covers every experiment named across both design documents. Nothing here
is inferred: each row is either a lane that ran with a linked report, or an explicit statement
of why it did not.

## Why the Polymarket atlas cannot be backfilled

Asked to source more PM days from the internet. Two blockers, and the second is the real one.

**1. No network from this environment.** Both probes returned HTTP 403:

```
https://gamma-api.polymarket.com/events?closed=true&...   403 Forbidden
https://clob.polymarket.com/prices-history?...            403 Forbidden
```

**2. Historical quotes do not exist as a retrievable product.** This is the binding constraint,
and it would hold even with working network access.

| data | backfillable? | why |
|---|---|---|
| settlement outcomes | **yes** | Gamma serves closed markets; already done — `pm_export_settlements.parquet` |
| coarse price history | partly | CLOB `/prices-history` returns a price series |
| **executable bid / ask** | **no** | never published historically |
| **L2 ladder depth** | **no** | live WebSocket only |
| **book age / quote timing** | **no** | a property of capture, not of the market |

Every lane on this venue prices against `up_ask` / `up_bid` and pays a fee computed from the
execution price. A mid-price series cannot produce a spread, so it cannot produce an executable
edge, a fee, or a maker test. The atlas needs the same.

**Conclusion: the ten days of paired PM data are the ten days that were recorded.** No API call
creates an eleventh. The only source of more is forward recording — both the quote recorder and
the settlement fetcher running together. That was the recommendation after batch 1 and it is
unchanged, now with the reason established rather than assumed.

---

## Lanes that RAN

| lane | verdict | report |
|---|---|---|
| `BINANCE_COST_CLEARANCE_V1` | CLOSE — <30m impossible at 12bps | [link](binance_cost_clearance/REPORT.md) |
| `VOLATILITY_EXPANSION_V1` | PARTIAL — real, insufficient | [link](volatility_expansion/REPORT.md) |
| `SPOT_PERP_BASIS_V1` | CLOSE — 15x too small | [link](spot_perp_basis/REPORT.md) |
| `TIME_PHASE_ALPHA_V1` | NO EFFECT | [link](time_phase_alpha/REPORT.md) |
| `POLYMARKET_RESIDUAL_V1` | CLOSE as taker | [link](polymarket_residual/REPORT.md) |
| `POLY_FULLSET_ARB_V1` | REAL, NEGLIGIBLE — $43.82/10d | [link](poly_fullset_arb/REPORT.md) |
| `HEDGED_POLY_MM_V1` | INCONCLUSIVE — upper bound only | [link](poly_fullset_arb/REPORT.md) |
| `MARKET_DISAGREEMENT_RESOLUTION_V1` | CLOSE — model loses disagreements | [link](BATCH_2_REPORT.md) |
| `MFE_MAE_DISTRIBUTION_V1` | closes a batch-1 caveat | [link](BATCH_2_REPORT.md) |
| `STATE_VALUE_ATLAS_V1` | UNDERPOWERED | [link](BATCH_2_REPORT.md) |
| `POLY_STALE_QUOTE_V1` | NO EFFECT, barely testable | [link](BATCH_2_REPORT.md) |
| `IMPACT_ASYMMETRY_V1` | REAL, NEGLIGIBLE — 0.056 bps | [link](BATCH_2_REPORT.md) |
| `WAIT_VS_BUY_V1` | NO CAUSAL EDGE — fixed-delay intervals span zero | [link](BATCH_3_REPORT.md) |
| `POLY_SETTLEMENT_CONVEXITY_V1` | STRUCTURAL RISK SURFACE, not a signal | [link](BATCH_3_REPORT.md) |
| `MAKER_MARKOUT_SURFACE_V1` | PARTIAL — hypothetical fills only | [link](BATCH_3_REPORT.md) |
| `COMPRESSION_BREAKOUT_V1` | MOVEMENT DIAGNOSTIC — no direction or executable instrument | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `TREND_PULLBACK_V1` | NO EDGE after 12 bps cost | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `TREND_SURVIVAL_HAZARD_V1` | NO EDGE after 12 bps cost | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `ADVERSE_MOVE_RECOVERY_V1` | GROSS effect, NO NET EDGE | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `PROFIT_CONTINUATION_GIVEBACK_V1` | NO EDGE after 12 bps cost | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `MICROBASIS_REVERSION_V1` | HIGH HIT RATE, about 1 bps gross, NO NET EDGE | [master report](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |

Twenty-one named lanes now have direct results. **None produced a positive lower bound on
executable net EV.** The complete campaign also executed all 42 Phase 5, all 46 Phase 5B and
all nine Phase 5C packages; their row-by-row statuses are in the master report.

---

## Lanes NOT run — blocked by data that does not exist

These need capture the repo has never taken. No amount of analysis substitutes.

| lane | missing input |
|---|---|
| `POLY_PROBABILITY_ELASTICITY_V1` | dense within-round quote series; 10 days is too few rounds to fit ΔP/ΔBTC conditionally |
| `PROBABILITY_ACCELERATION_V1` | same, plus sub-second timing |
| `EDGE_HALF_LIFE_V1` | event-time returns at 100ms–10s; matrix is 1-minute |
| `ORDER_FLOW_SURPRISE_V1` | sequenced L2 with per-level events |
| `BOOK_ELASTICITY_V1` | aggressive volume paired to mid moves at tick resolution |
| `LIQUIDITY_REPLENISHMENT_V1` | per-level add/cancel/execute — depth20 snapshots cannot separate cancel from fill |
| `LIQUIDITY_VACUUM_DISTANCE_V1` | same |
| `CANCELLATION_TOXICITY_V1` | same; also why the repo already marks queue/spoof telemetry research-only |
| `MAKER_MARKOUT_SURFACE_V1` | actual fills. This is the one measurement that decides `HEDGED_POLY_MM_V1` |
| `COUNTERFACTUAL_ORDER_POLICY_V1` | fills under alternative policies |
| `SUB_SECOND_BTC_TO_PM_REPRICING_V1` | synchronised event-time capture on both venues |
| `SYNCHRONIZED_SHOCK_V1` | multi-venue tick data aligned to <500ms |
| `INFORMATION_LEADER_V1` | same |
| `CROSS_ASSET_RELATIVE_VALUE_V1` | ETH/SOL/BNB series — repo records BTC only |
| `FUNDING_DISPERSION_V1` | funding from a second venue |
| `OPTIONS_IV_VS_REALIZED_V1` | Deribit chain history; `gex_live` has 7,763 rows, not a series |
| `POLY_PROBABILITY_SURFACE_V1` | contracts at other expiries; only 5m/15m recorded |
| `CROSS_MARKET_PROBABILITY_TRIANGULATION_V1` | options-implied probabilities, as above |
| `LIQUIDATION_EXHAUSTION_V1` | liquidation feed — not in the matrix |
| `POSITIONING_STATE_MACHINE_V1` | open interest series |
| `FUNDING_OI_CROWDING_V1` | same |
| `HAWKES_EVENT_INTENSITY_V1` | event-time arrivals |
| `MARK_INDEX_LAST_DISLOCATION_V1` | mark and index series separately; matrix has basis only |
| `FUNDING_BASIS_CARRY_V1` | actual paid rate/timestamp/interval and spot financing cash flows; `funding_velocity` is not a payment ledger |

## Lanes NOT run — runnable, not yet done

No blocker; simply not reached.

| lane | note |
|---|---|
| `CAPACITY_CURVE_V1` | needs a positive-EV strategy first; nothing has qualified |
| `ALPHA_DECAY_EARLY_WARNING_V1` | same |
| `ALPHA_PORTFOLIO_V1` | same — nothing to allocate between |
| `NEGATIVE_ALPHA_V1` / `ERROR_PREDICTOR_V1` | needs a live strategy whose failures can be labelled |
| `NO_TRADE_GATE_V1` | same |
| `EV_LONG_EV_SHORT_EV_WAIT_V1` | an architecture change to the app, not a research lane |
| `ALPHA_AUCTION_V1` | same |

---

## Honest summary

Twenty-one named lanes ran directly, plus the 97 frozen Phase 5/5B/5C packages. **Zero produced
a positive lower bound on executable net EV.**

Several were closed by arithmetic that no model can reach (cost clearance, basis and microbasis
reversion). Two by
a baseline the model failed to beat (Polymarket residual, disagreement resolution). One by an
interval that did not separate (time phase). Three are data-starved rather than negative
(atlas, stale quote, and the maker lane's fill question). The rest were closed by cost screens.

**The recurring blocker is capture, not modelling.** Of 24 lanes that could not run, 23 are
missing data the repo has never recorded — event-time L2, fills, a second venue, options
history, liquidations, open interest. One more day of analysis produces none of it.

**The two highest-value actions are both operational:**

1. Run the PM quote recorder and the settlement fetcher **together**, continuously. That
   unblocks the atlas, a second-era check, and every PM lane above.
2. Shadow-post maker quotes and record real fills with markouts at +1s/+5s/+30s. That single
   measurement decides `HEDGED_POLY_MM_V1`, which is currently the only lane whose upper bound
   has not already failed.

---

## Append-only update - 2026-08-13 action-value brief batch

This section appends the results requested by the two later action-value research briefs. It
does not rewrite the inventory above. Full methods and per-test values are in
[BRIEF_ACTION_VALUE_BATCH_REPORT_2026-08-13.md](BRIEF_ACTION_VALUE_BATCH_REPORT_2026-08-13.md).

| lane | verdict |
|---|---|
| `DIRECT_LONG_SHORT_WAIT_V1` | FAIL_NO_EDGE - top-5% model calls lost after 12 bps at 5m/15m/30m |
| `MOVEMENT_GATED_DIRECTION_V1` | FAIL_NO_EDGE - movement AUC 0.688-0.770, but gated direction remained negative |
| `HISTORICAL_MODEL_FAILURE_GATE_V1` | FAIL_NO_EDGE - failure-gate AUC 0.495-0.507 |
| `BINANCE_FIXED_DELAY_ENTRY_V1` | FAIL_UNSTABLE - all 1m/3m/5m delay intervals crossed zero |
| `THESIS_SURVIVAL_CLOCK_V1` | DIAGNOSTIC_ONLY - median first -5 bps close was 4m |
| `CHECKPOINT_HOLD_EXIT_REVERSE_V1` | FAIL_NO_EDGE - all policy and lift lower bounds were negative |
| `BREAKOUT_CONTINUATION_FAILURE_V1` | FAIL_NO_EDGE - AUC 0.510-0.521 and negative net value |
| `MINUTE_SPOT_PERP_LEADERSHIP_V1` | INSUFFICIENT_RESOLUTION - same-minute return correlation 0.992; zero isolated events |

This update supersedes only the earlier status line that listed historical `ERROR_PREDICTOR_V1`
and `EV_LONG_EV_SHORT_EV_WAIT_V1` as not reached. Their historical versions have now run and
failed. A **live** error predictor still requires independently resolved forward strategy calls.

Batch result: **0 promotable configurations; capital authority remains false.**

---

## Append-only update - multi-engine brief batch (2026-08-13)

Five additional answerable families from the 40-question multi-engine brief were executed with
chronological splits, 12 bps Binance cost and family-adjusted day-clustered intervals.

| lane | verdict |
|---|---|
| `RECORDED_REFERENCE_SOURCE_BASIS_V1` | DIAGNOSTIC_ONLY - the recorded PM reference tracked official outcomes better than causal Binance minute spot/perp near expiry; exact rule oracle is not archived |
| `SPOT_PERP_FLOW_DISAGREEMENT_V1` | FAIL_NO_EDGE - disagreement predicted larger movement, not its direction |
| `FUNDING_EVENT_AND_RATE_V1` | FAIL_NO_EDGE - next-rate model lost to the naive baseline; event arms were negative and underpowered |
| `PSYCHOLOGICAL_LEVEL_CONTINUATION_V1` | FAIL_NO_EDGE - $100/$500/$1,000 continuation lost after cost |
| `CONFIDENCE_THRESHOLD_ECONOMICS_V1` | FAIL_NO_EDGE - AUC 0.503-0.523 and every selected threshold was negative |

Full methods, values and all 40 question statuses are in
[MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md](MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md).

Canonical result: `results/multi_engine_brief_batch_20260813T072836Z.json`.

Batch result: **0 promotable configurations; capital authority remains false.**

---

## Append-only update - complete discussion reconciliation (2026-08-13)

Every proposal in the two later research briefs is now explicitly classified in
[COMPLETE_DISCUSSION_TEST_COVERAGE_2026-08-13.md](COMPLETE_DISCUSSION_TEST_COVERAGE_2026-08-13.md).

- Brief A: 60 of 60 questions classified.
- Brief B: 35 of 35 sections classified.
- Every non-run has an exact data, execution, evidence or design prerequisite.
- No undocumented runnable proposal remains from those two briefs.

This is a coverage result, not a profitability result. Promotable configurations remain zero.

---

# Batch 4 — Trade-Economics Lanes (appended 2026-08-14)

Full write-up: `BATCH_4_REPORT.md`. Blocked-lane register: `CANNOT_RUN_INVENTORY.md`.

| Lane | Units | Verdict |
|---|---|---|
| `DIRECT_PNL_DISTRIBUTION_V1` | 10 UTC days / 1,053 rounds | no action survives; selective bounds were artifacts |
| `PM_PROBABILITY_SURFACE_V1` | 9 UTC days / 244 pairs | not established; structure is NOT riskless |

Running total: **23 lanes, 0 with a positive lower bound on net EV.**

## Two artifacts caught in this batch

Both lanes first reported a positive lower bound. Both were false, for different reasons.

1. **Bet-count inflation.** 2,048 "profitable" snapshots were 94 rounds observed ~22 times.
   Collapsing to one bet per round exposed losses the snapshot view hid. Combined with a 332:1
   loss-to-gain ratio at a 0.997 entry, EV at the 95% upper bound on the loss rate is −2.19c
   to −4.89c. A day-block bootstrap does not fix this: it corrects day-level dependence, not a
   position re-counted within a day, and it cannot resample a loss that never occurred.

2. **Look-ahead entry selection.** Choosing each pair's cheapest quote (`idxmin`) inflated the
   result by **+18.47c per bet** — 13× the causal effect — and flipped the verdict from
   "POSITIVE" to "not established".

## Structural finding

Same-expiry PM rounds do **not** share a settlement reference: 5m settles on
`chainlink_btc_usd_twap_30s`, 15m on `chainlink_btc_usd_twap_60s`. 217 of 246 pairs settle at
different expiry prices, and the cross-strike-impossible state is observed. Any cross-horizon
"arbitrage" carries TWAP basis risk and is not riskless.

## Data ceiling measured

PM snapshot coverage is 11 dates in two clusters with a five-week hole (2026-07-05 → 2026-08-08).
Bounds rest on 9–10 independent days, not 3,336 rounds. Binance L2 is 31 sessions / 31.4 hours.

## ROUND_TO_ROUND_TRANSFER_V1 (appended 2026-08-14)

Write-up: `round_to_round_transfer/REPORT.md`.

| Lane | Units | Verdict |
|---|---|---|
| `ROUND_TO_ROUND_TRANSFER_V1` | 19 UTC days / 2,470 5m pairs | 0 of 24 rules clear the cost hurdle |

Running total: **24 lanes, 0 with a positive lower bound on net EV.**

Round-to-round direction is a coin flip at both horizons and across horizons. Closest rule
(5m reversion on the last settled 15m) is 2.89 pp short of the 0.5235 hurdle.

### Third artifact caught, and the first requiring a multiplicity correction

`5m reversion after run>=3` first showed accuracy 0.6196, LCB 0.5466, clearing the hurdle.
Two checks killed it:

- **More data reversed it.** A price filter needed by only two rules had cut the sample for all
  the others. Removing it took the rule from n=163/0.6196 to n=619/0.4879 - across 50%, not
  merely smaller.
- **It is what chance produces.** 24 rules at 5% means ~1 false winner is expected. A
  max-statistic permutation (labels shuffled within UTC day, family re-scored, 2,000 draws)
  gives best-under-null median 0.5404 and p95 0.5818 against an observed best of 0.5508 -
  family-wise p = 0.2985. In a family this size an apparent 55% rule IS the null.

### Correction to a previously recorded claim

`pm_round_settlements` was described as escaping the snapshot hole. It does so only for
direction: `anchor_price`/`expiry_btc` are NULL for 2,283 of 3,336 rounds, and the 1,053 priced
rounds are exactly the snapshotted ones. Direction rules get 19 days; anything price-derived
gets 10.
