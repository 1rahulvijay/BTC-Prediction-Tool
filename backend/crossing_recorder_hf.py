"""High-cadence forward crossing recorder: ~1s sampling, so 5s and 15s labels become resolvable.

WHY THIS EXISTS
    `CROSSING_HEADS_V1` found reversion genuinely predictable - AUC 0.6715 at 30s against a
    clock baseline of 0.5196. The 5s and 15s horizons could not be tested at all, because
    `round_state_snapshots` samples every ~15 seconds: 6 resolvable cases at 15s, none at 5s.
    Short horizons are where an executable edge would most plausibly live, and this is the only
    route to them.

WHAT IT DOES NOT DO
    It does not touch the serving path. It is a separate process with its own price poll and its
    own database, so starting or stopping it cannot change what the application does.

RECORDER HONESTY, BUILT IN
    This repository has a recorder that was wired, selftested and had NEVER RUN, and a readiness
    report that printed a healthy status while unable to read its source. So:

      - every poll writes a heartbeat, whether or not it produced a crossing;
      - a stall longer than MAX_GAP_MS writes an explicit GAP row rather than joining two
        observations across the hole as if they were adjacent;
      - a crossing detected across a gap is tagged `after_gap`, because a leader change that
        straddles missing data may have been several crossings.

THE ANCHOR IS A PROXY, AND IS LABELLED AS ONE
    A Polymarket round's official anchor is its settlement reference. This records the round's
    OPEN price on one venue and stores `price_source` alongside, so the proxy is explicit and
    auditable rather than implied.

    It is the OPEN, not "whatever arrived first". A round whose first observation lands more
    than ANCHOR_MAX_BOUNDARY_DELAY_MS after the boundary was joined mid-flight: its open is
    unknown, so it is recorded ANCHOR_UNAVAILABLE and produces NO crossings. Previously a
    restart at 04:32 made 04:32's price the "open" of the 04:30 round, and every crossing in
    that round measured against the wrong reference. Refusing a round costs one round; a
    wrong anchor silently corrupts all of them. `hf_round_anchors` records the decision either
    way, so "no crossings" and "never knew the anchor" are distinguishable in the data.

    python backend/crossing_recorder_hf.py --selftest
    python backend/crossing_recorder_hf.py --run --seconds 120
    python backend/crossing_recorder_hf.py --report
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DB = DATA_DIR / "polymarket_crossings_hf.duckdb"

POLL_MS = 1000
#: Two missed polls. Beyond this the series is not continuous and must say so.
MAX_GAP_MS = 3 * POLL_MS
#: How close to the round boundary the FIRST observation must land for its price to be
#: usable as that round's anchor. A round first seen later than this was joined mid-flight
#: and its true open is unknown - the previous behaviour silently used whatever price
#: happened to arrive first, so a restart at 04:32 made 04:32's price the "open" of the
#: 04:30 round and every later crossing measured against the wrong reference.
ANCHOR_MAX_BOUNDARY_DELAY_MS = 2 * POLL_MS
ANCHOR_UNAVAILABLE = "ANCHOR_UNAVAILABLE"
ROUND_MINUTES = (5, 15)
REVERSION_HORIZONS_S = (5, 15, 30, 60)
# v2 (2026-08-04), matching polymarket_crossing_recorder. v1 wrote `reverted_Ns` while
# computing state-at-horizon. This recorder exists to unlock the 5s/15s targets, so it
# must write the target the study actually needs - ever_reverted_by_Ns - not the misnamed
# one. Fresh table: this recorder has produced 2 rows total, so there is nothing to
# migrate and no v1 history worth preserving here.
LABEL_VERSION = "hf_crossing_labels_v2"

#: REST poll. The timestamp is taken LOCALLY, before the HTTP request, so the price is stamped
#: with a time that precedes it by one round trip. At a 30s horizon that is tolerable; at 5s it
#: is a material fraction of the horizon being measured, and every latency question - repricing
#: lag, round-open discovery, wait-regret - is a DIFFERENCE of timestamps, so a polled clock
#: does not add noise to those, it fabricates the quantity.
PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
PRICE_SOURCE = "binance_spot_ticker"

#: WebSocket aggTrade. Carries the exchange's own event time, so labels can be built on event
#: time with receive latency measured separately rather than baked in.
#: NOT bookTicker: spot bookTicker carries no event time at all (measured 2026-07-26 in
#: venues/multi_venue_recorder.py - only u,s,b,B,a,A). aggTrade carries E and T.
WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
WS_PRICE_SOURCE = "binance_spot_aggtrade_ws"

#: Which clock `crossing_ts` came from. Existing rows were recorded under LOCAL_POLL and are
#: NOT reinterpreted - the column makes the two populations separable rather than blending a
#: local clock and an exchange clock into one series.
TS_SOURCE_LOCAL = "LOCAL_POLL"
TS_SOURCE_EXCHANGE = "EXCHANGE_EVENT"

#: Continuous-flush cadence for the long-running service. State is durable within this bound,
#: so a crash costs at most this many seconds rather than the whole run.
FLUSH_EVERY_S = 30
#: A round is kept in memory until every horizon that could still produce a label has elapsed.
#: Past that it can never change, so retaining it is a leak - the previous code retained every
#: round for the life of the process, which is why only short runs were survivable.
PRUNE_MARGIN_MS = (max(REVERSION_HORIZONS_S) + 120) * 1000

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS hf_crossing_events (
        crossing_id    VARCHAR PRIMARY KEY,
        round_id       VARCHAR NOT NULL,
        horizon_min    INTEGER NOT NULL,
        crossing_ts    BIGINT  NOT NULL,
        from_side      VARCHAR NOT NULL,
        to_side        VARCHAR NOT NULL,
        seconds_left   INTEGER NOT NULL,
        anchor         DOUBLE  NOT NULL,
        price          DOUBLE  NOT NULL,
        move           DOUBLE  NOT NULL,
        crossing_index INTEGER NOT NULL,
        after_gap      BOOLEAN NOT NULL,
        price_source   VARCHAR NOT NULL,
        cadence_ms     INTEGER NOT NULL,
        recorded_ts    BIGINT  NOT NULL,
        crossing_recv_ts BIGINT,          -- local receive time; NULL for LOCAL_POLL rows
        ts_source        VARCHAR,         -- LOCAL_POLL | EXCHANGE_EVENT
        clock_skew_ms    BIGINT           -- recv - exch. NULL, never 0, when unknown
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_crossing_labels (
        crossing_id       VARCHAR NOT NULL,
        label_version     VARCHAR NOT NULL,
        eligible_after_ts BIGINT  NOT NULL,
        resolved_ts       BIGINT  NOT NULL,
        state_original_side_at_5s   BOOLEAN,
        state_original_side_at_15s  BOOLEAN,
        state_original_side_at_30s  BOOLEAN,
        state_original_side_at_60s  BOOLEAN,
        ever_reverted_by_5s         BOOLEAN,
        ever_reverted_by_15s        BOOLEAN,
        ever_reverted_by_30s        BOOLEAN,
        ever_reverted_by_60s        BOOLEAN,
        first_reversion_ts          BIGINT,
        n_recrossings               INTEGER,
        is_final_crossing BOOLEAN,
        PRIMARY KEY (crossing_id, label_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_heartbeats (
        beat_ts     BIGINT PRIMARY KEY,
        round_id    VARCHAR,
        price       DOUBLE,
        leader      VARCHAR,
        poll_ms     INTEGER,
        ok          BOOLEAN NOT NULL,
        note        VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_round_anchors (
        round_id          VARCHAR PRIMARY KEY,
        horizon_min       INTEGER,
        round_start_ts    BIGINT,
        anchor            DOUBLE,          -- NULL when unavailable
        anchor_quality    VARCHAR NOT NULL,-- OPEN_OBSERVED | ANCHOR_UNAVAILABLE
        boundary_delay_ms BIGINT  NOT NULL,
        anchor_source     VARCHAR NOT NULL,
        anchor_recv_ts    BIGINT  NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_gaps (
        gap_start_ts BIGINT PRIMARY KEY,
        gap_end_ts   BIGINT NOT NULL,
        gap_ms       BIGINT NOT NULL,
        reason       VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_runs (
        run_id        VARCHAR PRIMARY KEY,
        started_ts    BIGINT  NOT NULL,
        last_beat_ts  BIGINT,
        stopped_ts    BIGINT,             -- NULL while running or after a CRASH
        stop_reason   VARCHAR,            -- COMPLETED | SIGNAL | ERROR | NULL(crashed)
        price_source  VARCHAR NOT NULL,
        ts_source     VARCHAR NOT NULL,
        observations  BIGINT  DEFAULT 0,
        crossings     BIGINT  DEFAULT 0,
        flushes       BIGINT  DEFAULT 0,
        failures      BIGINT  DEFAULT 0,
        recovered_obligations INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_supervisor_events (
        event_ts    BIGINT  NOT NULL,
        event       VARCHAR NOT NULL,   -- START|SESSION_END|RESTART|GIVE_UP|STOP
        detail      VARCHAR,
        attempt     INTEGER NOT NULL,
        backoff_ms  BIGINT,
        PRIMARY KEY (event_ts, event)
    )
    """,
)

#: Columns added after the table shipped. CREATE TABLE IF NOT EXISTS does NOTHING to a table
#: that already exists, so a database written by an earlier build has none of these and any
#: query naming one fails to bind. Additive only - no existing row is read or rewritten.
_ADDED_EVENT_COLUMNS = (
    ("crossing_recv_ts", "BIGINT"),
    ("ts_source", "VARCHAR"),
    ("clock_skew_ms", "BIGINT"),
)


def _migrate(con) -> list[str]:
    """Add post-ship columns to an existing hf_crossing_events. Returns what it added."""
    existing = {row[1] for row in con.execute("PRAGMA table_info('hf_crossing_events')").fetchall()}
    added = []
    for name, sql_type in _ADDED_EVENT_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE hf_crossing_events ADD COLUMN {name} {sql_type}")
            added.append(name)
    return added


def round_id_for(ts_ms: int, minutes: int) -> tuple[str, int, int]:
    """(round_id, round_start_ms, seconds_left) for the round containing `ts_ms`.

    Rounds are deterministic wall-clock blocks, matching the `ptb_5m_<start_ms>` convention
    already used by `round_state_snapshots`."""
    span = minutes * 60_000
    start = (ts_ms // span) * span
    return f"ptb_{minutes}m_{start}", start, int((start + span - ts_ms) // 1000)


def leader_for(price: float, anchor: float) -> str:
    """Which side of the anchor price sits on. Exactly at the anchor is not a side."""
    if price > anchor:
        return "UP"
    if price < anchor:
        return "DOWN"
    return ""


def crossing_id(round_id: str, ts_ms: int) -> str:
    return hashlib.sha256(f"{round_id}|{int(ts_ms)}".encode()).hexdigest()[:32]


def fetch_price() -> float:
    with urllib.request.urlopen(PRICE_URL, timeout=5) as response:
        return float(json.load(response)["price"])


def connect(read_only: bool = False):
    import duckdb
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=read_only)
    if not read_only:
        for statement in SCHEMA:
            con.execute(statement)
        _migrate(con)
    return con


class Recorder:
    """Pure state machine. `record()` is driven by (timestamp, price); it never calls a clock.

    Keeping time and price OUT of the machine is what makes the selftest able to drive months
    of behaviour deterministically in milliseconds, and is why the gap and label logic can be
    asserted rather than hoped for."""

    def __init__(self, price_source: str = PRICE_SOURCE, cadence_ms: int = POLL_MS,
                 ts_source: str = TS_SOURCE_LOCAL):
        self.price_source = price_source
        self.cadence_ms = cadence_ms
        self.ts_source = ts_source
        self.anchors: dict[str, float | None] = {}
        self.anchor_quality: dict[str, dict] = {}
        self.unanchored: set[str] = set()
        self.leaders: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.samples: dict[str, list[tuple[int, str]]] = {}
        self.last_ts: int | None = None
        self.events: list[dict] = []
        self.gaps: list[dict] = []
        #: Written-through markers, so a continuous flush is incremental instead of rewriting
        #: the whole run every time. Without these, flushing every 30s over a week is O(n^2).
        self.flushed_events: set[str] = set()
        self.flushed_anchors: set[str] = set()
        self.flushed_gaps: set[int] = set()
        #: Events whose labels can no longer change. Kept so a pruned round is provably
        #: finished rather than merely old.
        self.final_ids: set[str] = set()
        self.pruned_rounds = 0
        self.recovered_obligations = 0

    def record(self, ts_ms: int, price: float, recv_ts: int | None = None) -> list[dict]:
        """One observation. Returns any crossings detected at this instant.

        `ts_ms` is the EVENT time - the exchange's own clock when the source provides one.
        `recv_ts` is when this process saw it. Keeping them separate is what makes receive
        latency a measurement rather than an unrecoverable distortion of the label."""
        gapped = False
        if self.last_ts is not None and ts_ms - self.last_ts > MAX_GAP_MS:
            self.gaps.append({"gap_start_ts": self.last_ts, "gap_end_ts": ts_ms,
                              "gap_ms": ts_ms - self.last_ts, "reason": "poll_stall"})
            gapped = True
        self.last_ts = ts_ms

        produced = []
        for minutes in ROUND_MINUTES:
            round_id, start, seconds_left = round_id_for(ts_ms, minutes)
            if round_id not in self.anchors:
                # The anchor is the round's OPEN. It is only knowable if this observation
                # is close enough to the boundary; otherwise the round was joined
                # mid-flight and is recorded as ANCHOR_UNAVAILABLE rather than anchored to
                # an arbitrary price. Refusing a round costs one round; a wrong anchor
                # silently corrupts every crossing in it.
                delay = ts_ms - start
                if delay <= ANCHOR_MAX_BOUNDARY_DELAY_MS:
                    self.anchors[round_id] = price
                    self.anchor_quality[round_id] = {
                        "quality": "OPEN_OBSERVED", "boundary_delay_ms": int(delay),
                        "source": self.price_source, "anchor_recv_ts": int(ts_ms)}
                else:
                    self.anchors[round_id] = None
                    self.anchor_quality[round_id] = {
                        "quality": ANCHOR_UNAVAILABLE, "boundary_delay_ms": int(delay),
                        "source": self.price_source, "anchor_recv_ts": int(ts_ms)}
                    self.unanchored.add(round_id)
                self.leaders[round_id] = ""
                self.counts[round_id] = 0
                self.samples[round_id] = []
            anchor = self.anchors[round_id]
            if anchor is None:
                continue                      # unanchored round produces NO crossings
            side = leader_for(price, anchor)
            self.samples[round_id].append((ts_ms, side))
            if not side:
                continue
            previous = self.leaders[round_id]
            if not previous:
                self.leaders[round_id] = side
                continue
            if side == previous:
                continue
            self.counts[round_id] += 1
            event = {
                "crossing_id": crossing_id(round_id, ts_ms), "round_id": round_id,
                "horizon_min": minutes, "crossing_ts": ts_ms, "from_side": previous,
                "to_side": side, "seconds_left": seconds_left, "anchor": anchor,
                "price": price, "move": price - anchor,
                "crossing_index": self.counts[round_id], "after_gap": gapped,
                "price_source": self.price_source, "cadence_ms": self.cadence_ms,
                "crossing_recv_ts": recv_ts, "ts_source": self.ts_source,
                # NULL, never 0, when either clock is unknown. Storing a missing timestamp as
                # zero is what made a sibling recorder report a skew of the entire Unix epoch.
                "clock_skew_ms": (int(recv_ts) - int(ts_ms)) if recv_ts is not None else None,
            }
            self.events.append(event)
            produced.append(event)
            self.leaders[round_id] = side
        return produced

    def resolve(self, now_ms: int) -> list[dict]:
        """Labels for every event whose horizons have elapsed. NULL where not yet knowable."""
        out = []
        for event in self.events:
            samples = self.samples.get(event["round_id"], [])
            after = [(ts, side) for ts, side in samples if ts > event["crossing_ts"]]
            round_end = event["crossing_ts"] + event["seconds_left"] * 1000
            row = {"crossing_id": event["crossing_id"], "label_version": LABEL_VERSION,
                   "eligible_after_ts": event["crossing_ts"] + REVERSION_HORIZONS_S[0] * 1000,
                   "is_final_crossing": None}
            row["first_reversion_ts"] = None
            row["n_recrossings"] = None
            for horizon in REVERSION_HORIZONS_S:
                row[f"state_original_side_at_{horizon}s"] = None
                row[f"ever_reverted_by_{horizon}s"] = None
                deadline = event["crossing_ts"] + horizon * 1000
                if now_ms < deadline or deadline > round_end:
                    continue
                pairs = [(ts, side) for ts, side in after if ts <= deadline and side]
                if not pairs:
                    continue
                # STATE AT HORIZON - what v1 computed.
                row[f"state_original_side_at_{horizon}s"] = (
                    pairs[-1][1] == event["from_side"])
                # EVER REVERTED - the target this recorder exists to make measurable at
                # 5s/15s. At 1s cadence a brief reversion is finally observable, which is
                # exactly what the 15s recorder could not see.
                touched = [ts for ts, side in pairs if side == event["from_side"]]
                row[f"ever_reverted_by_{horizon}s"] = bool(touched)
                if touched and row["first_reversion_ts"] is None:
                    row["first_reversion_ts"] = int(touched[0])
                if horizon == REVERSION_HORIZONS_S[-1]:
                    sides = [s for _, s in pairs]
                    row["n_recrossings"] = sum(
                        1 for a, b in zip(sides, sides[1:]) if a != b)
            if now_ms >= round_end:
                row["is_final_crossing"] = event["from_side"] not in {
                    side for _, side in after if side}
            # FINAL once the round has ended and every horizon that can still fall inside it
            # has elapsed. After this the row can never change, so it need not be re-emitted
            # and its round becomes prunable. Horizons past round_end are permanently NULL by
            # design, so waiting for them would keep every round alive forever.
            if now_ms >= round_end and now_ms >= event["crossing_ts"] + max(
                    h * 1000 for h in REVERSION_HORIZONS_S):
                self.final_ids.add(event["crossing_id"])
            if any(row[f"state_original_side_at_{h}s"] is not None
                   for h in REVERSION_HORIZONS_S) or row["is_final_crossing"] is not None:
                row["resolved_ts"] = now_ms
                out.append(row)
        return out

    def prune(self, now_ms: int) -> int:
        """Drop rounds that can no longer produce or change a label.

        A long-lived recorder that never forgets is a leak: `samples` holds every observation
        of every round for the life of the process. Pruning is only safe once nothing pending
        depends on the round, so the condition is conjunctive - past its horizons, AND every
        event in it flushed, AND every event in it final."""
        removed = 0
        for round_id in list(self.samples):
            minutes = int(round_id.split("_")[1].rstrip("m"))
            round_end = int(round_id.rsplit("_", 1)[1]) + minutes * 60_000
            if now_ms < round_end + PRUNE_MARGIN_MS:
                continue
            mine = [e for e in self.events if e["round_id"] == round_id]
            if any(e["crossing_id"] not in self.flushed_events
                   or e["crossing_id"] not in self.final_ids for e in mine):
                continue
            if round_id not in self.flushed_anchors:
                continue
            self.samples.pop(round_id, None)
            self.leaders.pop(round_id, None)
            self.counts.pop(round_id, None)
            self.anchors.pop(round_id, None)
            self.anchor_quality.pop(round_id, None)
            self.unanchored.discard(round_id)
            self.events = [e for e in self.events if e["round_id"] != round_id]
            removed += 1
        self.pruned_rounds += removed
        return removed

    def restore(self, con, now_ms: int) -> int:
        """Reload unfinished label obligations left by a previous process.

        A crossing recorded at 11:59:59 has horizons reaching into the next process's lifetime.
        Without this the obligation is silently abandoned: the event row exists forever with no
        label and nothing records that a label was ever owed. The samples that would have
        resolved it are gone, so this does NOT invent labels - it re-registers the obligation so
        the new process's own samples can complete what they can, and so an abandoned obligation
        is visible as an obligation rather than as an absence."""
        # ANCHORS FIRST. A round already open when the previous process died has a durable,
        # boundary-verified anchor in hf_round_anchors. Re-adopting it is what makes a restart
        # cost seconds instead of a whole round: without it every restart lands mid-round, is
        # correctly refused an anchor, and forfeits up to 15 minutes. Only rounds still running
        # are adopted - a finished round's anchor can change nothing.
        for round_id, anchor, quality, delay, source, recv in con.execute(
                "SELECT round_id, anchor, anchor_quality, boundary_delay_ms, anchor_source,"
                "       anchor_recv_ts FROM hf_round_anchors").fetchall():
            minutes = int(round_id.split("_")[1].rstrip("m"))
            round_end = int(round_id.rsplit("_", 1)[1]) + minutes * 60_000
            if now_ms >= round_end:
                continue
            self.anchors[round_id] = anchor
            self.anchor_quality[round_id] = {
                "quality": quality, "boundary_delay_ms": delay,
                "source": source, "anchor_recv_ts": recv}
            self.flushed_anchors.add(round_id)
            self.leaders.setdefault(round_id, "")
            self.counts.setdefault(round_id, 0)
            self.samples.setdefault(round_id, [])
            if anchor is None:
                self.unanchored.add(round_id)

        rows = con.execute(
            "SELECT e.crossing_id, e.round_id, e.horizon_min, e.crossing_ts, e.from_side,"
            "       e.to_side, e.seconds_left, e.anchor, e.price, e.move, e.crossing_index,"
            "       e.after_gap, e.price_source, e.cadence_ms"
            "  FROM hf_crossing_events e"
            "  LEFT JOIN hf_crossing_labels l"
            "    ON l.crossing_id = e.crossing_id AND l.label_version = ?"
            " WHERE l.crossing_id IS NULL OR l.is_final_crossing IS NULL"
            " ORDER BY e.crossing_ts",
            [LABEL_VERSION]).fetchall()
        restored = 0
        for r in rows:
            round_end = int(r[3]) + int(r[6]) * 1000
            if now_ms >= round_end + PRUNE_MARGIN_MS:
                continue                      # unresolvable now; its samples are long gone
            event = {"crossing_id": r[0], "round_id": r[1], "horizon_min": r[2],
                     "crossing_ts": r[3], "from_side": r[4], "to_side": r[5],
                     "seconds_left": r[6], "anchor": r[7], "price": r[8], "move": r[9],
                     "crossing_index": r[10], "after_gap": r[11], "price_source": r[12],
                     "cadence_ms": r[13], "crossing_recv_ts": None,
                     "ts_source": self.ts_source, "clock_skew_ms": None}
            self.events.append(event)
            self.flushed_events.add(r[0])     # already durable; do not rewrite it
            self.samples.setdefault(r[1], [])
            # Continue the round's crossing sequence rather than restarting it at 1, and adopt
            # the last known leader so the first post-restart observation is compared against
            # where the round actually was, not against a blank slate that would manufacture a
            # crossing out of the restart itself.
            self.counts[r[1]] = max(self.counts.get(r[1], 0), int(r[10]))
            if r[1] in self.anchors and self.anchors[r[1]] is not None:
                self.leaders[r[1]] = r[5]
            restored += 1
        self.recovered_obligations = restored
        return restored


def flush(recorder: Recorder, now_ms: int, con=None) -> tuple[int, int, int]:
    """Write everything new since the last call. INCREMENTAL by design.

    The original rewrote every anchor and re-checked every event on each call. That was fine
    for a 120-second run and quadratic for a week-long one, which is a large part of why this
    recorder could only ever be run in short bursts.

    Pass `con` to reuse a connection; DuckDB is single-writer and a long-running service that
    opens and closes per flush spends most of its life acquiring the file lock."""
    owned = con is None
    con = con or connect()
    try:
        # Anchor provenance FIRST, so a reader can always tell which rounds were usable -
        # including the ones that produced no crossings BECAUSE they were unanchored.
        # Without this, "no crossings this round" and "we never knew the anchor" look
        # identical in the data.
        for round_id, meta in list(recorder.anchor_quality.items()):
            if round_id in recorder.flushed_anchors:
                continue
            minutes = int(round_id.split("_")[1].rstrip("m"))
            start = int(round_id.rsplit("_", 1)[1])
            con.execute(
                "INSERT OR REPLACE INTO hf_round_anchors ("
                " round_id, horizon_min, round_start_ts, anchor, anchor_quality,"
                " boundary_delay_ms, anchor_source, anchor_recv_ts"
                ") VALUES (?,?,?,?,?,?,?,?)",
                [round_id, minutes, start, recorder.anchors.get(round_id),
                 meta["quality"], meta["boundary_delay_ms"], meta["source"],
                 meta["anchor_recv_ts"]])
            recorder.flushed_anchors.add(round_id)
        events = 0
        for event in list(recorder.events):
            if event["crossing_id"] in recorder.flushed_events:
                continue
            # EXPLICIT columns. The positional form broke the moment the schema grew in the
            # sibling recorder, and this table has now grown too.
            con.execute(
                "INSERT OR REPLACE INTO hf_crossing_events ("
                " crossing_id, round_id, horizon_min, crossing_ts, from_side, to_side,"
                " seconds_left, anchor, price, move, crossing_index, after_gap, price_source,"
                " cadence_ms, recorded_ts, crossing_recv_ts, ts_source, clock_skew_ms"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [event["crossing_id"], event["round_id"], event["horizon_min"],
                 event["crossing_ts"], event["from_side"], event["to_side"],
                 event["seconds_left"], event["anchor"], event["price"],
                 event["move"], event["crossing_index"], event["after_gap"],
                 event["price_source"], event["cadence_ms"], now_ms,
                 event.get("crossing_recv_ts"), event.get("ts_source"),
                 event.get("clock_skew_ms")])
            recorder.flushed_events.add(event["crossing_id"])
            events += 1
        labels = 0
        for row in recorder.resolve(now_ms):
            # EXPLICIT columns. The positional form here broke the moment the schema grew
            # in the main recorder; the same trap applies to this table.
            con.execute(
                "INSERT OR REPLACE INTO hf_crossing_labels ("
                " crossing_id, label_version, eligible_after_ts, resolved_ts,"
                " state_original_side_at_5s, state_original_side_at_15s,"
                " state_original_side_at_30s, state_original_side_at_60s,"
                " ever_reverted_by_5s, ever_reverted_by_15s,"
                " ever_reverted_by_30s, ever_reverted_by_60s,"
                " first_reversion_ts, n_recrossings, is_final_crossing"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [row["crossing_id"], row["label_version"], row["eligible_after_ts"],
                 row["resolved_ts"],
                 row.get("state_original_side_at_5s"), row.get("state_original_side_at_15s"),
                 row.get("state_original_side_at_30s"), row.get("state_original_side_at_60s"),
                 row.get("ever_reverted_by_5s"), row.get("ever_reverted_by_15s"),
                 row.get("ever_reverted_by_30s"), row.get("ever_reverted_by_60s"),
                 row.get("first_reversion_ts"), row.get("n_recrossings"),
                 row["is_final_crossing"]])
            labels += 1
        gaps = 0
        for gap in list(recorder.gaps):
            if gap["gap_start_ts"] in recorder.flushed_gaps:
                continue
            con.execute("INSERT OR REPLACE INTO hf_gaps VALUES (?,?,?,?)",
                        [gap["gap_start_ts"], gap["gap_end_ts"], gap["gap_ms"], gap["reason"]])
            recorder.flushed_gaps.add(gap["gap_start_ts"])
            gaps += 1
        return events, labels, gaps
    finally:
        if owned:
            con.close()


def heartbeat(beat_ts: int, round_id: str, price: float | None, leader: str,
              poll_ms: int, ok: bool, note: str = "", con=None) -> None:
    """Pass `con` in the service loop. Opening a DuckDB connection per beat means a
    connect/close every second for the life of the process, on a single-writer file."""
    owned = con is None
    con = con or connect()
    try:
        con.execute("INSERT OR REPLACE INTO hf_heartbeats VALUES (?,?,?,?,?,?,?)",
                    [beat_ts, round_id, price, leader, poll_ms, ok, note])
    finally:
        if owned:
            con.close()


class Service:
    """The durable side of the recorder: identity, periodic flush, pruning, run accounting.

    Separated from `Recorder` so the state machine stays a pure function of (time, price) and
    remains drivable by the selftest without a database or a clock."""

    def __init__(self, recorder: Recorder, con, run_id: str):
        self.recorder = recorder
        self.con = con
        self.run_id = run_id
        self.observations = 0
        self.failures = 0
        self.crossings = 0
        self.flushes = 0
        self.last_flush = 0.0
        self.last_beat_ts = 0

    def open_run(self, now_ms: int) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO hf_runs (run_id, started_ts, price_source, ts_source,"
            " recovered_obligations) VALUES (?,?,?,?,?)",
            [self.run_id, now_ms, self.recorder.price_source, self.recorder.ts_source,
             self.recorder.recovered_obligations])

    def observe(self, exch_ts: int, price: float, recv_ts: int, latency_ms: int) -> None:
        produced = self.recorder.record(exch_ts, price, recv_ts=recv_ts)
        self.observations += 1
        self.crossings += len(produced)
        round_id, _, _ = round_id_for(exch_ts, ROUND_MINUTES[0])
        # One heartbeat per second of WALL time, not per message: aggTrade can deliver many
        # messages a second, and a heartbeat table growing at trade rate is not a health
        # signal, it is a second copy of the tape.
        if recv_ts - self.last_beat_ts >= POLL_MS:
            heartbeat(recv_ts, round_id, price, self.recorder.leaders.get(round_id, ""),
                      latency_ms, True, con=self.con)
            self.last_beat_ts = recv_ts
        for event in produced:
            print(f"    CROSSING {event['round_id']} {event['from_side']}->"
                  f"{event['to_side']} at {event['seconds_left']}s left, "
                  f"move {event['move']:+.2f}", flush=True)

    def fail(self, note: str, recv_ts: int) -> None:
        self.failures += 1
        heartbeat(recv_ts, "", None, "", 0, False, note[:120], con=self.con)

    def maybe_flush(self, now_ms: int, force: bool = False) -> None:
        if not force and time.time() - self.last_flush < FLUSH_EVERY_S:
            return
        events, labels, gaps = flush(self.recorder, now_ms, con=self.con)
        pruned = self.recorder.prune(now_ms)
        self.flushes += 1
        self.last_flush = time.time()
        self.con.execute(
            "UPDATE hf_runs SET last_beat_ts = ?, observations = ?, crossings = ?,"
            " flushes = ?, failures = ? WHERE run_id = ?",
            [now_ms, self.observations, self.crossings, self.flushes, self.failures,
             self.run_id])
        if events or labels or gaps or pruned:
            print(f"    flush +{events}ev +{labels}lab +{gaps}gap -{pruned}rounds "
                  f"(live rounds {len(self.recorder.samples)})", flush=True)

    def close_run(self, now_ms: int, reason: str) -> None:
        self.maybe_flush(now_ms, force=True)
        self.con.execute(
            "UPDATE hf_runs SET stopped_ts = ?, stop_reason = ? WHERE run_id = ?",
            [now_ms, reason, self.run_id])


def _make_service(use_ws: bool) -> tuple[Recorder, Service, object]:
    recorder = Recorder(
        price_source=WS_PRICE_SOURCE if use_ws else PRICE_SOURCE,
        cadence_ms=POLL_MS,
        ts_source=TS_SOURCE_EXCHANGE if use_ws else TS_SOURCE_LOCAL)
    con = connect()
    now_ms = int(time.time() * 1000)
    restored = recorder.restore(con, now_ms)
    run_id = hashlib.sha256(f"{now_ms}|{os.getpid()}".encode()).hexdigest()[:16]
    service = Service(recorder, con, run_id)
    service.open_run(now_ms)
    if restored:
        print(f"  recovered {restored} unresolved label obligation(s) from a previous run")
    return recorder, service, con


def run_rest(seconds: int | None) -> int:
    """REST polling. Kept for environments without an outbound WebSocket, and explicitly
    tagged LOCAL_POLL so its rows are never mixed with event-time rows."""
    recorder, service, con = _make_service(use_ws=False)
    deadline = None if seconds is None else time.time() + seconds
    stop = _install_stop_handler()
    print(f"  REST poll ~{POLL_MS}ms, source {PRICE_SOURCE}, ts_source {TS_SOURCE_LOCAL}")
    try:
        while not stop() and (deadline is None or time.time() < deadline):
            started = time.time()
            ts_ms = int(started * 1000)
            try:
                price = fetch_price()
                service.observe(ts_ms, price, ts_ms, int((time.time() - started) * 1000))
            except Exception as exc:
                service.fail(str(exc), ts_ms)
            service.maybe_flush(int(time.time() * 1000))
            time.sleep(max(0.0, POLL_MS / 1000 - (time.time() - started)))
        service.close_run(int(time.time() * 1000), "SIGNAL" if stop() else "COMPLETED")
    except BaseException as exc:                       # noqa: BLE001 - recorded, then re-raised
        service.close_run(int(time.time() * 1000), f"ERROR:{type(exc).__name__}")
        raise
    finally:
        con.close()
    _summarise(service)
    return 0


async def _ws_loop(service: Service, stop, deadline: float | None) -> str:
    import websockets

    backoff = 1.0
    while not stop() and (deadline is None or time.time() < deadline):
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20,
                                          close_timeout=5) as ws:
                print(f"  connected {WS_URL}", flush=True)
                backoff = 1.0
                while not stop() and (deadline is None or time.time() < deadline):
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    recv_ts = int(time.time() * 1000)
                    payload = json.loads(raw)
                    # aggTrade: T = trade time, E = event time. T is the market event; prefer
                    # it. A missing timestamp is NOT defaulted to 0 or to recv_ts - the
                    # message is refused, because a fabricated event time is worse than a
                    # dropped message for every latency measurement downstream.
                    exch = payload.get("T") or payload.get("E")
                    if exch is None:
                        service.fail("aggTrade without T or E", recv_ts)
                        continue
                    exch_ts = int(exch)
                    service.observe(exch_ts, float(payload["p"]), recv_ts,
                                    recv_ts - exch_ts)
                    service.maybe_flush(recv_ts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                       # noqa: BLE001
            service.fail(f"ws: {exc}", int(time.time() * 1000))
            # A reconnect is a real hole in the series. The gap is detected by the state
            # machine on the next observation via MAX_GAP_MS, so it is recorded rather than
            # stitched over.
            print(f"  ws error ({exc}); reconnecting in {backoff:.0f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
    return "SIGNAL" if stop() else "COMPLETED"


def run_ws(seconds: int | None) -> int:
    recorder, service, con = _make_service(use_ws=True)
    stop = _install_stop_handler()
    deadline = None if seconds is None else time.time() + seconds
    print(f"  WS stream, source {WS_PRICE_SOURCE}, ts_source {TS_SOURCE_EXCHANGE}")
    try:
        reason = asyncio.run(_ws_loop(service, stop, deadline))
        service.close_run(int(time.time() * 1000), reason)
    except KeyboardInterrupt:
        service.close_run(int(time.time() * 1000), "SIGNAL")
    except BaseException as exc:                       # noqa: BLE001
        service.close_run(int(time.time() * 1000), f"ERROR:{type(exc).__name__}")
        raise
    finally:
        con.close()
    _summarise(service)
    return 0


def _install_stop_handler():
    """Ctrl-C and SIGTERM must reach the flush path, not kill the process mid-run."""
    flag = {"stop": False}

    def handler(_signum, _frame):
        flag["stop"] = True
        print("\n  stop requested - flushing before exit", flush=True)

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass                                   # not the main thread; caller handles it
    return lambda: flag["stop"]


def _summarise(service: Service) -> None:
    r = service.recorder
    print(f"  {service.observations} observations ({service.failures} failed), "
          f"{service.crossings} crossings, {service.flushes} flushes, "
          f"{r.pruned_rounds} rounds pruned")
    print(f"  run_id {service.run_id} -> {DB.name}")


#: Restart backoff. A WS disconnect at 03:00 must not end a three-week run, and a permanently
#: broken config must not spin at full speed forever.
BACKOFF_MS = (1_000, 5_000, 15_000, 60_000, 300_000)
#: Consecutive failed sessions that produced NO observation at all. A session that records
#: something before dying is a transient fault; one that records nothing repeatedly is a broken
#: setup, and retrying it forever would burn the machine while looking busy.
MAX_BARREN_ATTEMPTS = 6
#: A recorder is healthy only if rows are ADVANCING. "The process is alive" is the question this
#: repository already got wrong once: binance_l2_recorder is wired, its selftests pass, and it
#: has produced nothing.
STALL_AFTER_MS = 120_000


def _log_supervisor(event, detail, attempt, backoff_ms):
    try:
        con = connect()
        try:
            con.execute("INSERT OR REPLACE INTO hf_supervisor_events VALUES (?,?,?,?,?)",
                        [int(time.time() * 1000), event, str(detail)[:200], attempt, backoff_ms])
        finally:
            con.close()
    except Exception:
        pass                      # supervision must never be the thing that kills the run


def _observation_count():
    try:
        con = connect(read_only=True)
        try:
            return int(con.execute("SELECT count(*) FROM hf_heartbeats").fetchone()[0])
        finally:
            con.close()
    except Exception:
        return 0


def supervise(seconds, use_ws=True, session=None, sleeper=None, now=None, counter=None):
    """Run sessions until stopped, restarting on failure with bounded backoff.

    `session`, `sleeper`, `now` and `counter` are injected so the selftest can drive weeks of
    restart behaviour deterministically, with no network and no clock.

    A session that returns ends the service - that means a deadline or a stop signal. A session
    that RAISES is a fault: recorded, backed off, retried. The alternative is that one
    disconnect at 03:00 silently ends a collection meant to run for weeks, which is exactly how
    this repository ended up with recorders that were wired and had never produced a row."""
    session = session or (lambda remaining: run(remaining, use_ws=use_ws))
    sleeper = sleeper or time.sleep
    now = now or time.time
    counter = counter or _observation_count
    started = now()
    attempt = 0
    barren = 0
    _log_supervisor("START", "seconds=%s ws=%s" % (seconds, use_ws), 0, None)
    while True:
        remaining = None if seconds is None else max(0, int(seconds - (now() - started)))
        if remaining is not None and remaining <= 0:
            _log_supervisor("STOP", "deadline reached", attempt, None)
            return 0
        before = counter()
        try:
            session(remaining)
            _log_supervisor("SESSION_END", "clean", attempt, None)
            return 0
        except KeyboardInterrupt:
            _log_supervisor("STOP", "SIGNAL", attempt, None)
            return 0
        except BaseException as exc:                          # noqa: BLE001
            produced = counter() - before
            barren = 0 if produced > 0 else barren + 1
            attempt += 1
            if barren >= MAX_BARREN_ATTEMPTS:
                _log_supervisor("GIVE_UP", "%s: %s" % (type(exc).__name__, exc), attempt, None)
                print("  GIVE UP after %d consecutive sessions that recorded NOTHING - a "
                      "broken setup, not a transient fault: %s" % (barren, exc))
                return 1
            backoff = BACKOFF_MS[min(attempt - 1, len(BACKOFF_MS) - 1)]
            _log_supervisor("RESTART", "%s: %s" % (type(exc).__name__, exc), attempt, backoff)
            print("  session failed (%s: %s); recorded %d observations; restarting in %.0fs "
                  "(attempt %d)" % (type(exc).__name__, str(exc)[:70], produced,
                                    backoff / 1000, attempt))
            sleeper(backoff / 1000.0)


def health(now_ms=None):
    """ADVANCING / STALLED / NEVER_RAN, from ROW PROGRESS rather than process liveness."""
    if not DB.is_file():
        return {"status": "NEVER_RAN", "reason": "%s does not exist" % DB.name}
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    con = connect(read_only=True)
    try:
        beats, last_beat, failed = con.execute(
            "SELECT count(*), max(beat_ts), count(*) FILTER (WHERE NOT ok) "
            "FROM hf_heartbeats").fetchone()
        events = con.execute("SELECT count(*) FROM hf_crossing_events").fetchone()[0]
        try:
            restarts = con.execute(
                "SELECT count(*) FROM hf_supervisor_events WHERE event = 'RESTART'"
            ).fetchone()[0]
        except Exception:
            restarts = 0
        crashed = con.execute(
            "SELECT count(*) FROM hf_runs WHERE stopped_ts IS NULL").fetchone()[0]
    finally:
        con.close()
    if not beats or last_beat is None:
        return {"status": "NEVER_RAN", "reason": "no heartbeat has ever been written"}
    age = now_ms - int(last_beat)
    status = "ADVANCING" if age <= STALL_AFTER_MS else "STALLED"
    reason = "last write %.0fs ago" % (age / 1000)
    if status == "STALLED":
        reason += " (> %.0fs stall threshold)" % (STALL_AFTER_MS / 1000)
    return {"status": status, "last_write_age_ms": age, "heartbeats": int(beats),
            "failed_beats": int(failed), "crossings": int(events),
            "restarts": int(restarts), "runs_without_clean_stop": int(crashed),
            "reason": reason}


def run(seconds: int | None, use_ws: bool = True) -> int:
    return run_ws(seconds) if use_ws else run_rest(seconds)


def report() -> int:
    if not DB.is_file():
        print(f"  {DB} does not exist - nothing has been recorded yet")
        return 1
    con = connect(read_only=True)
    try:
        beats, first, last, failed = con.execute(
            "SELECT count(*), min(beat_ts), max(beat_ts), count(*) FILTER (WHERE NOT ok) "
            "FROM hf_heartbeats").fetchone()
        events = con.execute("SELECT count(*) FROM hf_crossing_events").fetchone()[0]
        gaps = con.execute("SELECT count(*), coalesce(max(gap_ms),0) FROM hf_gaps").fetchone()
        resolved = {}
        for horizon in REVERSION_HORIZONS_S:
            column = f"state_original_side_at_{horizon}s"
            resolved[horizon] = con.execute(
                f"SELECT count(*) FILTER (WHERE {column}), count(*) FROM hf_crossing_labels "
                f"WHERE {column} IS NOT NULL").fetchone()
    finally:
        con.close()
    import datetime as dt
    fmt = lambda ms: (dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
                      if ms else "-")
    print(f"  heartbeats {beats:,} ({failed} failed)   {fmt(first)} -> {fmt(last)}")
    print(f"  crossings {events:,}   gaps {gaps[0]} (longest {gaps[1]} ms)")
    for horizon, (reverted, total) in resolved.items():
        if total:
            print(f"  reverted within {horizon:>2}s: {reverted:,}/{total:,} "
                  f"({reverted / total:.1%})")
        else:
            print(f"  reverted within {horizon:>2}s: none resolvable yet")
    return 0


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    # An exact 15m boundary, which is necessarily also a 5m one. A 5m-only boundary would
    # join the containing 15m round mid-flight, and that round is now correctly refused an
    # anchor - so the fixture must open BOTH horizons cleanly to exercise crossings at all.
    base = 1_785_000_000_000 // 900_000 * 900_000

    rid, start, left = round_id_for(base + 60_000, 5)
    check(rid == f"ptb_5m_{base}" and start == base and left == 240,
          "round identity and time-remaining follow the wall clock deterministically")
    check(round_id_for(base + 299_999, 5)[2] == 0, "the final second of a round has 0s left")
    check(round_id_for(base + 300_000, 5)[0] != rid, "the next block is a different round")

    check(leader_for(101, 100) == "UP" and leader_for(99, 100) == "DOWN",
          "the leader is the side of the anchor")
    check(leader_for(100, 100) == "", "exactly AT the anchor is not a side")

    # ANCHOR PROVENANCE. The old rule made the first price the recorder happened to see
    # into the round's "open", so a restart at 04:32 anchored the 04:30 round to 04:32's
    # price and every crossing in it measured against the wrong reference.
    mid = Recorder(cadence_ms=1000)
    mid.record(base + 90_000, 100.0)                     # join a round 90s late
    rid, _, _ = round_id_for(base + 90_000, 5)
    check(mid.anchor_quality[rid]["quality"] == ANCHOR_UNAVAILABLE,
          "a round joined mid-flight is ANCHOR_UNAVAILABLE, not anchored to a stray price")
    check(mid.anchors[rid] is None, "and carries no anchor value at all")
    # A SEQUENCE, not one observation. A single call proves almost nothing: if the refusal were
    # replaced by "anchor = price", that one price would sit exactly ON its own anchor, produce
    # no side, and return [] anyway - so the mutation would pass a single-call check. Driving
    # the price back and forth across the would-be anchor is what makes the check able to fail.
    invented = []
    for offset, price in ((91_000, 200.0), (92_000, 50.0), (93_000, 300.0),
                          (94_000, 25.0), (95_000, 400.0)):
        invented += mid.record(base + offset, price)
    check(invented == [],
          "an unanchored round produces NO crossings across a whipsawing price - a wrong "
          "anchor would have invented one on every reversal")
    check(mid.samples.get(rid) in (None, []),
          "and it accumulates no samples, so nothing downstream can resolve against it")
    check(mid.anchor_quality[rid]["boundary_delay_ms"] == 90_000,
          "the boundary delay is recorded, so the refusal is auditable")

    clean = Recorder(cadence_ms=1000)
    clean.record(base, 100.0)
    rid2, _, _ = round_id_for(base, 5)
    check(clean.anchor_quality[rid2]["quality"] == "OPEN_OBSERVED",
          "a round opened at its boundary IS anchored")
    check(clean.anchors[rid2] == 100.0, "and the anchor is the OPEN price, not a later one")

    r = Recorder(cadence_ms=1000)
    check(r.record(base, 100.0) == [], "the first observation of a round is never a crossing")
    check(r.record(base + 1000, 101.0) == [], "establishing the first leader is not a crossing")
    check(r.record(base + 2000, 102.0) == [], "staying on one side is not a crossing")
    crossed = r.record(base + 3000, 99.0)
    check(len(crossed) == 2,
          "a flip produces one crossing for EACH round horizon (5m and 15m)")
    five = [e for e in crossed if e["horizon_min"] == 5][0]
    check(five["from_side"] == "UP" and five["to_side"] == "DOWN",
          "the crossing records the side it came from and went to")
    check(five["move"] == -1.0 and five["anchor"] == 100.0,
          "the anchor and signed move at the crossing are preserved")
    check(not five["after_gap"], "a continuously sampled crossing is not tagged after_gap")

    # GAPS. A stall must be recorded, and a crossing spanning it must be tagged.
    g = Recorder()
    g.record(base, 100.0)
    g.record(base + 1000, 101.0)
    produced = g.record(base + 30_000, 99.0)
    check(len(g.gaps) == 1 and g.gaps[0]["gap_ms"] == 29_000,
          "a poll stall writes an explicit GAP rather than joining across the hole")
    check(all(e["after_gap"] for e in produced),
          "a crossing detected across a gap is tagged - it may have been several crossings")

    # LABELS at the horizons this recorder exists to unlock.
    lr = Recorder()
    lr.record(base, 100.0)
    lr.record(base + 1000, 101.0)
    lr.record(base + 2000, 99.0)                       # crossing UP -> DOWN
    for offset in range(3000, 70_000, 1000):
        lr.record(base + offset, 99.0)                 # stays DOWN
    event = lr.events[0]
    check(lr.resolve(now_ms=event["crossing_ts"] + 1000) == [],
          "one second after a crossing NOTHING is resolvable and no row is produced")
    got = lr.resolve(now_ms=event["crossing_ts"] + 6000)[0]
    check(got["state_original_side_at_5s"] is False,
          "THE POINT OF THIS RECORDER: the 5s horizon IS resolvable at 1s cadence")
    check(got["state_original_side_at_60s"] is None,
          "an unelapsed horizon stays NULL - never a default False")
    late = lr.resolve(now_ms=event["crossing_ts"] + 70_000)[0]
    check(late["state_original_side_at_15s"] is False
          and late["state_original_side_at_30s"] is False,
          "15s and 30s resolve too - all four horizons are reachable")

    rv = Recorder()
    rv.record(base, 100.0)
    rv.record(base + 1000, 101.0)
    rv.record(base + 2000, 99.0)
    for offset in range(3000, 12_000, 1000):
        rv.record(base + offset, 101.5)                # returns to UP
    reverted = rv.resolve(now_ms=base + 12_000)[0]
    check(reverted["state_original_side_at_5s"] is True
          and reverted["ever_reverted_by_5s"] is True,
          "a leader that returns to the original side within 5s IS recorded as reverted")

    check(crossing_id("r", 1) == crossing_id("r", 1)
          and crossing_id("r", 1) != crossing_id("r", 2),
          "crossing identity is stable and time-specific")
    check(MAX_GAP_MS > POLL_MS,
          "the gap threshold exceeds one poll interval, so normal jitter is not a gap")

    # EVENT TIME vs RECEIVE TIME. The two clocks must stay separable, and an unknown skew must
    # be NULL rather than 0 - a sibling recorder stored a missing timestamp as 0.0 and read a
    # skew of the entire Unix epoch.
    ts = Recorder(ts_source=TS_SOURCE_EXCHANGE)
    ts.record(base, 100.0, recv_ts=base + 250)
    ts.record(base + 1000, 101.0, recv_ts=base + 1180)
    ev = ts.record(base + 2000, 99.0, recv_ts=base + 2140)[0]
    check(ev["crossing_ts"] == base + 2000 and ev["crossing_recv_ts"] == base + 2140,
          "event time and receive time are both recorded, and are different columns")
    check(ev["clock_skew_ms"] == 140, "skew is receive minus event, measured not assumed")
    back = Recorder(ts_source=TS_SOURCE_EXCHANGE)
    back.record(base, 100.0, recv_ts=base + 100)
    back.record(base + 1000, 101.0, recv_ts=base + 1100)
    dropped = back.record(base + 2000, 99.0, recv_ts=None)[0]
    check(dropped["crossing_recv_ts"] is None and dropped["clock_skew_ms"] is None,
          "a missing receive time stays NULL in BOTH columns - never back-filled from the "
          "event clock, which would report zero latency for an unmeasured message")
    noskew = Recorder()
    noskew.record(base, 100.0)
    noskew.record(base + 1000, 101.0)
    ev2 = noskew.record(base + 2000, 99.0)[0]
    check(ev2["clock_skew_ms"] is None,
          "with no receive time the skew is NULL, never 0 - 0 would read as a perfect clock")
    check(ev2["ts_source"] == TS_SOURCE_LOCAL and ev["ts_source"] == TS_SOURCE_EXCHANGE,
          "each row records WHICH clock produced it, so the populations never blend")

    # --- Durable behaviour. These need a database, so they run against a temp file. ---
    import tempfile

    global DB
    original_db = DB
    tmpdir = tempfile.mkdtemp(prefix="hf_selftest_")
    try:
        DB = Path(tmpdir) / "hf.duckdb"

        # MIGRATION. CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
        # so a database written by the previous build must be ALTERed or every query naming a
        # new column fails to bind. Simulated by creating the OLD shape first.
        import duckdb

        old = duckdb.connect(str(DB))
        old.execute("""CREATE TABLE hf_crossing_events (
            crossing_id VARCHAR PRIMARY KEY, round_id VARCHAR NOT NULL,
            horizon_min INTEGER NOT NULL, crossing_ts BIGINT NOT NULL,
            from_side VARCHAR NOT NULL, to_side VARCHAR NOT NULL,
            seconds_left INTEGER NOT NULL, anchor DOUBLE NOT NULL, price DOUBLE NOT NULL,
            move DOUBLE NOT NULL, crossing_index INTEGER NOT NULL, after_gap BOOLEAN NOT NULL,
            price_source VARCHAR NOT NULL, cadence_ms INTEGER NOT NULL,
            recorded_ts BIGINT NOT NULL)""")
        old.execute("INSERT INTO hf_crossing_events VALUES "
                    "('old1','ptb_5m_0',5,0,'UP','DOWN',10,1.0,1.0,0.0,1,false,'x',1000,0)")
        old.close()
        con = connect()
        cols = {r[1] for r in con.execute("PRAGMA table_info('hf_crossing_events')").fetchall()}
        check({"crossing_recv_ts", "ts_source", "clock_skew_ms"} <= cols,
              "an EXISTING events table is ALTERed to gain the new columns")
        check(con.execute("SELECT count(*) FROM hf_crossing_events").fetchone()[0] == 1,
              "and the pre-existing row survives untouched - additive, never a rewrite")
        check(con.execute(
            "SELECT ts_source FROM hf_crossing_events").fetchone()[0] is None,
            "an old row's clock is NULL, not retroactively claimed to be event time")

        # INCREMENTAL FLUSH. Flushing every 30s for a week must not rewrite the run each time.
        fr = Recorder()
        fr.record(base, 100.0)
        fr.record(base + 1000, 101.0)
        fr.record(base + 2000, 99.0)
        first = flush(fr, base + 3000, con=con)
        second = flush(fr, base + 3000, con=con)
        check(first[0] == 2 and second[0] == 0,
              "a second flush writes ZERO events - the write-through markers hold")
        check(con.execute("SELECT count(*) FROM hf_crossing_events "
                          "WHERE crossing_id != 'old1'").fetchone()[0] == 2,
              "and the database has each crossing exactly once")

        # PRUNING. Must not drop a round whose labels are still pending, and must drop one
        # that can no longer change.
        check(fr.prune(base + 3000) == 0,
              "a live round is NOT pruned - its horizons have not elapsed")
        check(len(fr.samples) > 0, "so its samples are still held")

        # THE CASE ONLY THE TIME GUARD PROTECTS. The check above passes even without it,
        # because the finality guard independently blocks a round that still has pending
        # events. A round in flight that has produced NO crossings yet has nothing pending,
        # so its anchor and its samples are held by the elapsed-time condition ALONE - and
        # pruning it mid-round would discard a verified OPEN anchor that cannot be recovered.
        quiet = Recorder()
        quiet.record(base, 100.0)
        quiet.record(base + 1000, 100.5)               # one side only: no crossing, ever
        flush(quiet, base + 2000, con=con)
        qid, _, _ = round_id_for(base, 15)
        check(not [e for e in quiet.events if e["round_id"] == qid],
              "the quiet round has no events, so no finality guard applies to it")
        check(quiet.prune(base + 2000) == 0 and qid in quiet.anchors,
              "a live round with NO crossings is still held - the elapsed-time guard is "
              "the only thing standing between it and a discarded anchor")
        far = base + 15 * 60_000 + PRUNE_MARGIN_MS + 1000
        flush(fr, far, con=con)                    # resolves and marks final
        fr.prune(far)
        check(len(fr.samples) == 0 and fr.pruned_rounds > 0,
              "a finished round IS pruned - memory does not grow for the life of the process")
        check(len(fr.events) == 0, "and its events are released with it")

        # RESTART SAFETY. A new process must re-adopt an in-flight anchor, or every restart
        # forfeits up to a whole round.
        live_base = (int(time.time() * 1000) // 900_000) * 900_000
        live = Recorder()
        live.record(live_base, 100.0)
        live.record(live_base + 1000, 101.0)
        live.record(live_base + 2000, 99.0)
        flush(live, live_base + 3000, con=con)
        reborn = Recorder()
        restored = reborn.restore(con, live_base + 4000)
        rid5, _, _ = round_id_for(live_base, 5)
        check(reborn.anchors.get(rid5) == 100.0,
              "a restart RE-ADOPTS the durable anchor of a round still in flight")
        check(reborn.anchor_quality[rid5]["quality"] == "OPEN_OBSERVED",
              "and keeps its provenance, so it is still the verified OPEN")
        check(restored > 0 and reborn.recovered_obligations == restored,
              "unresolved label obligations are recovered and counted, not silently dropped")
        check(reborn.counts[rid5] >= 1,
              "the crossing INDEX continues rather than restarting at 1")
        check(reborn.leaders[rid5] == "DOWN",
              "the last known leader is adopted - a blank slate would invent a crossing "
              "out of the restart itself")
        check(all(e["crossing_id"] in reborn.flushed_events for e in reborn.events),
              "recovered events are marked already-durable, so they are not rewritten")
        stale = Recorder()
        stale.restore(con, live_base + 30 * 60_000 + PRUNE_MARGIN_MS)
        check(stale.anchors.get(rid5) is None,
              "a round that has ENDED is not re-adopted - its anchor can change nothing")
    finally:
        DB = original_db
        try:
            con.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- supervision
    # Driven with injected session/sleep/clock/counter: weeks of restart behaviour, no network
    # and no waiting. A supervisor that can only be tested by crashing a live run is a
    # supervisor nobody tests.
    slept = []
    clock = {"t": 0.0}

    def tick(seconds_):
        slept.append(seconds_)
        clock["t"] += seconds_

    def now():
        return clock["t"]

    calls = {"n": 0}

    def clean_session(_remaining):
        calls["n"] += 1

    rc = supervise(60, session=clean_session, sleeper=tick, now=now, counter=lambda: 0)
    check(rc == 0 and calls["n"] == 1 and not slept,
          "a session that returns cleanly ENDS the service - no restart, no backoff")

    calls["n"] = 0
    slept.clear()

    def flaky(_remaining):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("websocket closed")

    counter = {"v": 0}

    def advancing():
        counter["v"] += 10          # each session recorded something
        return counter["v"]

    rc = supervise(None, session=flaky, sleeper=tick, now=now, counter=advancing)
    check(rc == 0 and calls["n"] == 3,
          "a session that RAISES is restarted - two faults, then a clean third session")
    # Compared against ITSELF, not against BACKOFF_MS: asserting slept == [BACKOFF_MS[0],
    # BACKOFF_MS[1]] passes even when the schedule is flattened, because the expectation
    # flattens with it. Mutation testing caught exactly that.
    check(len(slept) == 2 and slept[1] > slept[0],
          "backoff GROWS between attempts rather than hammering the venue")
    check(list(BACKOFF_MS) == sorted(BACKOFF_MS) and len(set(BACKOFF_MS)) > 1,
          "the declared schedule is non-decreasing and not flat")

    calls["n"] = 0
    slept.clear()

    def always_barren(_remaining):
        calls["n"] += 1
        raise RuntimeError("bad config")

    rc = supervise(None, session=always_barren, sleeper=tick, now=now, counter=lambda: 0)
    check(rc == 1 and calls["n"] == MAX_BARREN_ATTEMPTS,
          "sessions that record NOTHING give up after the declared limit - a broken setup is "
          "not retried forever")
    check(max(slept) <= BACKOFF_MS[-1] / 1000,
          "backoff is CAPPED, so a long outage does not become an unbounded sleep")

    calls["n"] = 0
    slept.clear()
    counter["v"] = 0

    def flaky_but_working(_remaining):
        calls["n"] += 1
        if calls["n"] >= 20:
            return
        raise RuntimeError("transient")

    rc = supervise(None, session=flaky_but_working, sleeper=tick, now=now, counter=advancing)
    check(rc == 0 and calls["n"] == 20,
          "a session that RECORDS before failing resets the barren counter - transient faults "
          "are retried past the give-up limit, unlike a dead setup")

    calls["n"] = 0

    def interrupted(_remaining):
        calls["n"] += 1
        raise KeyboardInterrupt()

    check(supervise(None, session=interrupted, sleeper=tick, now=now,
                    counter=lambda: 0) == 0 and calls["n"] == 1,
          "Ctrl-C stops the service instead of being retried as a fault")

    # An ALREADY-EXPIRED deadline must stop without running a session at all. Written as an
    # equality on the call count, not an `or True` - a check that cannot fail is not a check.
    calls["n"] = 0

    def never_called(_remaining):
        calls["n"] += 1

    # The clock must ADVANCE after `started` is captured, or elapsed time is always zero and
    # the deadline can never expire. First call establishes the start; the next is past it.
    stamps = iter([0.0, 999.0, 999.0, 999.0])
    rc = supervise(60, session=never_called, sleeper=tick,
                   now=lambda: next(stamps), counter=lambda: 0)
    check(rc == 0 and calls["n"] == 0,
          "an already-expired deadline stops WITHOUT starting a session")

    # And the remaining-time passed to the session shrinks as the deadline approaches.
    seen = []
    clock["t"] = 0.0

    def capture(remaining):
        seen.append(remaining)

    supervise(60, session=capture, sleeper=tick, now=now, counter=lambda: 0)
    check(seen == [60], "the session is told how many seconds remain, not the total")

    print(f"\nHF CROSSING RECORDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--forever", action="store_true",
                        help="run until stopped; the 5s/15s study needs weeks, not minutes")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--health", action="store_true",
                        help="ADVANCING/STALLED/NEVER_RAN from row progress")
    parser.add_argument("--supervise", action="store_true",
                        help="restart on failure with backoff (implied by --forever)")
    parser.add_argument("--rest", action="store_true",
                        help="poll REST instead of the WS stream; timestamps become LOCAL_POLL")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.health:
        info = health()
        print("  status %s   %s" % (info["status"], info.get("reason", "")))
        for key in ("heartbeats", "failed_beats", "crossings", "restarts",
                    "runs_without_clean_stop"):
            if key in info:
                print("    %-24s %s" % (key, format(info[key], ",")))
        return 0 if info["status"] == "ADVANCING" else 1
    if args.run or args.forever:
        seconds = None if args.forever else args.seconds
        if args.supervise or args.forever:
            return supervise(seconds, use_ws=not args.rest)
        return run(seconds, use_ws=not args.rest)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
