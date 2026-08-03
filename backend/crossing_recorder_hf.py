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

    python backend/crossing_recorder_hf.py --selftest
    python backend/crossing_recorder_hf.py --run --seconds 120
    python backend/crossing_recorder_hf.py --report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
ROUND_MINUTES = (5, 15)
REVERSION_HORIZONS_S = (5, 15, 30, 60)
LABEL_VERSION = "hf_crossing_labels_v1"
PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
PRICE_SOURCE = "binance_spot_ticker"

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
        recorded_ts    BIGINT  NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hf_crossing_labels (
        crossing_id       VARCHAR NOT NULL,
        label_version     VARCHAR NOT NULL,
        eligible_after_ts BIGINT  NOT NULL,
        resolved_ts       BIGINT  NOT NULL,
        reverted_5s       BOOLEAN,
        reverted_15s      BOOLEAN,
        reverted_30s      BOOLEAN,
        reverted_60s      BOOLEAN,
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
    CREATE TABLE IF NOT EXISTS hf_gaps (
        gap_start_ts BIGINT PRIMARY KEY,
        gap_end_ts   BIGINT NOT NULL,
        gap_ms       BIGINT NOT NULL,
        reason       VARCHAR
    )
    """,
)


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
    return con


class Recorder:
    """Pure state machine. `record()` is driven by (timestamp, price); it never calls a clock.

    Keeping time and price OUT of the machine is what makes the selftest able to drive months
    of behaviour deterministically in milliseconds, and is why the gap and label logic can be
    asserted rather than hoped for."""

    def __init__(self, price_source: str = PRICE_SOURCE, cadence_ms: int = POLL_MS):
        self.price_source = price_source
        self.cadence_ms = cadence_ms
        self.anchors: dict[str, float] = {}
        self.leaders: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.samples: dict[str, list[tuple[int, str]]] = {}
        self.last_ts: int | None = None
        self.events: list[dict] = []
        self.gaps: list[dict] = []

    def record(self, ts_ms: int, price: float) -> list[dict]:
        """One observation. Returns any crossings detected at this instant."""
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
                # First observation of a round establishes its anchor. It is never a crossing.
                self.anchors[round_id] = price
                self.leaders[round_id] = ""
                self.counts[round_id] = 0
                self.samples[round_id] = []
            anchor = self.anchors[round_id]
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
            for horizon in REVERSION_HORIZONS_S:
                row[f"reverted_{horizon}s"] = None
                deadline = event["crossing_ts"] + horizon * 1000
                if now_ms < deadline or deadline > round_end:
                    continue
                window = [side for ts, side in after if ts <= deadline and side]
                if window:
                    row[f"reverted_{horizon}s"] = (window[-1] == event["from_side"])
            if now_ms >= round_end:
                row["is_final_crossing"] = event["from_side"] not in {
                    side for _, side in after if side}
            if any(row[f"reverted_{h}s"] is not None for h in REVERSION_HORIZONS_S) \
                    or row["is_final_crossing"] is not None:
                row["resolved_ts"] = now_ms
                out.append(row)
        return out


def flush(recorder: Recorder, now_ms: int) -> tuple[int, int, int]:
    con = connect()
    try:
        events = 0
        for event in recorder.events:
            if con.execute("SELECT 1 FROM hf_crossing_events WHERE crossing_id = ?",
                           [event["crossing_id"]]).fetchone():
                continue
            con.execute("INSERT INTO hf_crossing_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [event["crossing_id"], event["round_id"], event["horizon_min"],
                         event["crossing_ts"], event["from_side"], event["to_side"],
                         event["seconds_left"], event["anchor"], event["price"],
                         event["move"], event["crossing_index"], event["after_gap"],
                         event["price_source"], event["cadence_ms"], now_ms])
            events += 1
        labels = 0
        for row in recorder.resolve(now_ms):
            con.execute("INSERT OR REPLACE INTO hf_crossing_labels VALUES (?,?,?,?,?,?,?,?,?)",
                        [row["crossing_id"], row["label_version"], row["eligible_after_ts"],
                         row["resolved_ts"], row["reverted_5s"], row["reverted_15s"],
                         row["reverted_30s"], row["reverted_60s"], row["is_final_crossing"]])
            labels += 1
        gaps = 0
        for gap in recorder.gaps:
            con.execute("INSERT OR REPLACE INTO hf_gaps VALUES (?,?,?,?)",
                        [gap["gap_start_ts"], gap["gap_end_ts"], gap["gap_ms"], gap["reason"]])
            gaps += 1
        return events, labels, gaps
    finally:
        con.close()


def heartbeat(beat_ts: int, round_id: str, price: float | None, leader: str,
              poll_ms: int, ok: bool, note: str = "") -> None:
    con = connect()
    try:
        con.execute("INSERT OR REPLACE INTO hf_heartbeats VALUES (?,?,?,?,?,?,?)",
                    [beat_ts, round_id, price, leader, poll_ms, ok, note])
    finally:
        con.close()


def run(seconds: int) -> int:
    recorder = Recorder()
    deadline = time.time() + seconds
    polls = failures = 0
    print(f"  recording for {seconds}s at ~{POLL_MS}ms, source {PRICE_SOURCE}")
    while time.time() < deadline:
        started = time.time()
        ts_ms = int(started * 1000)
        try:
            price = fetch_price()
            produced = recorder.record(ts_ms, price)
            round_id, _, _ = round_id_for(ts_ms, ROUND_MINUTES[0])
            heartbeat(ts_ms, round_id, price, recorder.leaders.get(round_id, ""),
                      int((time.time() - started) * 1000), True)
            for event in produced:
                print(f"    CROSSING {event['round_id']} {event['from_side']}->"
                      f"{event['to_side']} at {event['seconds_left']}s left, "
                      f"move {event['move']:+.2f}")
        except Exception as exc:
            failures += 1
            heartbeat(ts_ms, "", None, "", 0, False, str(exc)[:120])
        polls += 1
        time.sleep(max(0.0, POLL_MS / 1000 - (time.time() - started)))

    now_ms = int(time.time() * 1000)
    events, labels, gaps = flush(recorder, now_ms)
    print(f"  {polls} polls ({failures} failed), {len(recorder.events)} crossings detected")
    print(f"  wrote {events} events, {labels} label rows, {gaps} gaps -> {DB.name}")
    return 0


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
            column = f"reverted_{horizon}s"
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

    base = 1_785_000_000_000 // 300_000 * 300_000     # an exact 5m boundary

    rid, start, left = round_id_for(base + 60_000, 5)
    check(rid == f"ptb_5m_{base}" and start == base and left == 240,
          "round identity and time-remaining follow the wall clock deterministically")
    check(round_id_for(base + 299_999, 5)[2] == 0, "the final second of a round has 0s left")
    check(round_id_for(base + 300_000, 5)[0] != rid, "the next block is a different round")

    check(leader_for(101, 100) == "UP" and leader_for(99, 100) == "DOWN",
          "the leader is the side of the anchor")
    check(leader_for(100, 100) == "", "exactly AT the anchor is not a side")

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
    check(got["reverted_5s"] is False,
          "THE POINT OF THIS RECORDER: the 5s horizon IS resolvable at 1s cadence")
    check(got["reverted_60s"] is None,
          "an unelapsed horizon stays NULL - never a default False")
    late = lr.resolve(now_ms=event["crossing_ts"] + 70_000)[0]
    check(late["reverted_15s"] is False and late["reverted_30s"] is False,
          "15s and 30s resolve too - all four horizons are reachable")

    rv = Recorder()
    rv.record(base, 100.0)
    rv.record(base + 1000, 101.0)
    rv.record(base + 2000, 99.0)
    for offset in range(3000, 12_000, 1000):
        rv.record(base + offset, 101.5)                # returns to UP
    reverted = rv.resolve(now_ms=base + 12_000)[0]
    check(reverted["reverted_5s"] is True,
          "a leader that returns to the original side within 5s IS recorded as reverted")

    check(crossing_id("r", 1) == crossing_id("r", 1)
          and crossing_id("r", 1) != crossing_id("r", 2),
          "crossing identity is stable and time-specific")
    check(MAX_GAP_MS > POLL_MS,
          "the gap threshold exceeds one poll interval, so normal jitter is not a gap")

    print(f"\nHF CROSSING RECORDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.run:
        return run(args.seconds)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
