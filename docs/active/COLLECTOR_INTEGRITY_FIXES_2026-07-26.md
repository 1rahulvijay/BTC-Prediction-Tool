# Collector evidence-integrity fixes — 2026-07-26

**Status: implemented and tested. Required before Oracle deployment of the multi-venue collector.**

External review of pushed commit `8998d5b` found defects in the collector's evidence-qualification
path. Every one is **silent**: the process keeps running, the health report looks fine, and the
corruption only surfaces when a strategy is scored on data that was never valid. That is the worst
class of bug for an evidence run, because the `BINANCE_VOLATILITY_MOMENTUM_V1` preregistration
treats the collector's own qualification as ground truth.

All fixes are in `backend/venues/`. New regression suite: `test_collector_integrity.py`.

---

## Fixed

### D1 — a required Class-A stream was missing from the health gate

`EXPECTED` listed **8** streams; `bybit_perp/publicTrade` was absent. The preregistration names
Bybit public trades as **Class-A** input for trade imbalance and directional flow, so an episode
could report **8/8 healthy while a required strategy input was entirely missing.**

Required health is now **9/9**. The parser already emitted the stream, so this is a real gate
rather than a permanently-failing one.

### D2 — stale streams could still qualify

`max_ws_age_ms` and `max_rest_age_ms` were measured and written into `venue_episodes` — and then
never consulted by the qualification decision. An episode whose feeds were minutes stale counted
as evidence.

Both are now gating conditions, with limits **declared before any M0 score exists**:

| limit | value | rationale |
|---|---|---|
| `REST_MAX_AGE_MS` | 60,000 | the same 60s `venue_admissibility` already enforces for Class-B, so an episode cannot qualify on data a feature would refuse |
| `WS_MAX_AGE_MS` | 5,000 | observed steady state is tens of ms — ~100× looser than normal, so it excludes a wedged-but-"connected" socket, not jitter |

**Revising either after seeing an M0 result invalidates the experiment.**

### D3 — the evidence clock could start without any evidence

`flush()` called `mark_start()` *before* the insert, and cleared the buffer even when the insert
raised. That permitted the exact state an evidence run must never reach:

```text
collection_start_ts exists   (the clock appears to be running)
zero rows persisted          (and the dropped rows are gone)
```

Now: insert first; on failure **retain the buffer**, increment `writer_errors`, mark the episode
`writer_failed`, and **re-raise**. A persistent writer fault surfaces as a visible outage that
systemd restarts, rather than as silently thinned evidence. `mark_start()` runs only after a
confirmed successful insert.

### D4 — episode health counted parsed rows, not persisted rows

`ep_counts` advanced inside `add()`, before rows reached DuckDB. A failing writer therefore left an
episode looking fully healthy while nothing was stored.

Counters are now split: `ep_counts` (received/parsed, kept for diagnostics), `_pending_counts`
(parsed, awaiting commit), and **`ep_persisted`** — promoted only after a successful insert and the
**only** counter qualification consults.

### D5 — deduplication was scoped inside the lookback window

The most subtle of the set. The lookback filter sat **inside** the CTE, so `ROW_NUMBER()` ranked
only rows already within the window:

```sql
-- before: window applied, THEN identity resolved
WITH first_seen AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY venue, stream, event_key ORDER BY recv_ts) AS _rn
    FROM venue_events
    WHERE recv_ts <= ? AND recv_ts > ?        -- <-- lookback here
)

-- after: identity resolved over ALL history, THEN the window is applied
WITH canonical AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY venue, stream, event_key ORDER BY recv_ts) AS _rn
    FROM venue_events WHERE recv_ts <= ?
),
admissible AS (
    SELECT * FROM canonical
    WHERE (event_key IS NULL OR _rn = 1) AND recv_ts > ? AND ...
)
```

An event first observed 600s before a decision and re-polled 100s before it ranked `_rn = 1`
*within the window* and entered features as new — reintroducing the precise double-count the
`event_key` mechanism exists to prevent.

**Deliberate deviation from the review:** it suggested `ORDER BY recv_ts, process_start_id`. That
tiebreaker made the gate fail on any table lacking the column, and I was patching test fixtures one
at a time to satisfy it. It was **dropped**. The invariant is "the earliest observation wins"; a
`recv_ts` tie for the same `event_key` means two sightings at the same instant, and either is
equally valid as first — the tiebreaker bought no correctness while making the enforcement layer
brittle.

### D6 — REST identity could not distinguish a duplicate from a revision

Fixed earlier the same day, independently. `premiumIndex` / `openInterest` now key on
**instrument + publication timestamp + canonical payload hash**:

| case | behaviour |
|---|---|
| same ts + same hash | exact duplicate — one key, dedup keeps the **earliest** `recv_ts` |
| same ts + different hash | revision — distinct key, **both retained**, each usable only from its own `recv_ts` |

The revision policy is **causal visibility**, chosen over last-write-wins: a correction is never
back-dated onto the original's publication, and a decision made before the correction arrived
cannot see it. That is the only choice consistent with the module's receive-time discipline.

---

### D7 — REST `connection_id` was the poll counter

Every REST row stamped `connection_id = poll_id`, so a 1-second poll loop looked like a fresh
network connection once per second. That is not a cosmetic mislabel: connection generation is
**provenance**. It is how an analyst separates *"the session was rebuilt after a failure"* — which
resets `poll_id` and therefore produces a **poll-1 backlog** — from *"we polled again on a healthy
session"*. Conflated, every row looked backlog-adjacent and reconnect accounting was meaningless.

Now three distinct identifiers:

| field | meaning |
|---|---|
| `process_start_id` | which collector process observed the row |
| `connection_id` | REST **connection generation** — advances only when the HTTP session is actually rebuilt |
| `poll_id` | poll counter within the current generation; `poll_id <= 1` is backlog |

The generation advances only after **three consecutive** failed polls, so a transient timeout does
not manufacture fake reconnect churn. When it does advance the session is rebuilt and `poll_id`
resets to 0 — correct, because a rebuilt session re-polls history and its poll 1 is backlog again.
Previously the failure was swallowed by a bare `except: pass`, so the rebuild never happened at all.

### D8 — "four continuous weeks" is now mechanically enforced

The old check was:

```text
qualifying >= 1000  AND  wall_clock_span >= 4 weeks
```

A collector can satisfy both while being **broken for most of the window**. Demonstrated with a
test: three healthy blocks of 400 episodes separated by ~7-day outages gives **1,200 qualifying
episodes across 6 weeks of wall clock** — the old gate passes it, while the longest unbroken run is
**0.20 weeks**.

`continuity_report()` now computes what the preregistration actually asks for:

```text
longest_run_episodes / longest_run_weeks    unbroken consecutive qualifying episodes
coverage_pct                                qualifying episodes / episode slots in span
largest_gap_h, gaps                         outage geometry
gate / gate_reason                           MET only if BOTH count and CONTINUOUS run pass
```

Continuity is measured on the episode grid, so a missing **or excluded** episode breaks the run —
an outage in week three cannot be averaged away by healthy weeks either side.

> **Operational consequence you should decide on deliberately.** This is strict: a *single*
> 5-minute episode excluded for transient staleness resets a four-week accumulation (tested —
> one bad episode mid-run halves the longest run to 2.00w). That is what the frozen word
> "continuous" means, and the review was explicit that the requirement must not be silently
> reinterpreted. If operational tolerance is wanted (e.g. "a run survives ≤N isolated excluded
> episodes"), that is a **hashed pre-data clarification written before collection starts** — never
> a code change made after seeing how much data a run actually accumulated.

---

### D9 — writer-task failure could be swallowed by the supervisor

`asyncio.gather(..., return_exceptions=True)` allowed the collector to keep running after the
writer raised. The buffer was retained, but systemd never saw a process failure and therefore
could not restart the service. The main supervisor now propagates the first task failure after
cancelling sibling tasks. `register_session()`, `mark_start()` and episode persistence also fail
closed instead of converting a broken database into apparently healthy uptime.

### D10 — synchronous REST polling could stall every WebSocket parser

`requests.Session.get()` ran directly inside the async REST loop. One six-second timeout blocked
the collector event loop, delaying all WebSocket receive timestamps and manufacturing apparent
feed lag. REST HTTP calls now run through `asyncio.to_thread()`. REST provenance also uses the
`binance_perp_rest` connection generation rather than the unrelated perpetual-WebSocket generation.

### D11 — one fresh row followed by silence could qualify

The original age gate measured `recv_ts - exch_ts` only. That detects delayed events but does not
detect a dead stream: one low-latency row followed by four minutes of silence still looked fresh.
Episode health now includes inter-arrival and end-of-episode silence. An episode is excluded when
either a row is delayed beyond the declared limit or the stream stops producing within that limit.
Boundary rollover is performed before the first event of a new episode is added, so an event just
after a five-minute boundary cannot be credited to the previous episode.

## Still open

The reviewed silent-loss and stale-feed defects are closed, but this is not yet production
evidence. Before M0, deploy the collector, verify the 9/9 report, and accrue the preregistered four
continuous qualifying weeks. Sequence-gap and event-loop-latency diagnostics are retained in raw
provenance but are not yet independent episode-qualification columns; any later tightening must be
a hashed pre-data clarification, not a threshold selected after viewing M0.

---

## Tests

`backend/venues/test_collector_integrity.py` — regression assertions for every closed failure mode:

```text
PASS  bybit_perp/publicTrade is a REQUIRED stream (Class-A per the preregistration)
PASS  required health is 9/9, not 8/8
PASS  episode missing the Bybit trade stream is EXCLUDED
PASS  all streams present but REST STALE -> excluded        (rest_stale:60001ms>60000)
PASS  all streams present but WS STALE  -> excluded         (ws_stale:5001ms>5000)
PASS  healthy 9/9 episode within both age limits QUALIFIES  (gate is not vacuous)
PASS  one fresh row followed by an episode-long SILENCE is excluded
PASS  a failing insert RAISES rather than silently dropping evidence
PASS  the failed batch is RETAINED, not cleared
PASS  writer failure is recorded on the episode
PASS  collection_start_ts NOT created when the insert failed
PASS  episode with a writer failure is EXCLUDED
PASS  parsed-but-unpersisted rows do NOT make an episode healthy
PASS  a successful insert promotes pending counts to PERSISTED
PASS  pending counts are cleared after a good insert
PASS  collection_start_ts IS created once a row actually persisted
PASS  REST rows no longer stamp connection_id with poll_id
PASS  REST connection generation advances only on an actual session rebuild
PASS  a rebuilt session resets poll_id (its poll 1 is BACKLOG again)
PASS  writer-task exceptions are not swallowed by the collector supervisor
PASS  scenario satisfies the OLD count condition                (n=1,200)
PASS  >=1,000 qualifying spread across outages is REFUSED       (longest run 0.20w)
PASS  outage geometry reported                                  (gaps=2, largest=167h)
PASS  an unbroken 4-week run MEETS the gate                     (4.00w, coverage 100%)
PASS  a single excluded episode mid-run BREAKS the run          (longest 2.00w)
```

Plus, in `venue_admissibility --selftest`:

```text
PASS  re-poll of an event first seen OUTSIDE the lookback stays excluded (identity is global)
PASS  a genuinely new event inside the lookback is admitted
PASS  exactly one admissible row
```

Note the deliberate inclusion of a **positive** case ("healthy 9/9 qualifies"). A gate that
excludes everything is not a fix, and without that assertion these changes could tighten the
collector into never producing evidence at all.

### Full suite after the changes

```text
venue_admissibility.py        SELFTEST PASS
multi_venue_recorder.py       SELFTEST PASS
test_collector_integrity.py   PASS
executable_fill_engine.py     PASS
phold_challenger.py           PASS
head_health.py                PASS
paper_trading_integrity.py    PASS
```

The recorder's own selftest **failed first** on `healthy full window qualifies` — correctly: the
fixture populated `ep_counts` (parsed) while qualification now demands `ep_persisted`. The fixture
was updated to the new contract rather than the contract loosened to the fixture.

---

## Deployment consequence

`COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md` now requires `9/9` stream health. A deploy performed
against an older copy that expects `8/8` would treat a missing Bybit trade stream as healthy — the
exact defect D1 closes.

The evidence clock has **not** started. These fixes change what *counts* as a qualifying episode,
so any episodes recorded before them were qualified under the weaker rules and should not be mixed
into the frozen forward sample.
