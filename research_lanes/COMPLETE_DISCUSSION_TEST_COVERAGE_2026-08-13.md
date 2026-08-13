# Complete Discussion Test Coverage Ledger

Date: 2026-08-13

Authority: **RESEARCH ONLY**

This append-only ledger accounts for every proposal in the two research briefs supplied on
2026-08-13. It distinguishes process completion from scientific success. `RAN` means the test
executed; it does not mean the hypothesis passed or made money.

## Status Definitions

| Status | Meaning |
|---|---|
| `RAN` | Executed in the latest action-value batch |
| `RAN_PREVIOUSLY` | An existing frozen experiment already answered the question |
| `PARTIAL` | A valid subset ran, but final execution evidence is missing |
| `BLOCKED_DATA` | The causal source needed for the test was never recorded |
| `BLOCKED_EVIDENCE` | The test requires a positive candidate, fills or independent forward outcomes that do not exist |
| `INSUFFICIENT_SAMPLE` | Correct data exists, but the independent sample gate was not met |
| `DESIGN_ONLY` | Architecture/decision policy, not a hypothesis that can be scored before its inputs qualify |

## Evidence Keys

| Key | Evidence |
|---|---|
| `B4` | [Action-value brief batch](BRIEF_ACTION_VALUE_BATCH_REPORT_2026-08-13.md) |
| `MC` | [Complete alpha-lab campaign](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md) |
| `P5` | [Phase 5 suite](../docs/active/PHASE5_STANDALONE_RESEARCH_SUITE_2026-08-02.md) |
| `P5B` | [Phase 5B results](../docs/active/PHASE5B_STANDALONE_RESEARCH_RESULTS_2026-08-02.md) |
| `TP` | [Trade-policy heads results](../docs/active/TRADE_POLICY_HEADS_120D_RESULTS_2026-07-27.md) |
| `SB` | [Stopping baselines](../docs/active/STOPPING_BASELINES_2026-07-31.md) |
| `L2` | [Polymarket depth/queue simulation](../docs/active/POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md) |

## Brief A - All 60 Questions

| # | Proposed research question | Status | Evidence / exact reason not run |
|---:|---|---|---|
| 1 | Highest conservative EV across LONG/SHORT/PM/maker/basis/WAIT | `DESIGN_ONLY` | B4 tested LONG/SHORT/WAIT; other arms have no qualified positive-EV candidate, so a master auction cannot be validated |
| 2 | Binance moves before Polymarket | `BLOCKED_DATA` | P5B #73; no atomic sub-second Binance/paired-PM join |
| 3 | Polymarket moves before Binance | `BLOCKED_DATA` | Same missing synchronized event-time join |
| 4 | Dynamic venue information leader | `BLOCKED_DATA` | B4 one-minute proxy found zero isolated events at 0.992 correlation; sub-second multi-venue prices are absent |
| 5 | State-dependent Polymarket probability elasticity | `RAN_PREVIOUSLY` | P5 #19-20 and MC; insufficient/negative executable evidence |
| 6 | Current Polymarket quote is stale | `RAN_PREVIOUSLY` | MC/BATCH 2 stale-quote lane; no effect at available resolution, sub-second version remains blocked |
| 7 | Model beats market in this disagreement state | `RAN_PREVIOUSLY` | MC disagreement lane; model won only about 33-40% as disagreement widened |
| 8 | Probability the main model is wrong | `RAN` | B4 failure gate AUC 0.495-0.507; live version still needs forward resolved calls |
| 9 | Lower confidence bound of expected profit | `RAN` | B4 direct action value; every after-cost LCB was negative |
| 10 | Realizable alpha capture ratio | `PARTIAL` | P5 #1 tested extractability upper bound; realized capture needs actual fills and latency markouts |
| 11 | Edge half-life from 50ms to 30s | `BLOCKED_DATA` | Model-specific event-time returns/quotes at those lags were never recorded |
| 12 | Taker now, maker, wait or skip | `PARTIAL` | B4 fixed waits ran and failed; maker/cross counterfactuals require fills and queue position |
| 13 | Markout after passive fill | `PARTIAL` | MC maker markout is hypothetical-fill only; actual fill-conditioned markout is absent |
| 14 | Maker quote distance maximizing EV | `BLOCKED_DATA` | Requires observed queue/fills for best, +/-1 and +/-2 tick quotes |
| 15 | Maker cancel/pickoff risk | `BLOCKED_DATA` | Requires order acknowledgements, cancel timing, queue and synchronized fair-value changes |
| 16 | Delta-hedged Polymarket market making | `PARTIAL` | MC hedged-maker upper bound exists; both-leg fills and adverse selection are unobserved |
| 17 | Executable YES + NO parity | `RAN_PREVIOUSLY` | MC full-set lane; 114 opportunities, $43.82 theoretical over 10 days, economically negligible |
| 18 | Cross-expiry Polymarket probability surface | `RAN_PREVIOUSLY` | P5 #25; only 36 positive lock observations, below the 100-action gate |
| 19 | Polymarket-implied versus realized/options volatility | `PARTIAL` | Deadline/magnitude diagnostics ran; historical options chain and executable vol instrument are absent |
| 20 | Probability acceleration versus BTC acceleration | `BLOCKED_DATA` | Needs dense synchronized PM and BTC event-time paths |
| 21 | Will movement cover all-in costs? | `RAN` | B4 movement AUC 0.688-0.770; gated direction still lost after 12 bps |
| 22 | Full path distribution, MFE/MAE and quantiles | `RAN_PREVIOUSLY` | MC Phase 5C MFE/MAE and prior path/quantile reports; diagnostic, not profitable action |
| 23 | Target-specific first passage | `RAN_PREVIOUSLY` | TP/SB and prior barrier studies; no robust after-cost policy qualified |
| 24 | State-specific stop/target matrix | `RAN_PREVIOUSLY` | TP/SB; parameter grids did not establish an untouched positive-EV policy |
| 25 | Early exit after thesis deterioration | `RAN` | B4 checkpoint HOLD/EXIT/REVERSE plus prior exit tests; all policy/lift LCBs negative |
| 26 | Directional edge already consumed | `RAN_PREVIOUSLY` | P5B #57 information exhaustion; useful diagnostic, no causal executable policy |
| 27 | Breakout continuation versus fakeout | `RAN` | B4 AUC 0.510-0.521; all selected continuation trades negative after cost |
| 28 | Momentum continuation, stall or exhaustion | `RAN_PREVIOUSLY` | MC continuation/recovery lanes; point effects did not clear 12 bps |
| 29 | Liquidation continuation versus exhaustion | `BLOCKED_DATA` | Historical liquidation events are not aligned to the research matrix |
| 30 | Price/OI/flow positioning-state transitions | `BLOCKED_DATA` | Open-interest history is absent from the causal matrix |
| 31 | Perpetual moves too far versus spot | `RAN_PREVIOUSLY` | MC microbasis/basis lanes; about 1 bps gross versus 12 bps cost |
| 32 | Funding carry economics | `BLOCKED_DATA` | Funding velocity is not a paid-rate/timestamp cash-flow ledger; financing cash flows absent |
| 33 | Prefer spot or perpetual for the same view | `DESIGN_ONLY` | Needs a positive underlying alpha plus executable venue cost/capacity estimates |
| 34 | Delta-neutral spot/perpetual convergence | `RAN_PREVIOUSLY` | MC basis lanes; real convergence was too small after costs |
| 35 | Book elasticity | `PARTIAL` | MC impact proxy ran; exact elasticity needs aggressive notional and sequenced L2 changes |
| 36 | Buy/sell impact asymmetry | `RAN_PREVIOUSLY` | MC impact asymmetry was economically negligible; P5B #63 unstable |
| 37 | Displayed liquidity authenticity/replenishment | `PARTIAL` | P5B #59-60/72 are descriptive; add/cancel/execute and queue identity remain absent |
| 38 | Liquidity vacuum distance | `BLOCKED_DATA` | Exact per-level event sequence and time-to-replenish are not present |
| 39 | Unexpected order-flow residual | `BLOCKED_DATA` | Requires event-time OFI and expected-flow model on sequenced events |
| 40 | Self-exciting order-flow intensity | `BLOCKED_DATA` | Hawkes/event-arrival data is absent from the minute matrix |
| 41 | Synchronized multi-venue shocks | `BLOCKED_DATA` | Aligned Binance/Coinbase/Bybit event-time prices are absent |
| 42 | ETH/SOL/BNB lead BTC | `BLOCKED_DATA` | Repository historical research matrix is BTC-only |
| 43 | Exact clock-boundary behavior | `RAN_PREVIOUSLY` | MC time-phase lane; confidence intervals did not separate |
| 44 | Online change point/regime transition | `RAN_PREVIOUSLY` | P5B #55/#68/#82/#83; state diagnostics were unstable/non-economic |
| 45 | Nearest-neighbor historical support | `RAN_PREVIOUSLY` | P5B #51-52; analogue AUC about 0.503 and no economic lift |
| 46 | Positive State Value Atlas | `RAN_PREVIOUSLY` | MC atlas: 43 eligible cells, zero family-wise significant cells |
| 47 | Inverse/negative State Value Atlas | `PARTIAL` | Same atlas contains losing cells, but no base positive strategy exists for a validated veto policy |
| 48 | Strategy receiving the next dollar | `BLOCKED_EVIDENCE` | No strategy has positive executable EV; allocator comparison is undefined |
| 49 | Independence/correlation of winning strategies | `BLOCKED_EVIDENCE` | No set of winning strategies or canonical simultaneous PnL ledger exists |
| 50 | Capacity curve | `BLOCKED_EVIDENCE` | P5 #36; capacity is undefined before alpha and requires fillable depth |
| 51 | Cost stress at 1.25x/1.5x/2x | `RAN_PREVIOUSLY` | MC cost-clearance surface; candidates already fail at 1.0x, so higher positive costs are mathematically dominated |
| 52 | Profit concentration in a few days | `BLOCKED_EVIDENCE` | P5 #41; no qualified positive candidate with independent daily PnL |
| 53 | Alpha decay across 90d/30d/14d/7d/3d | `BLOCKED_EVIDENCE` | P5 #30; no canonical positive candidate evidence across windows |
| 54 | Strategy activation/sleep model | `DESIGN_ONLY` | Cannot train an activation policy until a strategy has positive state-conditional EV |
| 55 | Opportunity cost of holding current trade | `RAN_PREVIOUSLY` | P5B #76/#81 and B4 management; waiting/capital efficiency did not improve economics |
| 56 | Optimal simultaneous trade combination | `BLOCKED_EVIDENCE` | Requires multiple positive strategies and synchronized executable PnL/covariance |
| 57 | Monetize information without direction | `RAN_PREVIOUSLY` | Full-set, basis, microbasis and maker upper-bound lanes ran; none is promotable |
| 58 | Smallest top-ranked subset producing profit | `PARTIAL` | B4 top-5%/top-decile and MC atlas ran; no selected subset had positive LCB |
| 59 | WAIT as a competing strategy | `RAN` | B4 LONG/SHORT/WAIT and fixed-delay tests; no positive policy |
| 60 | Forward executable profit proof | `INSUFFICIENT_SAMPLE` | P5 #37 and forward ledgers lack enough independently resolved executable outcomes |

All 60 questions are classified. There is no unlisted runnable test hidden by this ledger.

## Brief B - All 35 Sections

This brief repeats many Brief A questions with more detail. The table records the full section
coverage rather than pretending duplicate wording is a new independent experiment.

| # | Section | Status | Evidence / remaining gap |
|---:|---|---|---|
| 1 | Whether to trade at all | `RAN` | B4 movement and direct LONG/SHORT/WAIT value; no positive LCB |
| 2 | Entry timing | `PARTIAL` | B4 fixed delays ran; maker, pullback-fill and staged-entry arms need execution data |
| 3 | While already LONG/SHORT | `PARTIAL` | B4 HOLD/EXIT/REVERSE ran; REDUCE/LOCK need atomic counterfactual action arms |
| 4 | Loser management | `PARTIAL` | B4 adverse checkpoint and MC recovery ran; causal opportunity cost/fills remain missing |
| 5 | Winner management | `PARTIAL` | B4 favorable checkpoint and MC giveback ran; independent trailing/reduce fills remain missing |
| 6 | Entire price path | `RAN_PREVIOUSLY` | MC Phase 5C and path/quantile reports |
| 7 | Competing risks/first passage | `RAN_PREVIOUSLY` | TP/SB barrier studies; no after-cost winner |
| 8 | Volatility rather than direction | `RAN` | B4/MC movement models predict activity but not executable direction profit |
| 9 | Regime transitions | `RAN_PREVIOUSLY` | P5B #68/#82/#83; diagnostic only |
| 10 | Liquidations | `BLOCKED_DATA` | No aligned liquidation event history |
| 11 | Open interest | `BLOCKED_DATA` | No causal OI series in the research matrix |
| 12 | Funding | `BLOCKED_DATA` | No payment ledger/financing cash flows |
| 13 | Basis | `RAN_PREVIOUSLY` | MC basis/microbasis; too small after cost |
| 14 | Spot versus perpetual leadership | `INSUFFICIENT_SAMPLE` | B4 minute data cannot order events; sub-second join missing |
| 15 | Cross-crypto relative value | `BLOCKED_DATA` | BTC-only historical research panel |
| 16 | Mean reversion | `RAN_PREVIOUSLY` | Basis, VWAP/path and reversal research ran; no robust executable edge |
| 17 | Trend age/remaining life | `RAN_PREVIOUSLY` | MC trend survival/pullback; no edge after 12 bps |
| 18 | HFT/microstructure | `BLOCKED_DATA` | Sequenced L2, queue and 10ms-5s markouts absent |
| 19 | Maker orders | `BLOCKED_DATA` | Actual quote attempts, queue, fills and cancel outcomes absent |
| 20 | Capacity | `BLOCKED_EVIDENCE` | No positive alpha and no realized fill curve |
| 21 | Polymarket maker | `PARTIAL` | MC/L2 upper bounds only; actual fill-conditioned economics absent |
| 22 | Polymarket binary fair value | `RAN_PREVIOUSLY` | MC market-prior residual: market beat the model; no promotion |
| 23 | Polymarket cross-contract arbitrage | `RAN_PREVIOUSLY` | P5 #25 insufficient cross-expiry sample |
| 24 | Complete set | `RAN_PREVIOUSLY` | MC full-set lane mechanically real, economically negligible |
| 25 | Polymarket/Binance hedge | `PARTIAL` | Hedged-maker upper bound; fills/rehedging costs unobserved |
| 26 | Cross-venue arbitrage | `BLOCKED_DATA` | No synchronized sub-second cross-venue series |
| 27 | Event-specific strategies | `PARTIAL` | Session/weekend diagnostics exist; CPI/FOMC/news/outage labels do not |
| 28 | Time of day | `RAN_PREVIOUSLY` | MC time-phase plus P5B environment slices; no stable edge |
| 29 | Model trust | `RAN` | B4 historical failure gate failed; live trust needs forward resolved releases |
| 30 | Prediction stability | `BLOCKED_DATA` | P5B #43-50; continuous release-bound revision paths absent |
| 31 | Capital efficiency | `RAN_PREVIOUSLY` | P5B #81 mean PnL/capital-minute was negative |
| 32 | Portfolio | `BLOCKED_EVIDENCE` | No multiple positive strategies/canonical joint PnL ledger |
| 33 | Drawdown control | `DESIGN_ONLY` | Risk shutdown exists as architecture; predictive scaling needs a proven live strategy |
| 34 | Model/error attribution | `RAN_PREVIOUSLY` | P5B #79-80; taxonomy/attribution incomplete and unstable |
| 35 | Decision-value contract | `DESIGN_ONLY` | Enforced by campaign promotion rule: no after-cost positive LCB means no wiring |

All 35 sections are classified.

## What Actually Ran in the Latest Campaign

The process evidence is explicit:

- 42 Phase 5 packages;
- 46 Phase 5B packages;
- 9 Phase 5C path diagnostics;
- Binance cost-clearance, volatility, time-phase, basis and path-extension lanes;
- Polymarket prior/residual, complete-set, state-atlas, disagreement, timing and hypothetical
  maker-markout lanes;
- 7 additional action-value families in B4.

The latest complete campaign ran 20 stages with zero process failures. B4 then ran separately
and completed all eight stages, including the blocked-question register.

## Tests Not Run and Why

Every not-run item above belongs to one of these explicit blocker classes:

1. **Event-time data absent:** sub-second cross-venue leadership, quote half-life, HFT markout,
   OFI/Hawkes, stale/cancel timing.
2. **Execution outcomes absent:** queue position, actual maker fills, reprice/cancel/cross arms,
   fill-conditioned adverse selection and capacity.
3. **Economic series absent:** open interest, liquidation events, actual funding payments,
   financing cash flows, multi-asset history and options chain history.
4. **Evidence prerequisite absent:** no independently positive executable strategy exists, so
   portfolio allocation, profit concentration, decay, activation and capacity cannot be scored
   without manufacturing a candidate.
5. **Design-only:** master action auction and risk allocation are product architecture. Their
   value can only be tested once their action inputs have valid economic evidence.

These are documented non-runs, not omissions. A minute-candle proxy is intentionally not used
when the question requires event ordering, queue state or realized fills.

## Final Reconciliation

- Brief A questions classified: **60 / 60**.
- Brief B sections classified: **35 / 35**.
- Latest B4 promotable configurations: **0**.
- Complete campaign promotable configurations: **0**.
- Capital authority: **false**.

No undocumented proposal from the two supplied briefs remains. No test result in this ledger
supports live-capital deployment.

---

## Append-only Brief C Reconciliation - 2026-08-13

A later 40-question multi-engine brief has now been reconciled separately in
[MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md](MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md).

- Brief C questions classified: **40 / 40**.
- Newly executed families: **5**.
- Newly promotable configurations: **0**.
- Capital authority: **false**.

This appendix does not alter Brief A or Brief B classifications above. It adds the new evidence
for recorded reference-source integrity, spot/perp CVD disagreement, funding events, psychological
levels and confidence-threshold economics, plus exact blockers for all other Brief C questions.
