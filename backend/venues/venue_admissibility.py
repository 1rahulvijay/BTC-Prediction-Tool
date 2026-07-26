"""
venue_admissibility.py - the ONLY sanctioned path from `venue_events` to a decision feature.

Section 0 of `PREREG_BINANCE_VOLATILITY_MOMENTUM_V1` is a binding contract, not a caveat. A
contract that lives in prose depends on an analyst remembering it eighteen months later, at the
exact moment a plausible result is on screen. This module makes it a function call that raises.

Two invariants are enforced here that cannot be enforced by the recorder alone:

1. REST BACKLOG IS PROHIBITED FROM FEATURES, NOT MERELY FILTERABLE.
   The first poll after every (re)connect returns up to 1000 historical aggTrades - measured ages
   of 255-317 SECONDS. Those rows are correct to record (diagnostics, provenance) and catastrophic
   to aggregate: several minutes of backlog would collapse into the first live decision window and
   read as a colossal, entirely fictitious flow impulse. `poll_id <= 1` never reaches a feature.

2. TIMESTAMP BASES MAY NOT BE MIXED IN LEAD-LAG.
   Binance SPOT bookTicker carries no exchange timestamp at all. Comparing its `recv_ts` against
   Bybit's `exch_ts` measures network latency and calls it market leadership. Each row therefore
   carries a `timestamp_basis`, and a lead-lag pair must share a usable basis or `require_leadlag`
   raises `InadmissiblePairing`.

Usage:
    python backend/venues/venue_admissibility.py --selftest
"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- timestamp basis

# What kind of clock a row's timestamps actually represent. Derived mechanically at write time
# (see Writer.add), never asserted by hand.
EXCHANGE_TIME = "EXCHANGE_TIME"          # push delivery + trustworthy venue event time
RECEIVE_TIME = "RECEIVE_TIME"            # push delivery, venue time present but NOT trustworthy
RECEIVE_ONLY = "RECEIVE_ONLY"            # push delivery, venue sends no event time at all
POLL_RECEIVE_TIME = "POLL_RECEIVE_TIME"  # REST poll: neither clock is comparable across venues

# Which comparison bases each label may be used in. "exch" = venue event time, "recv" = local
# receive time.
#
# POLL_RECEIVE_TIME maps to the EMPTY set, and that is the whole point. Its `exch_ts` is genuine
# but its delivery is delayed and batched, so ordering by it is the Class C prohibition verbatim;
# its `recv_ts` is dominated by poll cadence rather than by market events, so ordering by that is
# no better. A polled stream can carry slow aggregate state. It can never carry leadership.
BASIS_USABLE = {
    EXCHANGE_TIME: frozenset({"exch", "recv"}),
    RECEIVE_TIME: frozenset({"recv"}),
    RECEIVE_ONLY: frozenset({"recv"}),
    POLL_RECEIVE_TIME: frozenset(),
}

# A push feed whose event time sits outside this window relative to receipt is downgraded to
# RECEIVE_TIME: the venue clock exists but cannot serve as a common reference. Negative = venue
# clock ahead of ours (Coinbase runs ~-120ms; Bybit ~-85ms), so a small negative band is normal.
_WS_PLAUSIBLE_S = (-5.0, 60.0)

# Frozen Class-B age limit. DECLARED 2026-07-26, before any production row existed and before any
# M0 score - filling in the "frozen limit" that section 10 of the preregistration names but leaves
# unvalued. It matches that document's own ">= 60s aggregation" language for Class B. Observed
# steady-state ages are far below it (premiumIndex ~1.0s, openInterest ~6.1s, perp aggTrades ~1-2s
# once past the backlog poll), so this excludes malfunction, not normal operation.
# REVISING THIS AFTER SEEING M0 RESULTS INVALIDATES THE EXPERIMENT.
CLASS_B_MAX_AGE_S = 60.0

# Backlog polls. `poll_id` counts from 1 per collector process, and poll 1 is always a backfill of
# whatever the venue considers "recent". Restarts therefore produce a new poll 1.
FIRST_LIVE_POLL_ID = 2


class InadmissiblePairing(ValueError):
    """Raised when a lead-lag comparison would mix incompatible timestamp bases."""


def basis_for(source_mode, exch_ts, recv_ts):
    """Derive `timestamp_basis` from delivery path and timestamp plausibility.

    Called by the recorder for EVERY row, so a parser added later cannot forget to label itself."""
    if source_mode == "REST_POLL":
        return POLL_RECEIVE_TIME
    if not exch_ts:                       # None or 0.0 - spot bookTicker sends no event time
        return RECEIVE_ONLY
    if recv_ts is None:
        return RECEIVE_ONLY
    lag = float(recv_ts) - float(exch_ts)
    lo, hi = _WS_PLAUSIBLE_S
    return EXCHANGE_TIME if lo <= lag <= hi else RECEIVE_TIME


def common_basis(basis_a, basis_b):
    """Strongest basis both sources support, or None. Exchange time is preferred when available."""
    shared = BASIS_USABLE.get(basis_a, frozenset()) & BASIS_USABLE.get(basis_b, frozenset())
    if "exch" in shared:
        return "exch"
    if "recv" in shared:
        return "recv"
    return None


# Receive-basis lead-lag measures the OBSERVER, not the market. Naming it `venue_lead` would
# smuggle an economic claim into a network measurement, so the permitted names say what was
# actually measured and the gate below refuses anything else.
OBSERVER_LEAD_NAMES = ("observer_time_lead", "collector_arrival_lead")

_LEAD_NAME_RULE = """
A receive-time ordering says only: A reached THIS collector, on THIS host, through THIS route,
before B. It also contains network-route differences, venue publication latency, WebSocket
batching, event-loop scheduling, reconnect state and parser/queue delay. Calling that
`venue_lead` asserts economic price discovery that the measurement cannot support.

Permitted names for a receive-basis feature: {names}.
A true price-discovery claim needs EITHER compatible exchange timestamps on both sides, OR a
separately preregistered normalization for venue-specific receive-latency baselines.
"""


def leadlag_feature_name(name, basis):
    """Validate the NAME of a lead-lag feature against the basis it was computed in.

    Called at feature-definition time. The point is that the mislabelling is caught when the
    feature is written, not when someone is reading a result they already like."""
    if basis == "exch":
        return name
    if name not in OBSERVER_LEAD_NAMES:
        raise InadmissiblePairing(
            f"'{name}' is a receive-basis feature and may not be named as a venue/economic lead.\n"
            + _LEAD_NAME_RULE.format(names=" | ".join(OBSERVER_LEAD_NAMES)))
    return name


def require_leadlag(basis_a, basis_b, basis=None):
    """Gate for any lead-lag feature. Returns the basis to use; raises if the pair is invalid.

    `basis` may be given to demand a specific comparison; omit it to take the strongest shared one.
    Call this BEFORE computing a lead-lag statistic, not while interpreting one."""
    if basis is None:
        got = common_basis(basis_a, basis_b)
        if got is None:
            raise InadmissiblePairing(
                f"no shared timestamp basis: {basis_a} vs {basis_b}. "
                f"{POLL_RECEIVE_TIME} sources carry slow state only and can never establish "
                f"leadership; {RECEIVE_ONLY} sources can only be compared in receive time.")
        return got
    for label, name in ((basis_a, "a"), (basis_b, "b")):
        if basis not in BASIS_USABLE.get(label, frozenset()):
            raise InadmissiblePairing(
                f"source {name} has basis {label}, which cannot be compared in '{basis}' time")
    return basis


# ---------------------------------------------------------------- feature reader

# Enforcement lives in SQL so it cannot be bypassed by post-filtering a DataFrame in a notebook.
_ADMISSIBLE_SQL = """
WITH first_seen AS (
    SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY venue, stream, event_key ORDER BY recv_ts) AS _rn
    FROM venue_events
    WHERE recv_ts <= ? AND recv_ts > ?
      -- (1) REST backlog: prohibited outright, never merely filterable
      AND NOT (source_mode = 'REST_POLL' AND (poll_id IS NULL OR poll_id < {first_live}))
      -- (2) Class B staleness: a delayed observable past its frozen age is unavailable, not fresh
      AND NOT (source_mode = 'REST_POLL'
               AND (exch_ts IS NULL OR (recv_ts - exch_ts) > {max_age}))
      -- (3) a POLLED row with no stable natural identity cannot be recognised as a repeat after a
      --     reconnect, so it can silently double-count. Recorded, but never a feature.
      AND NOT (source_mode = 'REST_POLL' AND (event_key IS NULL OR event_key = ''))
)
SELECT * EXCLUDE (_rn) FROM first_seen
-- (4) first observation wins: a restart re-polls data already recorded, and the SECOND sighting of
--     an event carries a recv_ts that never reflected when we could first have known it
WHERE event_key IS NULL OR _rn = 1
"""


def admissible_sql(basis=None):
    """The exact SQL used to build features, exposed so an auditor can read the gate itself."""
    sql = _ADMISSIBLE_SQL.format(first_live=FIRST_LIVE_POLL_ID, max_age=CLASS_B_MAX_AGE_S)
    if basis:
        allowed = sorted(k for k, v in BASIS_USABLE.items() if basis in v)
        if not allowed:
            raise InadmissiblePairing(f"no timestamp basis supports '{basis}'")
        sql += "\n  AND timestamp_basis IN (" + ",".join(f"'{a}'" for a in allowed) + ")"
    return sql + "\nORDER BY recv_ts"


def admissible_events(con, decision_ts, lookback_s=300.0, basis=None):
    """Read decision-eligible rows as of `decision_ts`. THE sanctioned entry point for features.

    Nothing returned by this function may be later re-widened by a hand-written query; if a
    feature needs data this excludes, that is a preregistration change, not a query change."""
    if lookback_s <= 0:
        raise ValueError("lookback_s must be positive")
    return con.execute(admissible_sql(basis),
                       [float(decision_ts), float(decision_ts) - float(lookback_s)]).df()


def assert_feature_frame(df):
    """Belt-and-braces check on a frame about to become features. Cheap; run it anyway.

    The SQL above already guarantees this. This exists because the failure it catches is silent,
    unfalsifiable from the model's output, and would invalidate every number downstream of it."""
    if len(df) == 0:
        return df
    if "source_mode" not in df.columns or "timestamp_basis" not in df.columns:
        raise InadmissiblePairing("frame lacks provenance columns; it is not admissible evidence")
    rest = df[df["source_mode"] == "REST_POLL"]
    if len(rest):
        bad = rest[rest["poll_id"].fillna(0) < FIRST_LIVE_POLL_ID]
        if len(bad):
            raise InadmissiblePairing(
                f"{len(bad)} REST backlog rows (poll_id < {FIRST_LIVE_POLL_ID}) reached a feature "
                f"frame; up to 1000 trades spanning minutes would collapse into one window")
        stale = rest[(rest["recv_ts"] - rest["exch_ts"]) > CLASS_B_MAX_AGE_S]
        if len(stale):
            raise InadmissiblePairing(f"{len(stale)} Class B rows exceed the frozen "
                                      f"{CLASS_B_MAX_AGE_S:.0f}s age limit")
    if df["timestamp_basis"].isna().any():
        raise InadmissiblePairing("rows without a timestamp_basis cannot be used")
    return df


# ---------------------------------------------------------------- selftest

def selftest():
    import duckdb
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    # ---- basis derivation
    chk(basis_for("WS", 1000.0, 1000.02) == EXCHANGE_TIME, "healthy WS row -> EXCHANGE_TIME")
    chk(basis_for("WS", None, 1000.0) == RECEIVE_ONLY, "WS without event time -> RECEIVE_ONLY")
    chk(basis_for("WS", 0.0, 1000.0) == RECEIVE_ONLY, "exch_ts 0 treated as absent, not epoch")
    chk(basis_for("WS", 900.0, 1000.0) == RECEIVE_TIME, "WS with 100s skew downgraded to RECEIVE_TIME")
    chk(basis_for("WS", 1010.0, 1000.0) == RECEIVE_TIME, "WS timestamped in the future downgraded")
    chk(basis_for("WS", 1000.1, 1000.0) == EXCHANGE_TIME, "small negative lag stays EXCHANGE_TIME")
    chk(basis_for("REST_POLL", 940.0, 1000.0) == POLL_RECEIVE_TIME, "REST poll -> POLL_RECEIVE_TIME")

    # ---- lead-lag pairing
    chk(require_leadlag(EXCHANGE_TIME, EXCHANGE_TIME) == "exch", "exch vs exch admissible")
    chk(require_leadlag(EXCHANGE_TIME, RECEIVE_ONLY) == "recv",
        "exch-capable vs receive-only falls back to a SHARED receive basis")
    try:
        require_leadlag(EXCHANGE_TIME, RECEIVE_ONLY, basis="exch")
        chk(False, "spot(recv-only) vs bybit(exch) rejected in exchange time")
    except InadmissiblePairing:
        chk(True, "spot(recv-only) vs bybit(exch) rejected in exchange time")
    for other in (EXCHANGE_TIME, RECEIVE_ONLY, RECEIVE_TIME, POLL_RECEIVE_TIME):
        try:
            require_leadlag(POLL_RECEIVE_TIME, other)
            chk(False, f"polled source rejected for lead-lag vs {other}")
            break
        except InadmissiblePairing:
            pass
    else:
        chk(True, "polled source rejected for lead-lag against EVERY basis")

    # ---- the reader
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE venue_events(
        recv_ts DOUBLE, exch_ts DOUBLE, venue VARCHAR, stream VARCHAR, seq DOUBLE,
        event_key VARCHAR, price DOUBLE, source_mode VARCHAR, poll_id DOUBLE,
        timestamp_basis VARCHAR)""")
    D = 10_000.0                                  # decision_ts
    R, W = "REST_POLL", "WS"
    rows = [
        # backlog: inside the window, causal, but 5 minutes stale and from poll 1
        (D - 10, D - 310, "binance_perp", "aggTrade_rest", 1, "a:1", 1.0, R, 1, POLL_RECEIVE_TIME),
        # live REST trade, fresh
        (D - 10, D - 11, "binance_perp", "aggTrade_rest", 2, "a:2", 2.0, R, 7, POLL_RECEIVE_TIME),
        # live REST trade but stale beyond the frozen limit
        (D - 10, D - 95, "binance_perp", "aggTrade_rest", 3, "a:3", 3.0, R, 7, POLL_RECEIVE_TIME),
        # same event re-polled after a restart: later sighting must lose to the first
        (D - 300, D - 301, "binance_perp", "aggTrade_rest", 4, "a:4", 4.0, R, 5, POLL_RECEIVE_TIME),
        (D - 5, D - 301, "binance_perp", "aggTrade_rest", 4, "a:4", 4.0, R, 2, POLL_RECEIVE_TIME),
        # WS rows: one causal, one from the future
        (D - 2, D - 2.01, "binance_spot", "aggTrade", 11, "a:11", 5.0, W, None, EXCHANGE_TIME),
        (D + 2, D + 1.99, "binance_spot", "aggTrade", 12, "a:12", 6.0, W, None, EXCHANGE_TIME),
        # spot quote: no event time at all
        (D - 1, None, "binance_spot", "bookTicker", 13, "u:13", 7.0, W, None, RECEIVE_ONLY),
        # THE REAL DEFECT THIS CATCHES: a slow observable polled faster than the venue republishes.
        # Three polls, one underlying observation - identity is instrument + publication time.
        (D - 30, D - 31, "binance_perp", "premiumIndex", None, "t:9969000", 8.0, R, 4, POLL_RECEIVE_TIME),
        (D - 25, D - 31, "binance_perp", "premiumIndex", None, "t:9969000", 8.0, R, 5, POLL_RECEIVE_TIME),
        (D - 20, D - 31, "binance_perp", "premiumIndex", None, "t:9969000", 8.0, R, 6, POLL_RECEIVE_TIME),
        # a polled row the venue gave no identity for: unrecognisable as a repeat after a reconnect
        (D - 15, D - 16, "binance_perp", "openInterest", None, None, 9.0, R, 6, POLL_RECEIVE_TIME),
    ]
    con.executemany("INSERT INTO venue_events VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    df = admissible_events(con, D, lookback_s=600)
    got = set(zip(df["stream"], df["event_key"]))
    chk(("aggTrade_rest", "a:1") not in got, "poll_id=1 backlog PROHIBITED from the feature frame")
    chk(("aggTrade_rest", "a:2") in got, "fresh live REST trade admitted")
    chk(("aggTrade_rest", "a:3") not in got, "REST row past the frozen age limit excluded")
    kept = df[df["event_key"] == "a:4"]
    chk(len(kept) == 1, "re-polled duplicate collapsed to its FIRST observation")
    chk(len(kept) == 1 and abs(float(kept["recv_ts"].iloc[0]) - (D - 300)) < 1e-9,
        "the retained duplicate is the earlier sighting, not the later one")
    chk(("aggTrade", "a:12") not in got, "row with recv_ts after decision_ts excluded (causality)")
    chk(("aggTrade", "a:11") in got and ("bookTicker", "u:13") in got, "causal WS rows admitted")

    # The identity rule, on the streams that actually needed it
    pi = df[df["stream"] == "premiumIndex"]
    chk(len(pi) == 1, "one venue publication polled 3x collapses to ONE observation")
    chk(len(pi) == 1 and abs(float(pi["recv_ts"].iloc[0]) - (D - 30)) < 1e-9,
        "the surviving premiumIndex row is the earliest sighting")
    chk(len(df[df["stream"] == "openInterest"]) == 0,
        "polled row with NO stable identity barred from features (could double-count)")

    ex = admissible_events(con, D, lookback_s=600, basis="exch")
    chk(set(ex["timestamp_basis"]) <= {EXCHANGE_TIME},
        "exchange-time request returns ONLY exchange-time-capable rows")
    chk(len(ex[ex["source_mode"] == "REST_POLL"]) == 0,
        "no polled row survives an exchange-time request")

    # ---- the belt-and-braces check catches a hand-built frame the SQL never saw
    import pandas as pd
    chk(len(assert_feature_frame(df)) == len(df), "clean frame passes assert_feature_frame")
    smuggled = pd.DataFrame([{"recv_ts": D - 10, "exch_ts": D - 310, "source_mode": "REST_POLL",
                              "poll_id": 1.0, "timestamp_basis": POLL_RECEIVE_TIME}])
    try:
        assert_feature_frame(smuggled)
        chk(False, "hand-built frame containing backlog is rejected")
    except InadmissiblePairing:
        chk(True, "hand-built frame containing backlog is rejected")

    # ---- reconnect scenario: a fresh process re-polls data already stored
    # This is the case a synthetic poll-local counter would MISS entirely: poll_id restarts at 1
    # in the new process, so only a stable venue-supplied identity can recognise the repeat.
    rc = duckdb.connect(":memory:")
    rc.execute("""CREATE TABLE venue_events(
        recv_ts DOUBLE, exch_ts DOUBLE, venue VARCHAR, stream VARCHAR, seq DOUBLE,
        event_key VARCHAR, price DOUBLE, source_mode VARCHAR, poll_id DOUBLE,
        timestamp_basis VARCHAR, process_start_id BIGINT)""")
    rc.executemany("INSERT INTO venue_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        # process 1 records trades 100-102 live
        (D - 200, D - 201, "binance_perp", "aggTrade_rest", 100, "a:100", 1.0, R, 9, POLL_RECEIVE_TIME, 1),
        (D - 199, D - 200, "binance_perp", "aggTrade_rest", 101, "a:101", 2.0, R, 9, POLL_RECEIVE_TIME, 1),
        (D - 198, D - 199, "binance_perp", "aggTrade_rest", 102, "a:102", 3.0, R, 10, POLL_RECEIVE_TIME, 1),
        # ---- collector restarts here ----
        # process 2, poll 1: the venue hands back the same three trades plus a new one
        (D - 100, D - 201, "binance_perp", "aggTrade_rest", 100, "a:100", 1.0, R, 1, POLL_RECEIVE_TIME, 2),
        (D - 100, D - 200, "binance_perp", "aggTrade_rest", 101, "a:101", 2.0, R, 1, POLL_RECEIVE_TIME, 2),
        (D - 100, D - 199, "binance_perp", "aggTrade_rest", 102, "a:102", 3.0, R, 1, POLL_RECEIVE_TIME, 2),
        (D - 100, D - 101, "binance_perp", "aggTrade_rest", 103, "a:103", 4.0, R, 1, POLL_RECEIVE_TIME, 2),
        # process 2, poll 2 onward: trade 104 arrives live
        (D - 90, D - 91, "binance_perp", "aggTrade_rest", 104, "a:104", 5.0, R, 2, POLL_RECEIVE_TIME, 2),
    ])
    rdf = admissible_events(rc, D, lookback_s=600)
    chk(len(rdf) == len(set(rdf["event_key"])), "no event survives the reconnect twice")
    for key, want in (("a:100", D - 200), ("a:101", D - 199), ("a:102", D - 198)):
        row = rdf[rdf["event_key"] == key]
        if not (len(row) == 1 and abs(float(row["recv_ts"].iloc[0]) - want) < 1e-9):
            chk(False, "pre-restart recv_ts stays authoritative after a reconnect re-poll")
            break
    else:
        chk(True, "pre-restart recv_ts stays authoritative after a reconnect re-poll")
    chk(len(rdf[rdf["event_key"] == "a:103"]) == 0,
        "a trade first seen ONLY in a poll-1 backfill is excluded, not silently promoted")
    chk(len(rdf[rdf["event_key"] == "a:104"]) == 1, "genuinely new post-restart trade admitted")
    rc.close()

    # ---- naming: receive-basis lead-lag describes the OBSERVER, not the market
    for nm in OBSERVER_LEAD_NAMES:
        chk(leadlag_feature_name(nm, "recv") == nm, f"'{nm}' permitted for a receive-basis feature")
    try:
        leadlag_feature_name("venue_lead", "recv")
        chk(False, "'venue_lead' REFUSED for a receive-basis feature")
    except InadmissiblePairing:
        chk(True, "'venue_lead' REFUSED for a receive-basis feature")
    chk(leadlag_feature_name("venue_lead", "exch") == "venue_lead",
        "an economic lead name is allowed once BOTH sides carry exchange time")

    con.close()
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
