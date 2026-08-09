"""Binance perpetual funding and basis recorder — the missing term in the carry lane.

WHY THIS EXISTS
    `research/market_neutral_carry_lane.py` split carry into two terms and could only answer
    one of them:

        basis convergence   CLOSED     2.89 bps of range against a 24 bps four-leg round trip
        funding             UNMEASURED `funding_velocity` is 90% zeros; funding_events had 0 rows

    Funding is the DOMINANT term in a real carry book, and this repository had never recorded
    it. That made carry the only lane in the sweep blocked on data collection rather than on
    economics.

IT BACKFILLS, WHICH CHANGES THE TIMELINE
    `/fapi/v1/fundingRate` serves HISTORICAL settlements, so this is not a wait-three-months
    recorder. Years of 8-hourly funding can be pulled immediately and the carry question
    becomes answerable today. The forward poll adds the mark/index context that history does
    not carry between settlements.

    Both endpoints are public. No credentials, no orders, forward-only writes.

        /fapi/v1/fundingRate    fundingTime, fundingRate, markPrice   (history, paginated)
        /fapi/v1/premiumIndex   markPrice, indexPrice, nextFundingTime (current state)

THE BASIS HERE IS BETTER THAN THE ONE IN THE MATRIX
    `premiumIndex` gives mark AND index, and the index is Binance's own spot composite - the
    exact pair a hedge is put on against. The matrix's `perp_spot_basis_bps` is a real series
    but its construction could not be verified, and it sits at a persistent -4.65 bps that no
    one has explained. This records both sides so the basis is derived, not inherited.

WHAT IT REFUSES TO DO QUIETLY
    Funding settles on a FIXED 8-hourly schedule, so a missing settlement is detectable by
    TIME, not by an id - there is no per-message counter to check. That is the honest detector
    for this stream, and using an id-continuity test here would repeat the bookTicker mistake
    the tick recorder already made once.

      * MISSING SETTLEMENTS ARE WRITTEN. An 8h schedule with a hole in it is evidence.
      * SIGN FLIPS ARE COUNTED. A carry hedge dies when funding turns against it, and "how
        often does that happen" is the question the lane actually needs answered.
      * HEARTBEATS PROVE LIVENESS, so "no settlement this window" and "not running" stay
        distinguishable.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
DEFAULT_DB = Path(os.environ.get("BTC_FUNDING_DB", DATA / "funding.duckdb"))

FUNDING_HISTORY_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
SYMBOL = os.environ.get("BTC_FUNDING_SYMBOL", "BTCUSDT")

#: Binance settles funding every 8 hours at 00:00, 08:00 and 16:00 UTC.
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000
#: Tolerance before an interval counts as a missing settlement rather than clock jitter.
SETTLEMENT_TOLERANCE_MS = 30 * 60 * 1000
#: The endpoint caps a page at 1000 rows.
PAGE_LIMIT = 1000
#: Polite spacing between history pages.
PAGE_SLEEP_S = 0.25
#: Liveness cadence. `recorder_health.STALL_AFTER_MS` is a single 15-minute threshold applied
#: to every recorder, and funding only SETTLES every 8 hours. Writing one heartbeat per run
#: would have made this recorder read STALLED while it was working perfectly - a health check
#: reporting on the wrong property. The fix belongs here, not in a weakened threshold: the
#: daemon proves it is alive on the cadence the check asks about, and STALLED then means the
#: process actually died.
HEARTBEAT_SECONDS = 60.0
#: How often to re-poll history for newly settled funding. Settlements are 8h apart, so this
#: is deliberately far more frequent than needed - it costs one small request.
HISTORY_REFRESH_SECONDS = 600.0


def args_default_poll_interval() -> float:
    """The shipped default cadence, read off the parser rather than restated."""
    return float(HEARTBEAT_SECONDS)


def stall_budget_seconds() -> float:
    """The stall threshold this recorder must beat, READ FROM the module that enforces it.

    Importing it means tightening `recorder_health.STALL_AFTER_MS` below this recorder's
    cadence breaks the selftest here, instead of silently turning a healthy recorder red. A
    hard-coded copy of the number would assert nothing.
    """
    try:
        from recorder_health import STALL_AFTER_MS
        return float(STALL_AFTER_MS) / 1000.0
    except Exception:
        return 900.0


STALL_MS_BUDGET = stall_budget_seconds()


def _http_json(url: str, timeout: float = 20.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


class PayloadRejected(Exception):
    """A response field that must be real was not. The payload is quarantined, never coerced."""


def _f(value, default: float = 0.0) -> float:
    """OPTIONAL numbers only. Anything a study will divide by must use `require_*` below."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default: int = 0) -> int:
    """OPTIONAL integers only. See `_f`."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# MISSING IS NOT ZERO, AND THIS RECORDER BRIEFLY SAID IT WAS.
#
# `_f`/`_i` coerce any unparseable field to 0, and the settlement/basis writers fed every
# critical field through them. A malformed or partial Binance response therefore stored
#
#     fundingTime = 0     an epoch-1970 settlement, becoming a fake ~20-year schedule hole
#     fundingRate = 0     a real-looking 0 bps settlement diluting the mean
#     markPrice   = 0     a zero price recorded as market evidence
#     mark/index  = 0     basis_bps 0.00, reading as "no basis" instead of "unknown"
#
# and the carry study reads exactly these columns. A defaulted zero is indistinguishable
# downstream from a measured zero, which is the whole reason this repository keeps finding
# "absent read as pass". Critical fields now validate or the payload is quarantined as a gap.

def require_timestamp(value, field: str) -> int:
    ms = _i(value, default=0)
    if ms <= 0 or not (1_262_304_000_000 <= ms <= 4_102_444_800_000):   # 2010-01-01 .. 2100
        raise PayloadRejected(f"{field}={value!r} is not a plausible epoch-ms timestamp")
    return ms


def require_price(value, field: str) -> float:
    price = _f(value, default=float("nan"))
    if not (price > 0.0) or not math.isfinite(price):
        raise PayloadRejected(f"{field}={value!r} is not a positive finite price")
    return price


def optional_price(value):
    """A price that is legitimately ABSENT sometimes. Returns None, never 0.0.

    Binance's funding history genuinely omits `markPrice` on older settlements - it comes back
    as the empty string for every BTCUSDT row before 2023-10-31, 460 of the 3,500 recorded
    here, while `fundingRate` is perfectly good. Requiring it would have quarantined 13% of the
    dataset over a contextual field and destroyed real funding evidence.

    So the rule splits by ROLE, not by type: `fundingTime` and `fundingRate` are the evidence
    and must validate; the mark is context and may be missing. Missing is written as NULL,
    which is the one representation a study cannot mistake for a measured zero.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    price = _f(value, default=float("nan"))
    if not math.isfinite(price) or price <= 0.0:
        return None
    return price


def require_rate(value, field: str) -> float:
    """A funding rate MAY legitimately be zero or negative - only unparseable is rejected.

    So this one cannot use a sentinel default: 0.0 is a real observation here, which is
    precisely why it must not also be the failure value.
    """
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise PayloadRejected(f"{field}={value!r} is not a number") from None
    if not math.isfinite(rate) or abs(rate) > 0.1:      # +-10% per settlement is far past sane
        raise PayloadRejected(f"{field}={value!r} is not a plausible funding rate")
    return rate


class FundingStore:
    """Single-writer DuckDB, same conventions as btc_tick_recorder and l2_recorder."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.path)
        self._init_schema()
        row = self.conn.execute("""SELECT coalesce(max(seq), 0) FROM (
            SELECT seq FROM funding_settlements UNION ALL
            SELECT seq FROM funding_basis_samples UNION ALL
            SELECT seq FROM funding_gaps UNION ALL
            SELECT seq FROM funding_heartbeats)""").fetchone()
        self.next_seq = int(row[0]) + 1

    def close(self) -> None:
        self.conn.close()

    def disk_bytes(self) -> int:
        return sum(Path(c).stat().st_size for c in (self.path, f"{self.path}.wal")
                   if Path(c).exists())

    def _init_schema(self) -> None:
        # funding_time_ms is the SETTLEMENT instant; recv_ts_ns is when we learned of it.
        # Two moments, kept apart - the same rule the funding-notional fix (5.29) established.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS funding_settlements(
            symbol VARCHAR, funding_time_ms BIGINT, seq BIGINT,
            recv_ts_ns BIGINT, funding_rate DOUBLE, funding_rate_bps DOUBLE,
            mark_price DOUBLE, source VARCHAR,
            PRIMARY KEY (symbol, funding_time_ms))""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS funding_basis_samples(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, exchange_ts_ms BIGINT,
            symbol VARCHAR, mark_price DOUBLE, index_price DOUBLE,
            premium DOUBLE, basis_bps DOUBLE,
            last_funding_rate DOUBLE, next_funding_time_ms BIGINT,
            seconds_to_funding DOUBLE, transport_lag_ms BIGINT)""")
        # A missing settlement on a FIXED schedule is evidence, so it gets a row.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS funding_gaps(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, symbol VARCHAR, kind VARCHAR,
            previous_time_ms BIGINT, current_time_ms BIGINT, missing_intervals BIGINT,
            detail VARCHAR)""")
        # `endpoints_healthy` exists so a heartbeat can prove LIVENESS while still reporting
        # that an endpoint is failing. Those are two different facts and the earlier version
        # collapsed them: a heartbeat suppressed on fetch failure makes a live process read
        # STALLED, which is the same "check reporting on the wrong property" defect this file
        # was already written to avoid. The failure is recorded as a gap row AND flagged here.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS funding_heartbeats(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, uptime_seconds DOUBLE,
            settlements BIGINT, basis_samples BIGINT, gaps BIGINT, sign_flips BIGINT,
            disk_bytes BIGINT, endpoints_healthy BOOLEAN)""")
        existing = {r[0] for r in self.conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'funding_heartbeats'").fetchall()}
        if "endpoints_healthy" not in existing:      # database written before the flag existed
            self.conn.execute("ALTER TABLE funding_heartbeats "
                              "ADD COLUMN endpoints_healthy BOOLEAN")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS funding_runs(
            run_id VARCHAR PRIMARY KEY, started_ns BIGINT, host VARCHAR, platform VARCHAR,
            symbol VARCHAR, endpoints VARCHAR, notes VARCHAR)""")

    def sequence(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def register_run(self, run_id: str, started_ns: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO funding_runs VALUES (?,?,?,?,?,?,?)", [
            run_id, started_ns, socket.gethostname(), platform.platform(), SYMBOL,
            f"{FUNDING_HISTORY_URL};{PREMIUM_INDEX_URL}",
            "public endpoints only; no credentials, no orders. funding_time_ms is the "
            "settlement instant and recv_ts_ns is when it was learned - two moments."])

    def settlement(self, row: dict, recv_ns: int, source: str) -> bool:
        """Returns True when this settlement was new. Raises PayloadRejected on a bad row."""
        ft = require_timestamp(row.get("fundingTime"), "fundingTime")
        rate = require_rate(row.get("fundingRate"), "fundingRate")
        mark = optional_price(row.get("markPrice"))     # genuinely absent before 2023-10-31
        existing = self.conn.execute(
            "SELECT 1 FROM funding_settlements WHERE symbol = ? AND funding_time_ms = ?",
            [str(row.get("symbol", SYMBOL)), ft]).fetchone()
        if existing:
            return False
        self.conn.execute("INSERT INTO funding_settlements VALUES (?,?,?,?,?,?,?,?)", [
            str(row.get("symbol", SYMBOL)), ft, self.sequence(), recv_ns,
            rate, rate * 10_000.0, mark, source])
        return True

    def basis_sample(self, payload: dict, recv_ns: int) -> int:
        """Raises PayloadRejected rather than recording a zero mark, index or basis."""
        mark = require_price(payload.get("markPrice"), "markPrice")
        index = require_price(payload.get("indexPrice"), "indexPrice")
        # These two are genuinely optional: absent stays absent rather than becoming a number.
        exch = _i(payload.get("time"))
        nxt = _i(payload.get("nextFundingTime"))
        seq = self.sequence()
        self.conn.execute("INSERT INTO funding_basis_samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, exch, str(payload.get("symbol", SYMBOL)), mark, index,
            mark - index,
            ((mark - index) / index * 10_000.0) if index > 0 else 0.0,
            _f(payload.get("lastFundingRate")), nxt,
            (nxt - (recv_ns // 1_000_000)) / 1000.0 if nxt else 0.0,
            (recv_ns // 1_000_000) - exch if exch else 0])
        return seq

    def gap(self, recv_ns: int, kind: str, previous_ms: int, current_ms: int,
            missing: int, detail: str = "") -> int:
        seq = self.sequence()
        self.conn.execute("INSERT INTO funding_gaps VALUES (?,?,?,?,?,?,?,?)", [
            seq, recv_ns, SYMBOL, kind, previous_ms, current_ms, missing, detail])
        return seq

    def heartbeat(self, recv_ns: int, uptime: float, counters: dict,
                  endpoints_healthy: bool = True) -> int:
        seq = self.sequence()
        self.conn.execute("INSERT INTO funding_heartbeats VALUES (?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, uptime, counters.get("settlements", 0),
            counters.get("basis", 0), counters.get("gaps", 0),
            counters.get("sign_flips", 0), self.disk_bytes(), bool(endpoints_healthy)])
        return seq

    # -- analysis the lane actually needs -------------------------------------------------
    def observed_interval_ms(self) -> int | None:
        """The settlement cadence THIS symbol actually publishes, or None if not yet knowable.

        `FUNDING_INTERVAL_MS` is 8h because that is BTCUSDT's schedule, but `BTC_FUNDING_SYMBOL`
        is configurable and Binance runs 4h funding on some contracts - and can change a
        contract's interval. A frozen constant against a configurable symbol turns every normal
        settlement into a "missing" one, which is a fabricated gap in an evidence table.

        The recorded settlements already carry the answer, so it is measured rather than
        declared: the modal spacing between consecutive settlements. Falls back to the constant
        only while too few rows exist to measure anything.
        """
        rows = self.conn.execute(
            "SELECT funding_time_ms FROM funding_settlements WHERE symbol = ? "
            "ORDER BY funding_time_ms", [SYMBOL]).fetchall()
        if len(rows) < 8:
            return None
        deltas = [b[0] - a[0] for a, b in zip(rows, rows[1:]) if b[0] > a[0]]
        if not deltas:
            return None
        return max(set(deltas), key=deltas.count)

    def schedule_interval_ms(self) -> tuple[int, str]:
        observed = self.observed_interval_ms()
        if observed and observed > 0:
            return observed, "observed"
        return FUNDING_INTERVAL_MS, "declared_default"

    def audit_schedule(self, recv_ns: int) -> int:
        """Write a gap row for every hole in the OBSERVED settlement schedule.

        There is no per-message counter on this stream, so TIME is the only honest detector.
        Using an id-continuity test here would repeat the bookTicker mistake the tick recorder
        already made once.

        The cadence comes from `schedule_interval_ms`, measured from the settlements themselves
        rather than assumed to be 8h - the symbol is configurable and not every contract funds
        on the same clock. Judging a 4h symbol against an 8h constant would report a fabricated
        hole at every single settlement.
        """
        interval, source = self.schedule_interval_ms()
        rows = self.conn.execute(
            "SELECT funding_time_ms FROM funding_settlements WHERE symbol = ? "
            "ORDER BY funding_time_ms", [SYMBOL]).fetchall()
        written = 0
        for (prev,), (cur,) in zip(rows, rows[1:]):
            delta = cur - prev
            if delta > interval + SETTLEMENT_TOLERANCE_MS:
                missing = max(1, round(delta / interval) - 1)
                exists = self.conn.execute(
                    "SELECT 1 FROM funding_gaps WHERE previous_time_ms = ? AND "
                    "current_time_ms = ? AND kind = 'missing_settlement'",
                    [prev, cur]).fetchone()
                if not exists:
                    self.gap(recv_ns, "missing_settlement", prev, cur, missing,
                             f"{delta / 3_600_000:.1f}h between settlements; cadence "
                             f"{interval / 3_600_000:.1f}h ({source})")
                    written += 1
        return written

    def summary(self) -> dict:
        row = self.conn.execute("""
            SELECT COUNT(*), MIN(funding_time_ms), MAX(funding_time_ms),
                   AVG(funding_rate_bps), MEDIAN(funding_rate_bps),
                   SUM(CASE WHEN funding_rate_bps > 0 THEN 1 ELSE 0 END),
                   MIN(funding_rate_bps), MAX(funding_rate_bps)
            FROM funding_settlements WHERE symbol = ?""", [SYMBOL]).fetchone()
        flips = self.conn.execute("""
            WITH s AS (SELECT funding_rate_bps r,
                              LAG(funding_rate_bps) OVER (ORDER BY funding_time_ms) p
                       FROM funding_settlements WHERE symbol = ?)
            SELECT COUNT(*) FROM s WHERE p IS NOT NULL AND SIGN(r) <> SIGN(p)""",
            [SYMBOL]).fetchone()
        return {"count": row[0], "first_ms": row[1], "last_ms": row[2],
                "mean_bps": row[3], "median_bps": row[4], "positive": row[5],
                "min_bps": row[6], "max_bps": row[7], "sign_flips": flips[0]}


def backfill(store: FundingStore, days: int, counters: dict) -> tuple[int, bool]:
    """Pull historical settlements, newest-first pages walked backwards."""
    end_ms = int(time.time() * 1000)
    earliest = end_ms - days * 86_400_000
    added = 0
    ok = True
    while end_ms > earliest:
        url = (f"{FUNDING_HISTORY_URL}?symbol={SYMBOL}&limit={PAGE_LIMIT}"
               f"&endTime={end_ms}")
        try:
            page = _http_json(url)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            store.gap(time.time_ns(), "history_fetch_failed", 0, end_ms, 0,
                      f"{type(exc).__name__}: {exc}"[:200])
            counters["gaps"] += 1
            ok = False
            break
        if not page:
            break
        recv = time.time_ns()
        for row in page:
            if _i(row.get("fundingTime")) < earliest:
                continue
            try:
                if store.settlement(row, recv, "fapi_v1_fundingRate"):
                    added += 1
                    counters["settlements"] += 1
            except PayloadRejected as exc:
                # Quarantined, not coerced, and not silent: a rejected row is a gap row.
                store.gap(recv, "settlement_payload_rejected", 0,
                          _i(row.get("fundingTime")), 0, str(exc)[:200])
                counters["gaps"] += 1
                ok = False
        usable = [_i(r.get("fundingTime")) for r in page if _i(r.get("fundingTime")) > 0]
        if not usable:
            # Every row on this page was unreadable; walking back from a coerced 0 would
            # restart the whole history at the epoch.
            store.gap(recv, "history_page_unusable", 0, end_ms, 0,
                      f"{len(page)} rows, no usable fundingTime")
            counters["gaps"] += 1
            ok = False
            break
        oldest = min(usable)
        if oldest >= end_ms:
            break
        end_ms = oldest - 1
        time.sleep(PAGE_SLEEP_S)
    return added, ok


def poll_once(store: FundingStore, counters: dict) -> bool:
    recv = time.time_ns()
    try:
        payload = _http_json(f"{PREMIUM_INDEX_URL}?symbol={SYMBOL}")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        store.gap(recv, "premium_index_failed", 0, 0, 0,
                  f"{type(exc).__name__}: {exc}"[:200])
        counters["gaps"] += 1
        return False
    try:
        store.basis_sample(payload, recv)
    except PayloadRejected as exc:
        store.gap(recv, "premium_index_payload_rejected", 0, 0, 0, str(exc)[:200])
        counters["gaps"] += 1
        return False
    counters["basis"] += 1
    return True


def _selftest() -> int:
    import tempfile
    failures = []

    def chk(cond, msg):
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    print("FUNDING RECORDER")
    tmp_root = Path(tempfile.mkdtemp(prefix="funding_"))
    tmp = tmp_root / "f.duckdb"
    store = FundingStore(tmp)
    try:
        base = 1_800_000_000_000
        base -= base % FUNDING_INTERVAL_MS
        recv = base * 1_000_000
        rows = [{"symbol": SYMBOL, "fundingTime": base + i * FUNDING_INTERVAL_MS,
                 "fundingRate": r, "markPrice": 60000.0}
                for i, r in enumerate([0.0001, 0.00005, -0.00002, 0.00008])]
        for r in rows:
            store.settlement(r, recv, "test")
        chk(store.settlement(rows[0], recv, "test") is False,
            "a settlement already recorded is not written twice - the history endpoint is "
            "paginated and pages overlap")

        s = store.summary()
        chk(s["count"] == 4 and abs(s["max_bps"] - 1.0) < 1e-9,
            f"rates are stored in BOTH raw and bps form ({s['max_bps']:.2f} bps max)")
        chk(s["sign_flips"] == 2,
            f"sign flips are counted ({s['sign_flips']}) - a carry hedge dies when funding "
            f"turns against it, and that frequency IS the question the lane needs")

        # A hole in the fixed schedule.
        store.settlement({"symbol": SYMBOL,
                          "fundingTime": base + 7 * FUNDING_INTERVAL_MS,
                          "fundingRate": 0.0001, "markPrice": 60000.0}, recv, "test")
        written = store.audit_schedule(recv)
        g = store.conn.execute(
            "SELECT missing_intervals, kind FROM funding_gaps").fetchone()
        chk(written == 1 and g[0] == 3 and g[1] == "missing_settlement",
            f"a hole in the 8-hourly schedule is WRITTEN as {g[0]} missing settlements - "
            f"there is no id to check, so TIME is the only honest detector")
        chk(store.audit_schedule(recv) == 0,
            "and auditing twice does not duplicate the gap row")

        store.basis_sample({"symbol": SYMBOL, "markPrice": 60050.0, "indexPrice": 60000.0,
                            "lastFundingRate": 0.0001, "nextFundingTime": base,
                            "time": base - 20}, recv)
        b = store.conn.execute(
            "SELECT premium, basis_bps, transport_lag_ms FROM funding_basis_samples").fetchone()
        chk(abs(b[0] - 50.0) < 1e-6 and abs(b[1] - 8.333) < 0.01,
            f"basis is DERIVED from mark and index ({b[1]:.2f} bps), not inherited from a "
            f"column whose construction cannot be verified")
        chk(b[2] == 20, f"and transport lag is kept separate ({b[2]}ms)")

        store.heartbeat(recv, 30.0, {"settlements": 5, "basis": 1, "gaps": 1, "sign_flips": 2})
        h = store.conn.execute(
            "SELECT settlements, gaps, sign_flips FROM funding_heartbeats").fetchone()
        chk(h == (5, 1, 2),
            "heartbeats carry the counters, so 'no settlement this window' and 'not running' "
            "stay distinguishable")

        # A degraded endpoint must not be reported as a dead process.
        store.heartbeat(recv + 1, 31.0, {"settlements": 5}, endpoints_healthy=False)
        beats = store.conn.execute("SELECT endpoints_healthy FROM funding_heartbeats "
                                   "ORDER BY seq").fetchall()
        chk([b[0] for b in beats] == [True, False] and len(beats) == 2,
            "a FAILING endpoint still writes a heartbeat, flagged unhealthy - liveness and "
            "endpoint health are two facts, and suppressing the beat made a live recorder "
            "read STALLED")
        # MISSING IS NOT ZERO. Each of these previously became a stored 0.
        for bad, field in (({"symbol": SYMBOL, "fundingTime": None, "fundingRate": 0.0001,
                             "markPrice": 60000.0}, "fundingTime"),
                           ({"symbol": SYMBOL, "fundingTime": base, "fundingRate": "n/a",
                             "markPrice": 60000.0}, "fundingRate"),
                           ({"symbol": SYMBOL, "fundingTime": "junk", "fundingRate": 0.0001,
                             "markPrice": 60000.0}, "fundingTime (unparseable)")):
            try:
                store.settlement(bad, recv, "test")
                chk(False, f"a malformed {field} must be REJECTED, not stored as 0")
            except PayloadRejected:
                chk(True, f"a malformed {field} raises instead of being coerced to 0 - a "
                          f"defaulted zero is indistinguishable from a measured one")
        try:
            store.basis_sample({"symbol": SYMBOL, "markPrice": 0.0, "indexPrice": 60000.0}, recv)
            chk(False, "a zero mark price must be REJECTED")
        except PayloadRejected:
            chk(True, "a zero mark price is rejected rather than recorded as basis_bps 0.00, "
                      "which would read as 'no basis' instead of 'unknown'")
        chk(require_rate(0.0, "r") == 0.0 and require_rate(-0.0002, "r") < 0,
            "but a funding rate of exactly 0 or a negative one is a REAL observation and "
            "survives - which is why it must not double as the failure value")

        # REAL Binance behaviour, not a hypothetical: markPrice comes back as "" for every
        # BTCUSDT settlement before 2023-10-31. Requiring it would quarantine 460 of the 3,500
        # recorded settlements - good funding evidence thrown away over a contextual field.
        store.settlement({"symbol": SYMBOL, "fundingTime": base + 20 * FUNDING_INTERVAL_MS,
                          "fundingRate": 0.00002344, "markPrice": ""}, recv, "test")
        got = store.conn.execute(
            "SELECT mark_price, funding_rate_bps FROM funding_settlements "
            "WHERE funding_time_ms = ?", [base + 20 * FUNDING_INTERVAL_MS]).fetchone()
        chk(got is not None and got[0] is None and abs(got[1] - 0.2344) < 1e-6,
            "an ABSENT markPrice is kept as NULL with its funding rate intact - not coerced "
            "to 0 and not grounds for discarding a real settlement")
        chk(optional_price("") is None and optional_price(0) is None
            and optional_price("60000.5") == 60000.5,
            "optional_price returns None for absent and zero alike - NULL is the one value a "
            "study cannot mistake for a measurement")

        # Cadence is measured, not assumed: the symbol is configurable and not every contract
        # funds every 8h.
        chk(store.schedule_interval_ms() == (FUNDING_INTERVAL_MS, "declared_default"),
            "with too few rows the cadence falls back to the declared default, labelled so")
        four_h = 4 * 60 * 60 * 1000
        b2 = base + 50 * FUNDING_INTERVAL_MS
        for i in range(10):
            store.settlement({"symbol": SYMBOL, "fundingTime": b2 + i * four_h,
                              "fundingRate": 0.0001, "markPrice": 60000.0}, recv, "test")
        interval, src = store.schedule_interval_ms()
        chk(interval == four_h and src == "observed",
            f"a 4h-funding symbol is MEASURED at {interval / 3_600_000:.0f}h, so the cadence "
            f"is not inherited from a constant that describes a different contract")

        # And audit_schedule must USE it. Asserted on behaviour: a real hole in a 4h schedule
        # is an 8h delta, which the hardcoded 8h threshold waves through - so the failure of a
        # frozen constant here is a MISSED hole, not a fabricated one.
        with tempfile.TemporaryDirectory(prefix="funding4h_") as t4:
            s4 = FundingStore(Path(t4) / "f4.duckdb")
            try:
                stamps = [i for i in range(12) if i != 8]      # one settlement genuinely absent
                for i in stamps:
                    s4.settlement({"symbol": SYMBOL, "fundingTime": b2 + i * four_h,
                                   "fundingRate": 0.0001, "markPrice": 60000.0}, recv, "test")
                found = s4.audit_schedule(recv)
                row = s4.conn.execute("SELECT missing_intervals, detail FROM funding_gaps "
                                      "WHERE kind = 'missing_settlement'").fetchone()
                chk(found == 1 and row and row[0] == 1 and "4.0h (observed)" in row[1],
                    "a REAL hole in a 4h schedule is detected as 1 missing settlement - an "
                    "8h constant would treat that same 8h delta as normal and miss it")
            finally:
                s4.close()

        chk(args_default_poll_interval() < STALL_MS_BUDGET,
            f"the default poll cadence ({args_default_poll_interval():.0f}s) stays inside "
            f"recorder_health.STALL_AFTER_MS ({STALL_MS_BUDGET:.0f}s), so ADVANCING means "
            f"alive rather than lucky")
    finally:
        store.close()
        shutil.rmtree(tmp_root)

    print("\n" + ("FUNDING RECORDER SELFTEST: FAIL" if failures
                  else "FUNDING RECORDER SELFTEST: PASS"))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--backfill-days", type=int, default=0,
                        help="pull this many days of historical settlements, then exit "
                             "unless --poll-seconds is also given")
    parser.add_argument("--poll-seconds", type=float, default=None,
                        help="sample mark/index basis for this long after any backfill")
    parser.add_argument("--poll-interval", type=float, default=HEARTBEAT_SECONDS,
                        help="basis sample cadence; also the liveness cadence, which is why "
                             "it must stay well inside recorder_health.STALL_AFTER_MS")
    parser.add_argument("--funding-refresh-interval", type=float,
                        default=HISTORY_REFRESH_SECONDS,
                        help="seconds between official funding-history refreshes in live mode")
    parser.add_argument("--forever", action="store_true",
                        help="keep polling basis and official settlements until interrupted")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    store = FundingStore(args.db)
    started = time.time_ns()
    counters = {"settlements": 0, "basis": 0, "gaps": 0, "sign_flips": 0}
    store.register_run(f"funding_{started}", started)
    try:
        history_ok = True
        if args.backfill_days > 0:
            print(f"[funding] backfilling {args.backfill_days} days of {SYMBOL} settlements")
            added, history_ok = backfill(store, args.backfill_days, counters)
            print(f"[funding] {added:,} new settlements")
        holes = store.audit_schedule(time.time_ns())
        if holes:
            print(f"[funding] {holes} schedule holes recorded")

        if args.forever or args.poll_seconds:
            deadline = None if args.forever else time.monotonic() + args.poll_seconds
            next_history_refresh = time.monotonic() + max(
                30.0, args.funding_refresh_interval
            )
            print(f"[funding] sampling basis every {args.poll_interval:.0f}s")
            while deadline is None or time.monotonic() < deadline:
                basis_ok = poll_once(store, counters)
                now = time.monotonic()
                if now >= next_history_refresh:
                    _added, history_ok = backfill(store, 3, counters)
                    store.audit_schedule(time.time_ns())
                    next_history_refresh = now + max(30.0, args.funding_refresh_interval)
                # Unconditional. A heartbeat answers "is this process alive", and the answer
                # is yes even when an endpoint is refusing - that failure already has a gap
                # row and now rides along as a flag. Gating liveness on endpoint health is
                # what made a working recorder read STALLED.
                counters["sign_flips"] = store.summary()["sign_flips"]
                store.heartbeat(time.time_ns(), (time.time_ns() - started) / 1e9,
                                counters, endpoints_healthy=basis_ok and history_ok)
                sleep_for = max(1.0, args.poll_interval)
                if deadline is not None:
                    sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
                time.sleep(sleep_for)

        s = store.summary()
        counters["sign_flips"] = s["sign_flips"]
        store.heartbeat(time.time_ns(), (time.time_ns() - started) / 1e9, counters,
                        endpoints_healthy=history_ok)
        if s["count"]:
            import datetime
            fmt = lambda ms: datetime.datetime.fromtimestamp(  # noqa: E731
                ms / 1000, datetime.UTC).strftime("%Y-%m-%d")
            print(f"[funding] {s['count']:,} settlements  {fmt(s['first_ms'])} .. "
                  f"{fmt(s['last_ms'])}")
            print(f"[funding] mean {s['mean_bps']:+.4f} bps/8h   median "
                  f"{s['median_bps']:+.4f}   range [{s['min_bps']:+.3f}, {s['max_bps']:+.3f}]")
            print(f"[funding] positive {s['positive']:,}/{s['count']:,} "
                  f"({s['positive'] / s['count']:.1%})   sign flips {s['sign_flips']:,}")
            print(f"[funding] basis samples {counters['basis']:,}   "
                  f"disk {store.disk_bytes() / 1e6:.1f}MB")
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
