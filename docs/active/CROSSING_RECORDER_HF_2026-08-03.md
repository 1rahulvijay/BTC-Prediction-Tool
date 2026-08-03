# High-cadence forward crossing recorder

**Built and RUN** `2026-08-03` · **Module** `backend/crossing_recorder_hf.py` ·
**Store** `data/polymarket_crossings_hf.duckdb`

## Why

`CROSSING_HEADS_V1` found reversion genuinely predictable — **AUC 0.6715 at 30s** against a
clock baseline of 0.5196. The **5s and 15s horizons could not be tested at all**, because
`round_state_snapshots` samples every ~15 seconds: 6 resolvable cases at 15s, none at 5s.

Short horizons are where an executable edge would most plausibly live. This recorder is the only
route to them.

## It has actually run

This repository contains a recorder that was wired, selftested, and had **NEVER RUN**. That
distinction is the whole point of `recorder_evidence_check.py`, so this one was run before being
written up:

```
150 polls over 2m30s, 0 failed, 0 gaps
2 crossings detected on live Binance prices:
  ptb_5m_1785790200000   DOWN->UP  at 43s left,  move +5.70
  ptb_15m_1785789900000  DOWN->UP  at 343s left, move +5.70

reverted within  5s:  0/2      <- resolvable, and NOT resolvable from the 15s archive
reverted within 15s:  0/2      <- same
reverted within 30s:  0/2
reverted within 60s:  0/1      (one crossing's round ended first, correctly NULL)
```

**The 5s and 15s labels resolved.** That is the capability the 21-day archive could not provide
at any sample size, demonstrated on live data rather than argued.

## Design

```
cadence     ~1000 ms
rounds      5m and 15m blocks, derived from the wall clock, matching ptb_<n>m_<start_ms>
anchor      the round's OPEN price on the polled venue
leader      UP above the anchor, DOWN below; exactly AT the anchor is not a side
labels      5s / 15s / 30s / 60s reversion, plus is_final_crossing at round end
```

### Recorder honesty, built in

- **Every poll writes a heartbeat**, successful or not. "Wired" and "running" are different
  states and the database distinguishes them.
- **A stall beyond 3 seconds writes an explicit `GAP` row** rather than joining two observations
  across the hole as though they were adjacent.
- **A crossing detected across a gap is tagged `after_gap`**, because a leader change spanning
  missing data may have been several crossings.
- **A label field stays `NULL`** until its horizon has both elapsed *and* fitted inside the
  round. A crossing 3 seconds from settlement has no 60s outcome, and none is invented.

### The anchor is a proxy, and says so

A Polymarket round's official anchor is its settlement reference. This records the round's
**open price on one venue** and stores `price_source` beside every event, so the proxy is
explicit and auditable rather than implied. Any study using this data must treat the anchor as
venue-specific.

### It does not touch the serving path

Separate process, own price poll, own database. Starting or stopping it cannot change what the
application does. That is deliberate: wiring a recorder into live round processing changes
application behaviour and is a separate decision.

## Testability

`Recorder` is a pure state machine — `record(ts_ms, price)` never calls a clock or the network.
Time and price are injected, which is why gap handling, label eligibility and round rollover can
be *asserted* rather than hoped for.

Selftest: **21 checks**, including that the first observation of a round is never a crossing,
that a flip produces one crossing per round horizon, that a 29-second stall writes a gap and
tags the crossing that spans it, that one second after a crossing nothing is resolvable and no
row is produced, and — the point of the module — that **the 5s horizon IS resolvable at 1s
cadence**.

## Limits

- **A single venue's price**, not the official settlement reference.
- **2.5 minutes of data so far.** It must run for weeks before the short-horizon labels have
  enough volume to train on. At the archive's rate — 15,428 crossings over 21 days, ~735/day —
  a comparable sample takes about three weeks.
- **Polling, not streaming.** ~1s REST sampling can miss a crossing that occurs and reverses
  inside one second. A websocket feed would tighten this; the gap and `after_gap` machinery is
  what keeps that honest in the meantime.
- Running it is an operator action. It records nothing while stopped, and the heartbeat table is
  what proves which was the case.

## What it unlocks

```
reverted_5s    currently untestable          -> resolvable
reverted_15s   6 cases in 21 days            -> resolvable
```

Once enough forward data accumulates, `CROSSING_HEADS_V1` can be re-run at the short horizons
under a new preregistration. The existing result stands on 30s and 60s; the 5s and 15s questions
are open and now answerable.

The caveat from that result still applies unchanged: **a crossing probability is an input to a
decision, not a decision.** Every action lane measured in this repository remains closed on
cost.
