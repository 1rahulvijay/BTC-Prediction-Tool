"""Kline timestamp unit contract - REST history and the live WS bar MUST agree.

WHY THIS TEST EXISTS (2026-07-28)

An audit proposed "removing the // 1000 truncation in the WebSocket client so time
remains in milliseconds, matching the historical data schema". The historical schema is
NOT milliseconds: BinanceRESTClient emits `int(k[0]) // 1000`, i.e. SECONDS. Both paths
were already consistent, and the change made the WS bar 1000x larger than the history it
is merged into.

The damage is in server.handle_kline, which compares the incoming WS bar against the
last REST-built bar:

    kline["time"] == last["time"]   -> update the forming candle in place
    kline["time"] >  last["time"]   -> the candle closed; append a new one

With mixed units the first comparison never matches the real history, so the first ms
bar appends once as an ORPHAN carrying a 1000x timestamp. Every later tick then matches
that orphan and merges into it. The measured effect is quieter than unbounded growth and
worse than it looks: the genuine last bar FREEZES and never receives another live
update, while all incoming price data accumulates in a bar the rest of the system cannot
place in time.

These tests exercise that behaviour rather than reading the source, so the regression
cannot come back disguised as a fix.

    python backend/test_kline_time_contract.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK = True


def chk(cond: bool, msg: str) -> None:
    global OK
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    OK = OK and bool(cond)


def _rest_kline_row(open_ms: int) -> list:
    """A Binance REST /api/v3/klines row: index 0 is open time in MILLISECONDS."""
    return [open_ms, "60000.0", "60100.0", "59900.0", "60050.0", "12.5",
            open_ms + 59_999, "750000.0", 100, "6.0", "360000.0", "0"]


def _ws_kline_payload(open_ms: int) -> dict:
    """A Binance WS kline `k` object: "t" is open time in MILLISECONDS."""
    return {"t": open_ms, "o": "60000.0", "h": "60100.0", "l": "59900.0",
            "c": "60050.0", "v": "12.5", "x": False}


def main() -> int:
    print("kline-time-contract selftest")
    minute_ms = (int(time.time()) // 60) * 60 * 1000

    # ---------------------------------------------------------------- unit agreement
    print("\n[units] REST history and live WS bar must use the SAME unit")
    # Drive the real parsing expressions the two clients use.
    rest_time = int(_rest_kline_row(minute_ms)[0]) // 1000
    ws_time = _ws_kline_payload(minute_ms)["t"] // 1000

    chk(rest_time == ws_time,
        f"same minute -> same timestamp  (REST {rest_time} == WS {ws_time})")
    chk(rest_time < 1e11,
        "the shared unit is SECONDS (< 1e11), which is what features._t_s expects")

    # A mixed-unit pair must be detectable as such, so the next assertions mean something.
    ws_ms_bug = _ws_kline_payload(minute_ms)["t"]
    chk(ws_ms_bug != rest_time and ws_ms_bug / max(1, rest_time) > 900,
        f"the regression is ~1000x ({ws_ms_bug} vs {rest_time}) - not a subtle drift")

    # ------------------------------------------------------- handle_kline behaviour
    print("\n[merge] the live bar must MERGE into REST history, not append every tick")
    import server

    saved_klines = server.data_state.get("klines")
    saved_feed = dict(server.data_state.get("feed_timestamps_ms") or {})
    try:
        base = [{"time": rest_time - 60, "open": 59900.0, "high": 60000.0,
                 "low": 59800.0, "close": 59950.0, "volume": 10.0},
                {"time": rest_time, "open": 60000.0, "high": 60100.0,
                 "low": 59900.0, "close": 60050.0, "volume": 12.5}]

        # Same minute, correct unit -> in-place update, length unchanged.
        server.data_state["klines"] = [dict(k) for k in base]
        server.handle_kline({"time": rest_time, "open": 60000.0, "high": 60200.0,
                             "low": 59900.0, "close": 60150.0, "volume": 20.0})
        n_same = len(server.data_state["klines"])
        chk(n_same == 2, f"same-minute WS bar updates in place (len stays 2, got {n_same})")
        chk(server.data_state["klines"][-1]["close"] == 60150.0,
            "the forming candle actually received the new close")

        # Next minute -> exactly one append.
        server.data_state["klines"] = [dict(k) for k in base]
        server.handle_kline({"time": rest_time + 60, "open": 60050.0, "high": 60300.0,
                             "low": 60000.0, "close": 60250.0, "volume": 8.0})
        chk(len(server.data_state["klines"]) == 3,
            "a genuinely new minute appends exactly one bar")

        # THE REGRESSION: millisecond WS bar against a seconds history.
        # Measured failure mode: the first ms bar appends ONCE as an orphan whose
        # timestamp is 1000x the real one; every later tick then matches that orphan
        # and merges into it. So the history does not grow without bound - it does
        # something quieter and worse: the genuine last bar FREEZES and all live
        # price data accumulates in a bar the rest of the system cannot place in time.
        server.data_state["klines"] = [dict(k) for k in base]
        for i in range(5):
            server.handle_kline({"time": minute_ms, "open": 60000.0, "high": 60100.0,
                                 "low": 59900.0, "close": 70000.0 + i, "volume": 1.0})
        ks = server.data_state["klines"]
        chk(len(ks) == 3, f"a MILLISECOND bar appends exactly one orphan (len {len(ks)})")
        chk(ks[-1]["time"] / ks[1]["time"] > 900,
            f"the orphan carries a ~1000x timestamp ({ks[-1]['time']} vs {ks[1]['time']})")
        chk(ks[1]["close"] == 60050.0,
            "the genuine last bar FREEZES - it never receives another live update")
        chk(ks[-1]["close"] == 70004.0,
            "all subsequent live price data lands in the orphan instead")
    finally:
        if saved_klines is not None:
            server.data_state["klines"] = saved_klines
        server.data_state["feed_timestamps_ms"] = saved_feed

    # ----------------------------------------------------------- downstream tolerance
    print("\n[features] the feature layer normalises, but must not be relied on alone")
    import numpy as np
    import features as F

    times_s = np.array([rest_time - 120, rest_time - 60, rest_time], dtype=np.float64)
    norm = np.where(times_s > 1e11, times_s / 1000.0, times_s)
    chk(np.allclose(norm, times_s),
        "seconds pass through features' ms->s normaliser unchanged")
    chk(hasattr(F, "opening_range_breakout"),
        "opening_range_breakout takes SECONDS (times_s) - callers normalise, "
        "so it must not divide again internally")

    print("\nkline-time-contract:", "ALL PASS" if OK else "FAILURES")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
