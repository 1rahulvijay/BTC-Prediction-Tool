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

Twelve lanes. **None produced a positive lower bound on net EV.**

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

## Lanes NOT run — runnable, not yet done

No blocker; simply not reached.

| lane | note |
|---|---|
| `FUNDING_BASIS_CARRY_V1` | matrix has `funding_velocity`; needs the real funding schedule, not an assumed 8h cycle |
| `MICROBASIS_REVERSION_V1` | narrower variant of `SPOT_PERP_BASIS_V1`, which found 0.89 bps against a 12 bps cost — likely closed by the same arithmetic |
| `COMPRESSION_BREAKOUT_V1` | `compression_ratio` present; overlaps `VOLATILITY_EXPANSION_V1` |
| `TREND_PULLBACK_V1` | matrix sufficient |
| `TREND_SURVIVAL_HAZARD_V1` | matrix sufficient |
| `ADVERSE_MOVE_RECOVERY_V1` | matrix sufficient; partly bounded by the symmetric MFE/MAE result |
| `PROFIT_CONTINUATION_GIVEBACK_V1` | matrix sufficient |
| `WAIT_VS_BUY_V1` | PM data sufficient — within-round future ask is computable |
| `POLY_SETTLEMENT_CONVEXITY_V1` | PM data sufficient — ∂²P/∂BTC² from `distance_bps` and `up_mid` |
| `CAPACITY_CURVE_V1` | needs a positive-EV strategy first; nothing has qualified |
| `ALPHA_DECAY_EARLY_WARNING_V1` | same |
| `ALPHA_PORTFOLIO_V1` | same — nothing to allocate between |
| `NEGATIVE_ALPHA_V1` / `ERROR_PREDICTOR_V1` | needs a live strategy whose failures can be labelled |
| `NO_TRADE_GATE_V1` | same |
| `EV_LONG_EV_SHORT_EV_WAIT_V1` | an architecture change to the app, not a research lane |
| `ALPHA_AUCTION_V1` | same |

---

## Honest summary

Twelve lanes ran. **Zero produced a positive lower bound on net EV.**

Two were closed by arithmetic that no model can reach (cost clearance, basis reversion). Two by
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
