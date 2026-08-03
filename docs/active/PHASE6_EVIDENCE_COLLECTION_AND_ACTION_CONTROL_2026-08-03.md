# Phase 6: Evidence Collection And Action Control

Date: 2026-08-03
Freeze window: 2026-08-03 through 2026-09-02

## Decision

The application has not established a post-cost trading edge. Repeating model families or
threshold searches over the same information would increase selection bias, not expected profit.

For the next 30 days the project is an evidence-collection and decision-support system. Its only
permitted engineering changes are recorder reliability, causal data integrity, execution
simulation, official outcome resolution and operator health reporting.

The following work is frozen:

- new historical alpha campaigns;
- new model families;
- threshold tuning;
- strategy promotion;
- real-money execution.

The already-reviewed 1,000-day compatibility retrain is allowed because it restores the current
v14 model contract and artifact provenance. It does not authorize a strategy.

## Measured Conclusion

Current evidence says:

- the Polymarket price contains more resolution information than the current P(Hold) features;
- existing model features have not shown independent post-cost entry value;
- fixed Binance direction and bracket structures did not clear costs;
- raw probability thresholds can select winners while still losing after the share price paid;
- existing evidence windows are too short for subtle-alpha claims.

The three remaining legitimate research lanes are therefore:

1. cheaper passive execution with conservative fill and adverse-selection evidence;
2. better management of positions that are already open;
3. genuinely new, causally recorded information.

## Runtime Collection

`start.bat` launches these public-data, no-credential recorders unless explicitly skipped:

| recorder | purpose |
|---|---|
| Polymarket quote and settlement | official market and settlement evidence |
| Polymarket exact L2 | sequenced depth, trades and exact taker VWAP |
| cross-exchange microstructure | synchronized order-flow context |
| multi-venue event-time | venue leadership and atomic timestamp evidence |
| Binance sequenced L2 | queue/depth research and maker-conversion evidence |

The backend also records model revisions, opportunity decisions, position snapshots, paired books,
five action arms, crossing/reversion outcomes and official action outcomes.

## Protocol B: Post-Entry Crossings

Protocol B uses real `post_entry_crossing_outcomes` rows. It does not use hardcoded zero counts.
Its independent evidence unit is an officially resolved round, not every crossing row.

It measures whether an already-open position crossed the anchor and whether the new side reverted
after 5, 15, 30 or 60 seconds. It may inform future HOLD, REDUCE or EXIT research. It cannot open
an opposite-side trade by itself.

## Protocol C: Five-Arm Position Management

Every causal position checkpoint evaluates the same inventory and paired book under:

```text
HOLD
EXIT
REDUCE_50
SWITCH
LOCK
```

Protocol C completes only after all of these conditions hold:

- at least 1,000 independent resolved rounds;
- at least 56 qualifying calendar days and 8 independent weeks;
- all five arms complete from the same causal snapshot;
- all five arms resolved using official settlement;
- the UP/DOWN books satisfy the fee, skew and receive-time contract.

A HOLD-only row, provisional settlement floor, repeated snapshots from one round, stale book or
future-arriving quote cannot satisfy the gate.

The eventual model target is paired action advantage, such as `EXIT minus HOLD`, rather than raw
absolute PnL. No action model is trained or promoted during the collection window.

## Protocol D: Bounded Passive Execution

Protocol D remains preregistered and recorder-only. Its economic precondition has not passed: the
forward day-clustered lower bound of the entry surplus must first be above zero.

The exact L2 recorders may collect the raw evidence now:

- posted price and visible volume ahead;
- sequenced trades and queue depletion;
- partial and missed fills;
- cancellation and replacement timing;
- post-fill markouts and adverse selection;
- fee/rebate rules and residual inventory.

No maker strategy may claim a fill from a top-of-book touch. No maker score may be calculated or
promoted before the frozen precondition and sample gate pass.

## Daily Health, Not Daily Performance

While the app is running, use:

```powershell
python backend\bc_forward_readiness_report.py
python backend\evidence_health_report.py --expect-live
```

Both commands fall back to the live backend's counts-only API when the writer process owns DuckDB.
The UI System Health tab displays the same frozen B/C requirements and daily collection state.

Daily review is limited to:

- last heartbeat and latest successful write;
- rows and independent rounds written today;
- paired-book and five-arm coverage;
- stale-book and clock-skew refusals;
- missing official outcomes;
- model revision and opportunity counts;
- database freshness and size.

Do not inspect interim PnL, AUC, action ranking or a preferred threshold. The readiness payload is
recursively checked for performance-shaped fields and refuses to emit them.

## Day-One Verification

Within one day after `start.bat`, confirm:

1. every required recorder is `HEALTHY` in System Health;
2. opportunity decisions and model revisions are increasing;
3. the open-position heartbeat increases even when there are no positions;
4. position snapshots and action arms increase when a paper position exists;
5. crossing and official outcome rows appear only after the corresponding events;
6. stale/skew refusals are visible rather than silently discarded;
7. `DATA_GATE_COMPLETE_UNSCORED` never triggers automatic scoring or promotion.

If a count does not move, fix the recorder immediately. Do not wait 30 days to discover that a
collector was not writing.

## Decision On 2026-09-02

After the collection window:

- poor paired-action coverage means fix infrastructure and continue collecting;
- strong five-arm coverage makes one sealed Protocol C score eligible;
- impossible conservative maker-fill evidence closes Protocol D;
- failure to collect either lane closes further Polymarket strategy research;
- no outcome authorizes capital without a separate post-cost forward promotion gate.

## Capital Status

Paper and shadow only. Profit, precision and model accuracy remain measured outputs, not promises.
The application should prevent unsupported trades and explain uncertainty while the evidence clock
runs.
