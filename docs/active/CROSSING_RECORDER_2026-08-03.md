# Polymarket anchor-crossing recorder

**Built** `2026-08-03` · **Module** `backend/polymarket_crossing_recorder.py` ·
**Store** `data/polymarket_crossings.duckdb`

Unblocks the crossing/reversion heads, which have been named as blocked in this ledger twice.

## What existed, and what did not

The parallel session added a `post_entry_crossing_outcomes` table and a writer inside
`open_position_action_recorder.py`. Two things stopped it producing anything:

- **The table does not exist in the live database.** `CREATE TABLE IF NOT EXISTS` only runs when
  the recorder initialises, and it has not run since the code changed.
- **It is coupled to open positions.** `_record_crossing_observations` is private, called only
  from the position-capture path, and there are **0 position snapshots** — the calibrator cannot
  deploy while 0 of 25 artifacts are serviceable. Protocol B therefore could not collect
  crossings no matter how long collection ran.

A crossing is a property of the **BTC path against the round anchor**. It does not require
holding a position. This recorder is standalone, so crossing evidence accrues whether or not the
position lane is unblocked, and Protocol B can later join its positions to it.

## Two tables, because the facts are known at different times

```
crossing_events   immutable, complete at the instant of the crossing
crossing_labels   appended only when each horizon has actually elapsed
```

The earlier single-row design held `crossing_ts` beside `reverted_60s` and
`settlement_resolved`. Those are known a minute and several minutes apart. One row invites
writing a label before its horizon has passed, and makes *"not yet known"* indistinguishable
from *"known to be false"* — the same NULL-versus-zero defect that has appeared repeatedly here.

An event is never updated. A label field stays `NULL` until its horizon has both **elapsed in
wall-clock time** and **fitted inside the round**. Asserted in the selftest.

## Validated against 21 days of real rounds

```
6,732 rounds  ->  15,428 crossings  over 5,738 crossing-bearing rounds  (2.69 each)
```

| statistic | value |
|---|---|
| **crossings that are FINAL** | **5,738 / 15,428 = 37.2%** |
| reverted within 30s | 2,781 / 14,844 = 18.7% |
| reverted within 60s | 4,295 / 14,273 = 30.1% |
| reverted within 15s | 2 / 6 — *see below* |
| reverted within 5s | no resolvable cases |

**37.2% of anchor crossings are final; 62.8% get undone.** That is the first direct measurement
of the quantity the crossing heads are meant to predict, and it is a usable base rate.

## The cadence limit, stated plainly

`round_state_snapshots` samples roughly every 15 seconds. The 5s and 15s reversion horizons
therefore almost never contain a later observation, and the recorder correctly writes `NULL`
rather than guessing — 6 resolvable cases at 15s, none at 5s.

So the short-horizon reversion labels the strategy notes call for **cannot** come from this
archive. They need forward collection at a finer cadence. The 30s and 60s labels are well
populated and usable now.

This is a data-cadence limit, not a recorder defect: writing a `False` where no observation
exists would have produced 15,428 confident short-horizon labels, all invented.

## Not wired into the live app

The module provides a pure `detect_crossings()`, a writer, and a backfill. **It is not called
from the serving path.** Wiring a recorder into live round processing changes application
behaviour and needs explicit approval; the evidence it produces is available now from backfill
regardless.

## Governance

- `detect_crossings()` is pure — no I/O, no clock, no future. Input order cannot change the
  answer; it sorts by time, and the selftest asserts a reversed input gives an identical result.
- The **first observation of a round is never a crossing** — nothing precedes it to cross from.
- An unknown leader is skipped rather than treated as a flip to and from it.
- Events are idempotent on `crossing_id = sha256(round_id | crossing_ts)`; re-writing is a no-op.
- Selftest: **20 checks**, including that one second after a crossing *nothing* is resolvable and
  no label row is written at all, that an unelapsed horizon stays `NULL` rather than defaulting
  to `False`, and that a crossing 3 seconds before settlement has no 60s outcome and none is
  invented.

## What this enables

```
ANCHOR_RECROSS_HEAD      P(recross within 30s / 60s)   base rates now measured
FINAL_CROSSING_HEAD      P(this crossing is final)     base rate 37.2%
```

Both were listed as blocked. They are now buildable on 15,428 real labelled crossings — though
any such head is a new preregistration, and the 37.2% base rate is a *prior*, not a result.

Protocol B remains blocked for its own reason: it scores crossings on **open positions**, and
there are none. This recorder removes the crossing-data blocker but not the position blocker.
