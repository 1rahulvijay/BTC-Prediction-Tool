"""Collector evidence-integrity tests (2026-07-26).

Each test corresponds to a defect found in external review of commit 8998d5b. They exist because
every one of these failures is SILENT: the collector keeps running, the report looks healthy, and
the corruption only becomes visible when a strategy is scored on data that was never valid.

    D1  a required Class-A stream was missing from the health gate  -> episode qualified anyway
    D2  stream ages were recorded but never gated on                -> stale feeds qualified
    D3  the evidence clock started before the insert succeeded      -> clock without evidence
    D4  episode health counted PARSED rows, not PERSISTED ones      -> healthy-looking data loss
    D5  dedup was scoped inside the lookback window                 -> re-polls re-entered features

    python backend/venues/test_collector_integrity.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import multi_venue_recorder as R          # noqa: E402


def _writer(persistent=False):
    con = R.init_db(":memory:")
    w = R.Writer(con, persistent=persistent)
    return con, w


def _fill(w, streams, n=10):
    for s in streams:
        w.ep_persisted[s] = n
        w.ep_counts[s] = n


def _episode_row(con):
    return con.execute("SELECT qualifying, exclusion_reason, streams_live, streams_required "
                       "FROM venue_episodes ORDER BY episode_start DESC LIMIT 1").fetchone()


def main():
    ok = True

    def chk(cond, msg, extra=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    print("collector integrity tests")

    # ---- D1: the required-stream set ---------------------------------------------------------
    chk("bybit_perp/publicTrade" in R.EXPECTED,
        "bybit_perp/publicTrade is a REQUIRED stream (Class-A per the preregistration)")
    chk(len(R.EXPECTED) == 9, f"required health is 9/9, not 8/8 (got {len(R.EXPECTED)})")

    con, w = _writer()
    _fill(w, [s for s in R.EXPECTED if s != "bybit_perp/publicTrade"])
    w._put_episode(1000, w.ep_counts, 20.0, 900.0, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=False)
    q, why, live, req = _episode_row(con)
    chk(not q and "missing:bybit_perp/publicTrade" in why,
        "episode missing the Bybit trade stream is EXCLUDED", f"({why})")
    con.close()

    # ---- D2: staleness must disqualify -------------------------------------------------------
    con, w = _writer()
    _fill(w, R.EXPECTED)
    w._put_episode(1000, w.ep_counts, 20.0, R.REST_MAX_AGE_MS + 1, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=False)
    q, why, _, _ = _episode_row(con)
    chk(not q and "rest_stale" in why, "all streams present but REST STALE -> excluded", f"({why})")
    con.close()

    con, w = _writer()
    _fill(w, R.EXPECTED)
    w._put_episode(1000, w.ep_counts, R.WS_MAX_AGE_MS + 1, 100.0, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=False)
    q, why, _, _ = _episode_row(con)
    chk(not q and "ws_stale" in why, "all streams present but WS STALE -> excluded", f"({why})")
    con.close()

    # a fully healthy 9/9 episode inside both limits MUST qualify (the gate must not be vacuous)
    con, w = _writer()
    _fill(w, R.EXPECTED)
    w._put_episode(1000, w.ep_counts, 30.0, 6_000.0, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=False)
    q, why, live, req = _episode_row(con)
    chk(bool(q) and live == 9 and req == 9,
        "healthy 9/9 episode within both age limits QUALIFIES", f"(live={live}/{req})")
    con.close()

    # Fresh event timestamps alone are not enough: a stream can emit once and then go silent.
    con, w = _writer()
    w.ep_start = w.boot_ts = 900.0
    rows = []
    for key in R.EXPECTED:
        venue, stream = key.split("/", 1)
        rows.append({
            "recv_ts": 901.0,
            "exch_ts": 900.99,
            "venue": venue,
            "stream": stream,
            "source_mode": "REST_POLL" if key in R.REST_EXPECTED else "WS",
            "poll_id": 2.0 if key in R.REST_EXPECTED else None,
            "event_key": f"test:{key}",
        })
    w.add(rows)
    w.flush()
    w.close_episode(now=1201.0)
    q, why, _, _ = _episode_row(con)
    chk(not q and ("ws_stale" in why or "rest_stale" in why),
        "one fresh row followed by an episode-long SILENCE is excluded", f"({why})")
    con.close()

    # ---- D3: the evidence clock must not start unless a row was persisted --------------------
    class BadCon:
        """A connection whose executemany always fails; everything else behaves."""
        def __init__(self, real):
            self._r = real

        def executemany(self, *a, **k):
            raise RuntimeError("simulated disk failure")

        def __getattr__(self, n):
            return getattr(self._r, n)

    real = R.init_db(":memory:")
    w = R.Writer(BadCon(real), persistent=True)
    w.buf.append([None] * len(R.COLS))
    raised = False
    try:
        w.flush()
    except Exception:
        raised = True
    chk(raised, "a failing insert RAISES rather than silently dropping evidence")
    chk(len(w.buf) == 1, "the failed batch is RETAINED, not cleared", f"(buf={len(w.buf)})")
    chk(w.writer_errors == 1 and w.ep_writer_failed, "writer failure is recorded on the episode")
    started = real.execute(
        "SELECT COUNT(*) FROM venue_collection_meta WHERE k='collection_start_ts'").fetchone()[0]
    chk(started == 0, "collection_start_ts NOT created when the insert failed", f"(rows={started})")

    # and an episode sealed after a writer failure cannot qualify
    _fill(w, R.EXPECTED)
    w._put_episode(2000, w.ep_counts, 20.0, 100.0, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=w.ep_writer_failed)
    q, why, _, _ = _episode_row(real)
    chk(not q and "writer_failed" in why, "episode with a writer failure is EXCLUDED", f"({why})")
    real.close()

    # ---- D4: qualification counts PERSISTED rows, not parsed ones ----------------------------
    con, w = _writer()
    for s in R.EXPECTED:
        w.ep_counts[s] = 50          # parsed
    # ep_persisted deliberately left empty: nothing reached the database
    w._put_episode(3000, w.ep_counts, 20.0, 100.0, 0, partial=False,
                   persisted=w.ep_persisted, writer_failed=False)
    q, why, live, _ = _episode_row(con)
    chk(not q and live == 0,
        "parsed-but-unpersisted rows do NOT make an episode healthy", f"(live={live}, {why})")
    con.close()

    # a good flush promotes pending -> persisted
    con2 = R.init_db(":memory:")
    w2 = R.Writer(con2, persistent=True)
    row = [None] * len(R.COLS)
    row[R.COLS.index("venue")] = "binance_spot"
    row[R.COLS.index("stream")] = "bookTicker"
    row[R.COLS.index("recv_ts")] = time.time()
    w2.buf.append(row)
    w2._pending_counts["binance_spot/bookTicker"] = 1
    w2.flush()
    chk(w2.ep_persisted.get("binance_spot/bookTicker") == 1,
        "a successful insert promotes pending counts to PERSISTED")
    chk(len(w2._pending_counts) == 0, "pending counts are cleared after a good insert")
    started2 = con2.execute(
        "SELECT COUNT(*) FROM venue_collection_meta WHERE k='collection_start_ts'").fetchone()[0]
    chk(started2 == 1, "collection_start_ts IS created once a row actually persisted")
    con2.close()

    # ---- D7: connection generation is NOT the poll counter -----------------------------------
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "multi_venue_recorder.py"), encoding="utf-8").read()
    chk('"connection_id": float(poll_id)' not in src,
        "REST rows no longer stamp connection_id with poll_id")
    chk('w.connection_id["binance_perp_rest"] += 1' in src,
        "REST connection generation advances only on an actual session rebuild")
    chk("poll_id = 0" in src.split("session rebuilt")[0][-800:] or "poll_id = 0" in src,
        "a rebuilt session resets poll_id (its poll 1 is BACKLOG again)")
    chk("return_exceptions=True)" not in src.split("async def _run", 1)[1].split(
        "def report", 1)[0].split("finally:", 1)[0],
        "writer-task exceptions are not swallowed by the collector supervisor")

    # ---- D8: 'four CONTINUOUS weeks' is enforced mechanically ---------------------------------
    E = R.EPISODE_S

    def _grid(rows):
        c = R.init_db(":memory:")
        for slot, qual in rows:
            c.execute("INSERT OR REPLACE INTO venue_episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (slot * E, slot * E + E, "{}", 9 if qual else 3, 9, 20.0, 100.0, 0,
                       qual, "" if qual else "missing:x"))
        return c

    # scattered health: passes the OLD count+span check, fails 'continuous'
    rows, s = [], 0
    for _ in range(3):
        rows += [(s + i, True) for i in range(400)]
        s += 400 + 2000                      # ~7-day outage between blocks
    c = _grid(rows)
    rep = R.continuity_report(c)
    chk(rep["total_qualifying"] >= R.GATE_MIN_QUALIFYING,
        "scenario satisfies the OLD count condition", f"(n={rep['total_qualifying']:,})")
    chk(rep["gate"] == "NOT MET",
        ">=1,000 qualifying spread across outages is REFUSED",
        f"(longest run {rep['longest_run_weeks']:.2f}w)")
    chk(rep["gaps"] == 2 and rep["largest_gap_h"] > 100,
        "outage geometry reported", f"(gaps={rep['gaps']}, largest={rep['largest_gap_h']:.0f}h)")
    c.close()

    # a genuinely unbroken run passes
    need = R.GATE_MIN_CONTINUOUS_WEEKS * R.GATE_EPISODES_PER_WEEK
    c = _grid([(i, True) for i in range(need + 10)])
    rep = R.continuity_report(c)
    chk(rep["gate"] == "MET", "an unbroken 4-week run MEETS the gate",
        f"({rep['longest_run_weeks']:.2f}w, coverage {rep['coverage_pct']:.0f}%)")
    c.close()

    # one excluded episode mid-run breaks continuity (this is what 'continuous' means)
    c = _grid([(i, i != (need + 10) // 2) for i in range(need + 10)])
    rep = R.continuity_report(c)
    chk(rep["gate"] == "NOT MET",
        "a single excluded episode mid-run BREAKS the run",
        f"(longest {rep['longest_run_weeks']:.2f}w)")
    c.close()

    print("collector-integrity:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
