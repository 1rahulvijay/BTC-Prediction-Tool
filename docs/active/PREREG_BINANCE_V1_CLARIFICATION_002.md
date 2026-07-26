# CLARIFICATION 002 — `BINANCE_VOLATILITY_MOMENTUM_V1`

Interpretation constraint on receive-time ordering. Like 001, this **completes** rather than
amends: the original protocol file is untouched and its hash is unchanged.

```
Declared:                        2026-07-26
Production rows at declaration:  0
M0 results available:            no
Any Binance analysis run:        no

Clarification:
    a receive-basis ordering may not be reported as an economic lead

Reason:
    section 4 admits cross-venue lead-lag as a Class A feature, and
    Binance spot bookTicker carries no exchange timestamp, so some
    lead-lag can only be computed in receive time

Original preregistration:
    unchanged
    sha256 0973744b73651e8287b44309c976530f72a3964ceb082703c6b49400564c72f7
```

## The distinction

```
RECEIVE_TIME A precedes RECEIVE_TIME B
```

means **A reached this collector first.** It does not mean **venue A moved first economically.**

Receive-time order also contains:

```
network-route differences        venue publication latency
WebSocket batching               local event-loop scheduling
reconnect state                  parser/queue delay
```

Calling that ordering `venue_lead` smuggles a price-discovery claim into a network measurement.

## Binding rules

**1. Naming.** A receive-basis lead-lag feature must be named `observer_time_lead` or
`collector_arrival_lead`. It may not be named `venue_lead` or any equivalent economic term.
Enforced by `venue_admissibility.leadlag_feature_name()`, which raises at feature-definition time —
when the feature is written, not when someone is reading a result they already like.

**2. Attribution.** Receive-time ordering is a property of a specific observer, so the observer is
identifiable per row: `process_start_id`, `connection_id`, `queue_delay_ms`,
`processing_delay_ms`, plus `collector_host_id` / `pid` / `started_ts` / code hashes resolved
through `collector_sessions`. Stream health for the containing episode is in `venue_episodes`.
Without this, "A arrived before B" is an unattributable claim.

**3. Price-discovery claims.** Any test asserting true price discovery must **either** use
compatible exchange timestamps on both sides (`require_leadlag(..., basis="exch")`), **or** carry
its own preregistration declaring a normalization for venue-specific receive-latency baselines.
That normalization may not be introduced inside this experiment.

## What this does not change

M0 as specified does not depend on a lead-lag term; section 6 defines the composite from flow,
volatility and book-pressure state. This clarification constrains how any lead-lag feature may be
*named and interpreted* if one is used, and forecloses the specific error of reporting collector
arrival order as market leadership.

## Audit chain

```
PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md   0973744b73651e82...   (frozen 2026-07-26, unchanged)
PREREG_BINANCE_V1_CLARIFICATION_001.md     <hashed in PREREG_HASH.txt>
PREREG_BINANCE_V1_CLARIFICATION_002.md     <this file, hashed in PREREG_HASH.txt>
```
