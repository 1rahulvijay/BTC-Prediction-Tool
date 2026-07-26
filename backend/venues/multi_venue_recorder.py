"""
multi_venue_recorder.py — synchronized event-time collection across BTC venues
==============================================================================
Priority-3 collector. Its purpose is NOT research: it is to capture data that **cannot be
reconstructed later**. Binance trades and klines are backfillable from daily archives; what is
gone forever is the *event-time cross-venue picture* — who moved first, what the local receive
latency was, and what the book looked like at that instant.

Records, for every event, the three timestamps that make lead-lag analysis honest:

    exch_ts   the venue's own event timestamp
    recv_ts   when this process received it
    seq       venue sequence/update id, for gap detection

Streams (all public, no credentials, read-only — this process CANNOT trade):

    binance_spot   BTCUSDT   bookTicker (best bid/ask + size) · aggTrade
    binance_perp   BTCUSDT   bookTicker · aggTrade · markPrice (mark/index/funding, 1s)
    bybit_perp     BTCUSDT   orderbook.1 · publicTrade
    coinbase       BTC-USD   ticker

Design rules:
  * append-only; no row is ever updated (immutable raw events)
  * one DuckDB writer, batched inserts, WAL-friendly
  * a venue failing or disconnecting never stops the others (independent supervised tasks)
  * clock drift vs each venue is measured continuously, not assumed
  * a --smoke run writes to :memory: and never touches the evidence DB

Usage:
    python backend/venues/multi_venue_recorder.py                 # collect
    python backend/venues/multi_venue_recorder.py --smoke         # 20s, in-memory, prints stats
    python backend/venues/multi_venue_recorder.py --report        # summarize what has been stored
    python backend/venues/multi_venue_recorder.py --selftest      # no network; schema/parse checks
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import defaultdict, deque

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BTC_VENUE_DB") or os.path.join(DATA, "multi_venue.duckdb")
BATCH, FLUSH_S = 500, 2.0

STREAMS = {
    "binance_spot": ("wss://stream.binance.com:9443/stream?streams="
                     "btcusdt@bookTicker/btcusdt@aggTrade"),
    # Perp WS carries bookTicker ONLY from this host - aggTrade and markPrice@1s deliver zero
    # messages (measured 2026-07-26). Those two arrive via _binance_perp_rest instead. Do not
    # "fix" this by re-adding them here; it fails silently, which is how the gap went unnoticed.
    "binance_perp": "wss://fstream.binance.com/stream?streams=btcusdt@bookTicker",
    "bybit_perp": "wss://stream.bybit.com/v5/public/linear",
    "coinbase": "wss://ws-feed.exchange.coinbase.com",
}

# Streams that MUST produce rows in a healthy run. A silent zero here means a venue changed
# something - the collector reports it rather than quietly recording an incomplete picture.
# bybit_perp/publicTrade added 2026-07-26: the Binance V1 preregistration names Bybit public
# trades as CLASS-A input for trade imbalance and directional flow. Omitting it from the health
# gate let an episode qualify while a REQUIRED strategy input was entirely absent - the health
# report read 8/8 while the data was incomplete. Required health is now 9/9.
EXPECTED = ("binance_spot/bookTicker", "binance_spot/aggTrade", "binance_perp/bookTicker",
            "binance_perp/aggTrade_rest", "binance_perp/premiumIndex",
            "binance_perp/openInterest", "bybit_perp/orderbook.1", "bybit_perp/publicTrade",
            "coinbase/ticker")
REST_EXPECTED = {
    "binance_perp/aggTrade_rest",
    "binance_perp/premiumIndex",
    "binance_perp/openInterest",
}

try:
    from .venue_admissibility import basis_for
except ImportError:                                   # run as a script, not imported as a package
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from venue_admissibility import basis_for

COLS = ("recv_ts exch_ts venue stream symbol event seq event_key bid bid_size ask ask_size "
        "price size side extra source_mode poll_id timestamp_basis "
        "process_start_id connection_id queue_delay_ms processing_delay_ms").split()
_TEXT = {"venue", "stream", "symbol", "event", "side", "extra", "source_mode",
         "timestamp_basis", "event_key"}

# `seq` and `event_key` are NOT the same thing and must not be conflated.
#   seq        the venue's own update/sequence number - used for GAP DETECTION
#   event_key  a STABLE NATURAL IDENTITY for the observation - used for DEDUPLICATION
# A synthetic poll-local counter would be useless as an identity: it changes on every reconnect,
# so the same observation re-fetched by a fresh process would not be recognised as a repeat.
# Identity per stream:
#   aggTrade / aggTrade_rest   venue (aggregate) trade id
#   bookTicker / orderbook.1   venue update id
#   premiumIndex / openInterest   instrument + venue publication timestamp + PAYLOAD HASH
#     (2026-07-26: timestamp alone cannot separate an exact duplicate from a same-timestamp
#      revision. same ts + same hash = duplicate, earliest recv_ts wins; same ts + different
#      hash = revision -- both retained, each usable only from its own recv_ts, so a
#      correction is never back-dated onto the original's arrival.)
# A REST row that cannot produce one is recorded, but is barred from features (see
# venue_admissibility.admissible_sql) precisely because it could not survive a reconnect intact.

# One 5-minute research episode = the independent unit declared in
# PREREG_BINANCE_VOLATILITY_MOMENTUM_V1 section 8. An episode only counts if the streams it
# needs were actually healthy for it - a running process is NOT the same as valid data.
EPISODE_S = 300

# FROZEN EPISODE HEALTH LIMITS - declared 2026-07-26, BEFORE any M0 score exists.
# D2 (2026-07-26): these ages were already being measured and written to venue_episodes, but were
# never used in the qualification decision - so an episode whose feeds were minutes stale still
# counted as evidence. They are now gating conditions.
#   REST 60s: the same limit venue_admissibility already enforces for Class-B observables, so an
#             episode cannot qualify on data a feature would refuse.
#   WS    5s: observed steady state is tens of milliseconds, so this is ~100x looser than normal
#             operation. It excludes malfunction (a wedged-but-"connected" socket), not jitter.
# REVISING EITHER AFTER SEEING AN M0 RESULT INVALIDATES THE EXPERIMENT.
REST_MAX_AGE_MS = 60_000.0
WS_MAX_AGE_MS = 5_000.0


def init_db(path=None):
    import duckdb
    con = duckdb.connect(path or DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS venue_events(" + ", ".join(
        c + (" VARCHAR" if c in _TEXT else " DOUBLE") for c in COLS) + ")")
    for c in COLS:
        con.execute(f"ALTER TABLE venue_events ADD COLUMN IF NOT EXISTS {c} "
                    f"{'VARCHAR' if c in _TEXT else 'DOUBLE'}")
    # Clock drift is measured, never assumed: exch_ts - recv_ts per venue per minute.
    con.execute("""CREATE TABLE IF NOT EXISTS venue_clock(
        minute_ts BIGINT, venue VARCHAR, n BIGINT, drift_med_ms DOUBLE,
        drift_p95_ms DOUBLE, PRIMARY KEY(minute_ts, venue))""")
    # Episode health. Persisted rather than derived, because raw events alone cannot distinguish
    # "no trades occurred" from "the stream was down" - and that difference decides whether an
    # episode is admissible evidence. Outages are EXCLUDED explicitly, never interpolated.
    con.execute("""CREATE TABLE IF NOT EXISTS venue_episodes(
        episode_start BIGINT PRIMARY KEY, episode_end BIGINT,
        stream_counts VARCHAR, streams_live INT, streams_required INT,
        max_ws_age_ms DOUBLE, max_rest_age_ms DOUBLE, reconnects INT,
        qualifying BOOLEAN, exclusion_reason VARCHAR)""")
    for c, t in (("max_ws_age_ms", "DOUBLE"), ("max_rest_age_ms", "DOUBLE")):
        con.execute(f"ALTER TABLE venue_episodes ADD COLUMN IF NOT EXISTS {c} {t}")
    # The evidence clock starts at the first PERSISTENT write, never a smoke run.
    con.execute("""CREATE TABLE IF NOT EXISTS venue_collection_meta(
        k VARCHAR PRIMARY KEY, v VARCHAR)""")
    # Who observed it. Receive-time ordering is a property of THIS observer, so the observer has
    # to be identifiable: rows carry process_start_id/connection_id, and this table resolves them
    # to a host and a code version. Without it, "A arrived before B" is an unattributable claim.
    con.execute("""CREATE TABLE IF NOT EXISTS collector_sessions(
        process_start_id BIGINT PRIMARY KEY, collector_host_id VARCHAR, pid BIGINT,
        started_ts DOUBLE, code_sha256 VARCHAR, admissibility_sha256 VARCHAR)""")
    return con


def _sha256_of(path):
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except Exception:
        return None


# ---------------------------------------------------------------- parsers
# Each returns a list of row-dicts. Pure functions of (payload, recv_ts) so they are unit-testable
# without a network - see selftest().

def parse_binance(msg: dict, recv: float, venue: str):
    d = msg.get("data") or msg
    e = d.get("e") or ("bookTicker" if "b" in d and "a" in d else None)
    # SPOT bookTicker carries NO event time (measured 2026-07-26: no E, no T - only u,s,b,B,a,A).
    # FUTURES bookTicker does. Storing a missing timestamp as 0.0 made recv_ts-exch_ts read as
    # ~1.79e12 ms, i.e. the whole Unix epoch, so it MUST stay NULL: for that stream, recv_ts is
    # the only honest time and cross-venue lead-lag on it can only be measured in receive time.
    _e = d.get("E") or d.get("T")
    ts = (float(_e) / 1000.0) if _e else None
    if e == "bookTicker" or (e is None and "b" in d):
        return [{"recv_ts": recv, "exch_ts": ts, "venue": venue, "stream": "bookTicker", "source_mode": "WS",
                 "symbol": d.get("s", "BTCUSDT"), "event": "quote",
                 "seq": float(d.get("u") or 0.0),
                 "event_key": (f"u:{d['u']}" if d.get("u") is not None else None),
                 "bid": float(d["b"]), "bid_size": float(d["B"]),
                 "ask": float(d["a"]), "ask_size": float(d["A"])}]
    if e == "aggTrade":
        return [{"recv_ts": recv, "exch_ts": ts, "venue": venue, "stream": "aggTrade", "source_mode": "WS",
                 "symbol": d.get("s", "BTCUSDT"), "event": "trade",
                 "seq": float(d.get("a") or 0.0),
                 "event_key": (f"a:{d['a']}" if d.get("a") is not None else None),
                 "price": float(d["p"]),
                 "size": float(d["q"]),
                 # Binance 'm' = buyer is maker -> the aggressor was the SELLER
                 "side": "sell" if d.get("m") else "buy"}]
    if e == "markPriceUpdate":
        return [{"recv_ts": recv, "exch_ts": ts, "venue": venue, "stream": "markPrice", "source_mode": "WS",
                 "symbol": d.get("s", "BTCUSDT"), "event": "mark",
                 "event_key": (f"t:{_e}" if _e else None),
                 "price": float(d.get("p") or 0.0),
                 "extra": json.dumps({"index": d.get("i"), "funding": d.get("r"),
                                      "next_funding_ts": d.get("T")}, separators=(",", ":"))}]
    return []


def parse_bybit(msg: dict, recv: float):
    _t = msg.get("ts")
    topic, ts = msg.get("topic", ""), (float(_t) / 1000.0) if _t else None
    data = msg.get("data")
    if not data:
        return []
    if topic.startswith("orderbook"):
        b, a = (data.get("b") or [[None, None]])[0], (data.get("a") or [[None, None]])[0]
        if b[0] is None or a[0] is None:
            return []
        return [{"recv_ts": recv, "exch_ts": ts, "venue": "bybit_perp", "stream": "orderbook.1", "source_mode": "WS",
                 "symbol": data.get("s", "BTCUSDT"), "event": "quote",
                 "seq": float(data.get("u") or 0.0),
                 "event_key": (f"u:{data['u']}" if data.get("u") is not None else None),
                 "bid": float(b[0]), "bid_size": float(b[1]),
                 "ask": float(a[0]), "ask_size": float(a[1])}]
    if topic.startswith("publicTrade"):
        return [{"recv_ts": recv, "exch_ts": float(t.get("T") or 0.0) / 1000.0,
                 "venue": "bybit_perp", "stream": "publicTrade", "source_mode": "WS", "symbol": t.get("s", "BTCUSDT"),
                 "event": "trade", "event_key": (f"i:{t['i']}" if t.get("i") else None),
                 "price": float(t["p"]), "size": float(t["v"]),
                 "side": "buy" if t.get("S") == "Buy" else "sell"} for t in data]
    return []


def parse_coinbase(msg: dict, recv: float):
    if msg.get("type") != "ticker" or "price" not in msg:
        return []
    import datetime as _dt
    ts = None
    if msg.get("time"):
        try:
            ts = _dt.datetime.fromisoformat(msg["time"].replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = None
    return [{"recv_ts": recv, "exch_ts": ts, "venue": "coinbase", "stream": "ticker", "source_mode": "WS",
             "symbol": msg.get("product_id", "BTC-USD"), "event": "quote",
             "seq": float(msg.get("sequence") or 0.0),
             "event_key": (f"s:{msg['sequence']}" if msg.get("sequence") is not None else None),
             "bid": float(msg.get("best_bid") or 0.0),
             "bid_size": float(msg.get("best_bid_size") or 0.0),
             "ask": float(msg.get("best_ask") or 0.0),
             "ask_size": float(msg.get("best_ask_size") or 0.0),
             "price": float(msg["price"]),
             "side": ("buy" if msg.get("side") == "buy" else
                      "sell" if msg.get("side") == "sell" else None)}]


# ---------------------------------------------------------------- writer
class Writer:
    def __init__(self, con, persistent=True, process_start_id=None):
        self.con, self.buf, self.last = con, [], time.time()
        # Identifies the observing process. Receive-time order is only interpretable relative to a
        # specific observer on a specific host running specific code.
        self.process_start_id = int(process_start_id if process_start_id is not None
                                    else time.time() * 1000)
        self.connection_id = defaultdict(int)   # per venue: generation, incremented per reconnect
        self.queue_delay_ms = 0.0               # most recent measured event-loop scheduling lag
        self.counts = defaultdict(int)
        self.drift = defaultdict(lambda: deque(maxlen=4000))
        self.persistent = persistent
        self.boot_ts = time.time()
        self.ep_counts = defaultdict(int)      # per-episode RECEIVED (parsed), reset each episode
        # D4 (2026-07-26): qualification must use PERSISTED rows, not parsed ones. ep_counts
        # advances in add() before the rows reach DuckDB, so a failing writer previously left an
        # episode looking fully healthy while nothing had been stored. Parsed counts stay for
        # diagnostics; only ep_persisted decides whether an episode is evidence.
        self.ep_persisted = defaultdict(int)     # rows CONFIRMED written this episode
        self._pending_counts = defaultdict(int)  # parsed, awaiting a successful insert
        self.writer_errors = 0
        self.ep_writer_failed = False
        self.ep_start = int(self.boot_ts // EPISODE_S) * EPISODE_S
        self.ep_ws_age = 0.0
        self.ep_rest_age = 0.0
        # Arrival latency and feed silence are different failure modes. `ep_*_age` measures how
        # old an event was when received; `ep_*_silence` measures inter-arrival/tail gaps. The old
        # gate checked only the former, so a socket that emitted one fresh row and then went quiet
        # for four minutes still qualified.
        self.ep_ws_silence = 0.0
        self.ep_rest_silence = 0.0
        self._last_recv = {}
        self.reconnects = 0
        self.started_marked = False

    def register_session(self):
        """Record who is observing. Cheap, once per process, and the join key for every row."""
        if not self.persistent:
            return
        here = os.path.dirname(os.path.abspath(__file__))
        self.con.execute(
            "INSERT OR REPLACE INTO collector_sessions VALUES (?,?,?,?,?,?)",
            (self.process_start_id,
             os.environ.get("BTC_COLLECTOR_HOST_ID") or __import__("socket").gethostname(),
             os.getpid(), time.time(),
             _sha256_of(os.path.join(here, "multi_venue_recorder.py")),
             _sha256_of(os.path.join(here, "venue_admissibility.py"))))

    def mark_start(self):
        """Record the collection-start timestamp once, on the first PERSISTENT row.

        The evidence clock in the preregistration starts here - not when the process launched,
        and never on a --smoke run, which writes to :memory: and is discarded."""
        if self.started_marked or not self.persistent:
            return
        self.con.execute("INSERT OR IGNORE INTO venue_collection_meta VALUES (?,?)",
                         ("collection_start_ts", f"{time.time():.3f}"))
        self.started_marked = True

    def _put_episode(self, start, counts, ws_age, rest_age, reconnects, partial,
                     persisted=None, writer_failed=False):
        """Write one episode row. Non-qualifying episodes are stored WITH a reason, never dropped -
        silence would be indistinguishable from a healthy quiet period.

        QUALIFICATION USES PERSISTED ROWS AND ENFORCES THE AGE LIMITS (fixed 2026-07-26).
        Two defects were closed here:
          D2  max_ws_age_ms / max_rest_age_ms were recorded but never gated on, so an episode
              whose feeds were minutes stale still qualified as evidence.
          D4  `counts` came from parsed events, which advance before the rows reach DuckDB, so a
              failing writer left an episode looking fully healthy while nothing was stored.
        Both limits are frozen and DECLARED BEFORE any M0 score exists. CLASS_B_MAX_AGE_S is the
        same 60s the admissibility contract already enforces for REST; the WS limit is set an
        order of magnitude looser than observed steady state (tens of ms), so it excludes
        malfunction rather than normal operation. Revising either after seeing M0 invalidates
        the experiment.
        """
        counts = persisted if persisted is not None else counts
        live = [s for s in EXPECTED if counts.get(s)]
        missing = [s for s in EXPECTED if not counts.get(s)]
        why = []
        if partial:
            why.append("partial_window")
        if missing:
            why.append("missing:" + ",".join(missing))
        if reconnects:
            why.append(f"reconnects:{reconnects}")
        if writer_failed:
            why.append("writer_failed")
        if rest_age is not None and rest_age > REST_MAX_AGE_MS:
            why.append(f"rest_stale:{rest_age:.0f}ms>{REST_MAX_AGE_MS:.0f}")
        if ws_age is not None and ws_age > WS_MAX_AGE_MS:
            why.append(f"ws_stale:{ws_age:.0f}ms>{WS_MAX_AGE_MS:.0f}")
        self.con.execute(
            "INSERT OR REPLACE INTO venue_episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(start), int(start + EPISODE_S),
             json.dumps(dict(counts), separators=(",", ":")),
             len(live), len(EXPECTED), ws_age, rest_age, int(reconnects),
             not why, " ".join(why)))

    def close_episode(self, now=None):
        """Seal every episode window that has fully elapsed.

        Gaps are materialised as explicit excluded rows rather than left absent: a stalled or
        crashed collector must show up as `missing:...`, not as an episode that never existed."""
        now = now if now is not None else time.time()
        cur = int(now // EPISODE_S) * EPISODE_S
        if cur <= self.ep_start:
            return
        end = self.ep_start + EPISODE_S
        ws_health_age = self.ep_ws_age
        rest_health_age = self.ep_rest_age
        # Include silence from the final event to the episode boundary. Inter-arrival gaps were
        # accumulated in add(). A feed that produced one fresh row and then stalled now fails.
        for key in EXPECTED:
            last = self._last_recv.get(key)
            if last is None:
                continue
            tail_ms = max(0.0, (end - max(float(last), float(self.ep_start))) * 1000.0)
            if key in REST_EXPECTED:
                rest_health_age = max(rest_health_age, self.ep_rest_silence, tail_ms)
            else:
                ws_health_age = max(ws_health_age, self.ep_ws_silence, tail_ms)
        # The window in progress at boot is partial by construction; it cannot qualify.
        self._put_episode(
            self.ep_start, self.ep_counts, ws_health_age, rest_health_age,
            self.reconnects, partial=self.boot_ts > self.ep_start,
            persisted=self.ep_persisted, writer_failed=self.ep_writer_failed,
        )
        # Windows with no housekeeper tick at all (process stalled): record them as empty.
        gap = self.ep_start + EPISODE_S
        while gap < cur:
            self._put_episode(gap, {}, 0.0, 0.0, 0, partial=False)
            gap += EPISODE_S
        self.ep_counts = defaultdict(int)
        self.ep_persisted = defaultdict(int)
        self.ep_writer_failed = False
        self.ep_ws_age = self.ep_rest_age = 0.0
        self.ep_ws_silence = self.ep_rest_silence = 0.0
        self.reconnects = 0
        self.ep_start = cur

    def add(self, rows):
        for r in rows:
            recv_ts = float(r.get("recv_ts") or time.time())
            # Assign a boundary-crossing event to the new episode. The housekeeper runs every
            # five seconds, so without this roll the first few seconds after each boundary were
            # counted in the previous episode. Flush pending old-window rows before sealing it.
            if recv_ts >= self.ep_start + EPISODE_S:
                self.flush()
                self.close_episode(now=recv_ts)
            # Stamped HERE, centrally, rather than in each parser: a parser added for a new venue
            # next year cannot forget to label its clock, because it never gets the chance to.
            r["timestamp_basis"] = basis_for(r.get("source_mode"), r.get("exch_ts"),
                                             r.get("recv_ts"))
            r["process_start_id"] = self.process_start_id
            r.setdefault("connection_id", float(self.connection_id[r["venue"]]))
            r["queue_delay_ms"] = self.queue_delay_ms
            if r.get("_t_parsed"):     # local handling cost, measured not assumed
                r["processing_delay_ms"] = (r["_t_parsed"] - r["recv_ts"]) * 1000.0
            self.buf.append([r.get(c) for c in COLS])
            key = f"{r['venue']}/{r['stream']}"
            prev_recv = self._last_recv.get(key)
            if prev_recv is None:
                silence_ms = max(0.0, (recv_ts - self.ep_start) * 1000.0)
            else:
                silence_ms = max(
                    0.0, (recv_ts - max(float(prev_recv), float(self.ep_start))) * 1000.0
                )
            if key in REST_EXPECTED:
                self.ep_rest_silence = max(self.ep_rest_silence, silence_ms)
            else:
                self.ep_ws_silence = max(self.ep_ws_silence, silence_ms)
            self._last_recv[key] = max(float(prev_recv or recv_ts), recv_ts)
            self.counts[key] += 1
            self.ep_counts[key] += 1          # RECEIVED
            self._pending_counts[key] += 1    # promoted to ep_persisted only on a good insert
            # Feature age = recv_ts - exch_ts: how stale the datum already was on arrival. Tracked
            # SEPARATELY per class, because a 54s REST lag must never mask a healthy 20ms WS feed
            # (or vice versa) - the admissibility contract gates them on different limits.
            if r.get("exch_ts"):
                age = (r["recv_ts"] - r["exch_ts"]) * 1000.0
                if r.get("source_mode") == "REST_POLL":
                    self.ep_rest_age = max(self.ep_rest_age, age)
                else:
                    self.ep_ws_age = max(self.ep_ws_age, age)
            # Clock drift is only meaningful for PUSH streams. A REST poll returns a batch of
            # trades that may already be a minute old, so recv-exch measures POLL LAG, not clock
            # offset - mixing them made binance_perp read +54,000ms and would have silently
            # corrupted every lead-lag conclusion drawn from this table.
            if r.get("exch_ts") and not r["stream"].endswith("_rest") \
                    and r["stream"] not in ("premiumIndex", "openInterest"):
                self.drift[r["venue"]].append((r["recv_ts"] - r["exch_ts"]) * 1000.0)
        if len(self.buf) >= BATCH or time.time() - self.last >= FLUSH_S:
            self.flush()

    def flush(self):
        """Persist the buffer. THE EVIDENCE CLOCK STARTS ONLY AFTER A SUCCESSFUL INSERT.

        Previously `mark_start()` ran BEFORE the insert and the buffer was cleared even when the
        insert raised. That permitted the worst possible state for an evidence run:
        `collection_start_ts` exists, so the clock appears to be running, while zero rows were
        ever persisted - and the dropped rows were gone with only a console line to show for it.

        Now: insert first; on failure keep the buffer, count the error, mark the episode
        writer-failed, and re-raise. A persistent writer fault must surface as a visible outage
        (systemd restarts the unit) rather than as silently thinned evidence.
        """
        if not self.buf:
            return
        rows = list(self.buf)
        try:
            self.con.executemany(
                "INSERT INTO venue_events (" + ",".join(COLS) + ") VALUES (" +
                ",".join("?" * len(COLS)) + ")", rows)
        except Exception as e:
            self.writer_errors += 1
            self.ep_writer_failed = True
            print(f"[venues] INSERT FAILED ({len(rows)} rows retained, "
                  f"error #{self.writer_errors}): {e}")
            raise
        # success only past this point
        for k, n in self._pending_counts.items():
            self.ep_persisted[k] += n
        self._pending_counts.clear()
        self.buf.clear()
        self.last = time.time()
        self.mark_start()

    def write_clock(self):
        """Persist measured clock drift per venue per minute; assumed sync is a silent killer."""
        import statistics
        minute = int(time.time() // 60) * 60
        for v, d in self.drift.items():
            if len(d) < 10:
                continue
            s = sorted(d)
            try:
                self.con.execute(
                    "INSERT OR REPLACE INTO venue_clock VALUES (?,?,?,?,?)",
                    (minute, v, len(s), statistics.median(s), s[int(len(s) * 0.95)]))
            except Exception:
                pass


# ---------------------------------------------------------------- streams
async def _binance(name, url, w, stop):
    import websockets
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, max_queue=4096) as ws:
                print(f"[venues] {name} connected")
                async for raw in ws:
                    if stop.is_set():
                        break
                    _t0 = time.time()
                    _rows = parse_binance(json.loads(raw), _t0, name)
                    for _r in _rows:
                        _r["_t_parsed"] = time.time()
                    w.add(_rows)
        except Exception as e:
            if not stop.is_set():
                w.reconnects += 1
                w.connection_id[name] += 1
                print(f"[venues] {name} reconnect: {type(e).__name__} "
                      f"(connection #{w.connection_id[name]})")
                await asyncio.sleep(3)


async def _bybit(w, stop):
    import websockets
    while not stop.is_set():
        try:
            async with websockets.connect(STREAMS["bybit_perp"], ping_interval=20) as ws:
                await ws.send(json.dumps({"op": "subscribe",
                                          "args": ["orderbook.1.BTCUSDT", "publicTrade.BTCUSDT"]}))
                print("[venues] bybit_perp connected")
                async for raw in ws:
                    if stop.is_set():
                        break
                    _t0 = time.time()
                    _rows = parse_bybit(json.loads(raw), _t0)
                    for _r in _rows:
                        _r["_t_parsed"] = time.time()
                    w.add(_rows)
        except Exception as e:
            if not stop.is_set():
                w.reconnects += 1
                w.connection_id["bybit_perp"] += 1
                print(f"[venues] bybit reconnect: {type(e).__name__} "
                      f"(connection #{w.connection_id['bybit_perp']})")
                await asyncio.sleep(3)


async def _coinbase(w, stop):
    import websockets
    while not stop.is_set():
        try:
            async with websockets.connect(STREAMS["coinbase"], ping_interval=20) as ws:
                await ws.send(json.dumps({"type": "subscribe",
                                          "product_ids": ["BTC-USD"],
                                          "channels": ["ticker"]}))
                print("[venues] coinbase connected")
                async for raw in ws:
                    if stop.is_set():
                        break
                    _t0 = time.time()
                    _rows = parse_coinbase(json.loads(raw), _t0)
                    for _r in _rows:
                        _r["_t_parsed"] = time.time()
                    w.add(_rows)
        except Exception as e:
            if not stop.is_set():
                w.reconnects += 1
                w.connection_id["coinbase"] += 1
                print(f"[venues] coinbase reconnect: {type(e).__name__} "
                      f"(connection #{w.connection_id['coinbase']})")
                await asyncio.sleep(3)


def _payload_digest(d, fields):
    """Canonical hash of the SEMANTIC payload - order-independent, precision-sensitive.

    Values are normalised through repr(float(...)) where numeric so that 12.30 and 12.3 hash
    alike (they are the same observation), while a genuine value change does not."""
    parts = []
    for f in sorted(fields):
        v = d.get(f)
        if v is None:
            parts.append(f"{f}=")
            continue
        try:
            parts.append(f"{f}={float(v)!r}")
        except (TypeError, ValueError):
            parts.append(f"{f}={v}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _stamp_key(d, fields=()):
    """Stable identity for the slow REST observables.

    premiumIndex and openInterest carry no id of their own, so identity is instrument +
    VENUE PUBLICATION TIME + CANONICAL PAYLOAD HASH (venue+stream already pin the instrument).
    Both are polled every 5s against a venue that republishes less often, so without an identity
    the same observation was stored - and would have been counted - repeatedly. A real duplicate
    OI observation was found inside 45s, so this is not a theoretical concern.

    The payload hash (added 2026-07-26) covers the case publication time alone cannot:

        same time + same hash  -> EXACT DUPLICATE. Same event_key, so first_seen dedupe keeps the
                                  EARLIEST recv_ts. A repeat can never claim a later arrival.
        same time + diff hash  -> REVISION/CONFLICT. Different event_key, so BOTH rows survive for
                                  audit. Each is usable only from its own recv_ts, so a correction
                                  is never back-dated onto the moment the original was published.
                                  This is the frozen revision policy: causal, not last-write-wins.

    A missing venue timestamp yields NO key, which bars the row from features by design rather
    than inventing an identity that could not survive a reconnect."""
    t = d.get("time")
    if not t:
        return None
    if not fields:
        return f"t:{int(t)}"
    return f"t:{int(t)}#h:{_payload_digest(d, fields)}"


# The semantic fields per REST stream. Ancillary/bookkeeping fields are deliberately excluded:
# hashing them would turn a cosmetic venue change into a fake new observation.
_PREMIUM_FIELDS = ("markPrice", "indexPrice", "lastFundingRate", "nextFundingTime")
_OI_FIELDS = ("openInterest",)


async def _binance_perp_rest(w, stop):
    """Perp trade flow / mark / funding / OI via REST, because the WS streams do not deliver.

    MEASURED 2026-07-26 from this host: `fstream` serves btcusdt@bookTicker fine but yields
    ZERO messages for btcusdt@aggTrade and btcusdt@markPrice@1s - in single, combined and
    /ws/ forms alike. The futures REST API, however, returns all of it (premiumIndex,
    aggTrades, openInterest, fundingRate: HTTP 200 with live data). The older note that
    "Binance futures is geo-blocked here" is therefore too broad: REST works, those WS
    streams do not. Polling REST is the correct workaround, not a degraded fallback -
    exchange timestamps are preserved, only the delivery path changes.

    aggTrades are deduped on the venue's own aggregate-trade id, so a slow poll loses
    granularity but never invents or double-counts a trade.
    """
    import requests
    F = "https://fapi.binance.com/fapi/v1"
    sess = requests.Session()
    last_id = 0
    last_slow = 0.0
    poll_id = 0
    # D7 (2026-07-26): `connection_id` used to be assigned `poll_id`, so every single poll looked
    # like a fresh network connection. That is not a cosmetic mislabel - connection generation is
    # provenance: it is how an analyst distinguishes "the session was recreated after a failure"
    # (which resets poll_id and produces a poll-1 BACKLOG) from "we polled again on a healthy
    # session". Conflating them made every row look like backlog-adjacent and made reconnect
    # accounting meaningless. The generation now advances ONLY when the HTTP session is actually
    # rebuilt after a failure, which is also the only moment poll_id legitimately restarts.
    consecutive_failures = 0

    async def get(path, **kwargs):
        # `requests` is synchronous. Calling it directly inside this coroutine blocked every
        # WebSocket parser and the event-loop lag monitor for up to six seconds per request,
        # manufacturing receive-time lead/lag and stale-feed episodes. Keep the existing HTTP
        # client but move each blocking request off the event loop.
        return await asyncio.to_thread(sess.get, path, **kwargs)

    while not stop.is_set():
        now = time.time()
        try:
            poll_id += 1
            r = await get(f"{F}/aggTrades", params={"symbol": "BTCUSDT", "limit": 1000}, timeout=6)
            if r.status_code == 200:
                rows = []
                for t in r.json():
                    tid = float(t.get("a") or 0)
                    if tid <= last_id:
                        continue
                    last_id = max(last_id, tid)
                    rows.append({"recv_ts": time.time(), "exch_ts": float(t["T"]) / 1000.0,
                                 "venue": "binance_perp", "stream": "aggTrade_rest",
                                 "source_mode": "REST_POLL", "poll_id": float(poll_id),
                                 "connection_id": float(w.connection_id["binance_perp_rest"]),
                                 "symbol": "BTCUSDT", "event": "trade", "seq": tid,
                                 "event_key": f"a:{int(tid)}",
                                 "price": float(t["p"]), "size": float(t["q"]),
                                 "side": "sell" if t.get("m") else "buy"})
                if rows:
                    w.add(rows)
            consecutive_failures = 0
        except Exception as exc:
            # A failed poll is not automatically a new connection. Only rebuild the session (and
            # therefore advance the generation) once the failure looks persistent, so transient
            # timeouts do not manufacture fake reconnect churn in the provenance record.
            consecutive_failures += 1
            if consecutive_failures >= 3:
                try:
                    sess.close()
                except Exception:
                    pass
                sess = requests.Session()
                w.connection_id["binance_perp_rest"] += 1
                poll_id = 0          # a rebuilt session re-polls history: poll 1 is BACKLOG again
                consecutive_failures = 0
                print(f"[venues] binance_perp REST session rebuilt after repeated failure "
                      f"({type(exc).__name__}); connection generation "
                      f"#{w.connection_id['binance_perp_rest']}, poll_id reset")
        if now - last_slow >= 5.0:          # mark/index/funding + OI change slowly
            last_slow = now
            try:
                p = await get(f"{F}/premiumIndex", params={"symbol": "BTCUSDT"}, timeout=6)
                if p.status_code == 200:
                    d = p.json()
                    w.add([{"recv_ts": time.time(), "exch_ts": float(d.get("time", 0)) / 1000.0,
                            "venue": "binance_perp", "stream": "premiumIndex", "symbol": "BTCUSDT",
                            "source_mode": "REST_POLL", "poll_id": float(poll_id),
                            "connection_id": float(w.connection_id["binance_perp_rest"]),
                            "event": "mark", "event_key": _stamp_key(d, _PREMIUM_FIELDS),
                            "price": float(d.get("markPrice") or 0.0),
                            "extra": json.dumps({"index": d.get("indexPrice"),
                                                 "funding": d.get("lastFundingRate"),
                                                 "next_funding_ts": d.get("nextFundingTime")},
                                                separators=(",", ":"))}])
                o = await get(f"{F}/openInterest", params={"symbol": "BTCUSDT"}, timeout=6)
                if o.status_code == 200:
                    d = o.json()
                    w.add([{"recv_ts": time.time(), "exch_ts": float(d.get("time", 0)) / 1000.0,
                            "venue": "binance_perp", "stream": "openInterest", "symbol": "BTCUSDT",
                            "source_mode": "REST_POLL", "poll_id": float(poll_id),
                            "connection_id": float(w.connection_id["binance_perp_rest"]),
                            "event": "oi", "event_key": _stamp_key(d, _OI_FIELDS),
                            "size": float(d.get("openInterest") or 0.0)}])
            except Exception:
                pass
        await asyncio.sleep(1.0)


async def _loop_lag(w, stop):
    """Measure how late the event loop actually wakes us. Receive-time ordering inherits this."""
    while not stop.is_set():
        t = time.time()
        await asyncio.sleep(0.5)
        w.queue_delay_ms = max(0.0, (time.time() - t - 0.5) * 1000.0)


async def _housekeeper(w, stop, smoke_s=None):
    t0 = time.time()
    while not stop.is_set():
        await asyncio.sleep(5)
        w.flush()
        w.write_clock()
        w.close_episode()
        if smoke_s and time.time() - t0 >= smoke_s:
            stop.set()


async def _run(con, smoke_s=None):
    w = Writer(con, persistent=smoke_s is None)
    w.register_session()
    stop = asyncio.Event()
    tasks = [asyncio.create_task(_binance("binance_spot", STREAMS["binance_spot"], w, stop)),
             asyncio.create_task(_binance("binance_perp", STREAMS["binance_perp"], w, stop)),
             asyncio.create_task(_binance_perp_rest(w, stop)),   # WS won't serve perp trades/mark
             asyncio.create_task(_bybit(w, stop)),
             asyncio.create_task(_coinbase(w, stop)),
             asyncio.create_task(_loop_lag(w, stop)),
             asyncio.create_task(_housekeeper(w, stop, smoke_s))]
    try:
        # Do not use return_exceptions=True here. A DuckDB writer failure must terminate the
        # service so systemd can restart it; swallowing the exception left the process alive with
        # one dead task and made the evidence outage look like an ordinary quiet period.
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        w.flush()
        w.write_clock()
        w.close_episode()
    return w


def report(path=None):
    con = init_db(path)
    n = con.execute("SELECT COUNT(*) FROM venue_events").fetchone()[0]
    print(f"venue_events rows: {n:,}")
    if n:
        for r in con.execute("""SELECT venue, stream, COUNT(*) n,
                                       MIN(recv_ts) a, MAX(recv_ts) b
                                FROM venue_events GROUP BY 1,2 ORDER BY 1,2""").fetchall():
            hrs = (r[4] - r[3]) / 3600 if r[4] and r[3] else 0
            print(f"  {r[0]:<14} {r[1]:<12} {r[2]:>10,}  span {hrs:.2f}h")
        print("\nclock drift (exch_ts - recv_ts, ms):")
        for r in con.execute("""SELECT venue, COUNT(*), ROUND(AVG(drift_med_ms),1),
                                       ROUND(MAX(drift_p95_ms),1)
                                FROM venue_clock GROUP BY 1 ORDER BY 1""").fetchall():
            print(f"  {r[0]:<14} minutes={r[1]:<6} median={r[2]}  worst p95={r[3]}")
    episode_report(con)
    con.close()


# The frozen forward-data gate. D8 (2026-07-26): the previous check was
#     qualifying >= 1000  AND  wall_clock_span >= 4 weeks
# which a collector with large outages satisfies trivially - run for six weeks, be healthy for
# ten days, and both conditions pass while the preregistration's word "CONTINUOUS" is unmet.
# "Continuous" is the frozen requirement and is NOT reinterpretable: it means an unbroken run of
# consecutive qualifying episodes, not a total count spread across a long calendar window.
GATE_MIN_QUALIFYING = 1000
GATE_MIN_CONTINUOUS_WEEKS = 4
# One missing episode breaks a run. A gap larger than this many episode-slots is an OUTAGE; the
# run restarts. (A single 5-minute slot lost to a restart is still a break - it just produces a
# short gap rather than a long one. The gate cares about the longest unbroken run either way.)
GATE_EPISODES_PER_WEEK = int(7 * 24 * 3600 / EPISODE_S)     # 2016


def continuity_report(con):
    """Longest UNBROKEN run of qualifying episodes, plus coverage and outage geometry.

    Returns the numbers the frozen gate actually requires. Consecutiveness is measured on the
    episode grid (every EPISODE_S seconds), so a missing or excluded episode breaks the run -
    which is the whole point: an outage in the middle of week three does not get to be averaged
    away by healthy weeks either side of it.
    """
    rows = con.execute("""SELECT episode_start, qualifying FROM venue_episodes
                          ORDER BY episode_start""").fetchall()
    out = {"longest_run_episodes": 0, "longest_run_weeks": 0.0, "coverage_pct": 0.0,
           "largest_gap_h": 0.0, "gaps": 0, "total_qualifying": 0,
           "gate": "NOT MET", "gate_reason": "no episodes recorded"}
    if not rows:
        return out
    q_total = sum(1 for _, q in rows if q)
    out["total_qualifying"] = q_total
    span_eps = int((rows[-1][0] - rows[0][0]) / EPISODE_S) + 1
    out["coverage_pct"] = 100.0 * q_total / max(1, span_eps)

    best = run = 0
    prev_slot = None
    gaps = []
    for start, qual in rows:
        slot = int(start // EPISODE_S)
        contiguous = prev_slot is not None and slot == prev_slot + 1
        if not contiguous and prev_slot is not None:
            gaps.append((slot - prev_slot - 1) * EPISODE_S)
        run = run + 1 if (qual and contiguous) else (1 if qual else 0)
        best = max(best, run)
        prev_slot = slot
    out["longest_run_episodes"] = best
    out["longest_run_weeks"] = best / GATE_EPISODES_PER_WEEK
    out["gaps"] = len(gaps)
    out["largest_gap_h"] = (max(gaps) / 3600.0) if gaps else 0.0

    need_run = GATE_MIN_CONTINUOUS_WEEKS * GATE_EPISODES_PER_WEEK
    reasons = []
    if q_total < GATE_MIN_QUALIFYING:
        reasons.append(f"qualifying {q_total:,} < {GATE_MIN_QUALIFYING:,}")
    if best < need_run:
        reasons.append(f"longest continuous run {best:,} episodes "
                       f"({out['longest_run_weeks']:.2f}w) < {need_run:,} ({GATE_MIN_CONTINUOUS_WEEKS}w)")
    out["gate"] = "MET" if not reasons else "NOT MET"
    out["gate_reason"] = "; ".join(reasons)
    return out


def episode_report(con):
    """Uptime and qualifying coverage are DIFFERENT numbers. The promotion contract counts the
    second one; reporting only the first is how a lane claims four weeks of data it does not have."""
    row = con.execute("""SELECT COUNT(*), SUM(CASE WHEN qualifying THEN 1 ELSE 0 END),
                                MIN(episode_start), MAX(episode_end)
                         FROM venue_episodes""").fetchone()
    n, q = int(row[0] or 0), int(row[1] or 0)
    print("\nepisode coverage (5-minute research episodes):")
    if not n:
        print("  none recorded yet")
        return
    start = con.execute("SELECT v FROM venue_collection_meta WHERE k='collection_start_ts'"
                        ).fetchone()
    wall_h = (row[3] - row[2]) / 3600.0
    print(f"  collection start   {start[0] if start else '(unmarked)'}")
    print(f"  wall-clock span    {wall_h:.2f}h  ({wall_h/168:.2f} weeks)")
    print(f"  episodes recorded  {n:,}")
    print(f"  QUALIFYING         {q:,}  ({100.0*q/n:.1f}% of recorded)")
    cont = continuity_report(con)
    print(f"  longest CONTINUOUS qualifying run  {cont['longest_run_episodes']:,} episodes"
          f"  ({cont['longest_run_weeks']:.2f} weeks)")
    print(f"  qualifying coverage of span        {cont['coverage_pct']:.1f}%")
    print(f"  largest outage gap                 {cont['largest_gap_h']:.2f}h"
          f"  ({cont['gaps']} gaps total)")
    print(f"  prereg requires    >= {GATE_MIN_QUALIFYING:,} qualifying AND "
          f">= {GATE_MIN_CONTINUOUS_WEEKS} CONTINUOUS weeks -> {cont['gate']}")
    if cont["gate"] != "MET":
        print(f"    why: {cont['gate_reason']}")
    ex = con.execute("""SELECT exclusion_reason, COUNT(*) FROM venue_episodes
                        WHERE NOT qualifying GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").fetchall()
    for r in ex:
        print(f"    excluded {r[1]:>6,}  {r[0][:70]}")


def selftest():
    """No network. Parsers + schema, so a broken payload shape fails here rather than silently."""
    ok = True
    b = parse_binance({"data": {"e": "bookTicker", "s": "BTCUSDT", "E": 1700000000000,
                                "u": 42, "b": "60000.1", "B": "1.5", "a": "60000.6", "A": "2.0"}},
                      1700000000.5, "binance_spot")
    c1 = len(b) == 1 and b[0]["bid"] == 60000.1 and b[0]["event"] == "quote"
    print(f"  {'OK  ' if c1 else 'FAIL'} binance bookTicker -> quote"); ok &= c1
    t = parse_binance({"data": {"e": "aggTrade", "s": "BTCUSDT", "T": 1700000000000, "a": 7,
                                "p": "60001", "q": "0.3", "m": True}}, 1.0, "binance_perp")
    c2 = t and t[0]["side"] == "sell"      # buyer is maker => aggressor sold
    print(f"  {'OK  ' if c2 else 'FAIL'} binance aggTrade maker-flag -> aggressor side"); ok &= c2
    m = parse_binance({"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "E": 1700000000000,
                                "p": "60002", "i": "60001", "r": "0.0001"}}, 1.0, "binance_perp")
    c3 = m and json.loads(m[0]["extra"])["funding"] == "0.0001"
    print(f"  {'OK  ' if c3 else 'FAIL'} binance markPrice -> funding captured"); ok &= c3
    y = parse_bybit({"topic": "orderbook.1.BTCUSDT", "ts": 1700000000000,
                     "data": {"s": "BTCUSDT", "u": 9, "b": [["60000", "3"]],
                              "a": [["60001", "4"]]}}, 1.0)
    c4 = y and y[0]["ask"] == 60001.0
    print(f"  {'OK  ' if c4 else 'FAIL'} bybit orderbook.1 -> quote"); ok &= c4
    yt = parse_bybit({"topic": "publicTrade.BTCUSDT", "ts": 1700000000000,
                      "data": [{"T": 1700000000000, "s": "BTCUSDT", "p": "60000",
                                "v": "0.5", "S": "Buy", "i": "2290000000123"}]}, 1.0)
    c5 = yt and yt[0]["side"] == "buy"
    print(f"  {'OK  ' if c5 else 'FAIL'} bybit publicTrade -> aggressor side"); ok &= c5
    cb = parse_coinbase({"type": "ticker", "product_id": "BTC-USD", "price": "60000",
                         "best_bid": "59999", "best_ask": "60001", "sequence": 5,
                         "time": "2026-07-26T00:00:00.000000Z"}, 1.0)
    c6 = cb and cb[0]["exch_ts"] > 0 and cb[0]["price"] == 60000.0
    print(f"  {'OK  ' if c6 else 'FAIL'} coinbase ticker -> quote + parsed ISO time"); ok &= c6
    c7 = not parse_coinbase({"type": "subscriptions"}, 1.0) and not parse_bybit({"topic": "x"}, 1.0)
    print(f"  {'OK  ' if c7 else 'FAIL'} non-data control frames ignored"); ok &= c7
    con = init_db(":memory:")
    rows = b + t + m + y + yt + cb
    con.executemany("INSERT INTO venue_events (" + ",".join(COLS) + ") VALUES (" +
                    ",".join("?" * len(COLS)) + ")", [[r.get(c) for c in COLS] for r in rows])
    c8 = con.execute("SELECT COUNT(*) FROM venue_events").fetchone()[0] == len(rows)
    print(f"  {'OK  ' if c8 else 'FAIL'} all parsed row shapes insert cleanly"); ok &= c8

    # --- admissibility contract: every WS parser must self-label as Class A -----------------
    c9 = all(r.get("source_mode") == "WS" for r in rows) and all(r.get("poll_id") is None
                                                                for r in rows)
    print(f"  {'OK  ' if c9 else 'FAIL'} every WS row carries source_mode=WS and no poll_id")
    ok &= bool(c9)
    # A Class B row must be distinguishable in SQL alone, without knowing the stream names.
    con.execute("INSERT INTO venue_events (recv_ts,exch_ts,venue,stream,source_mode,poll_id) "
                "VALUES (1000.0, 940.0, 'binance_perp', 'aggTrade_rest', 'REST_POLL', 3)")
    c10 = con.execute("SELECT COUNT(*) FROM venue_events WHERE source_mode='REST_POLL' "
                      "AND poll_id IS NOT NULL").fetchone()[0] == 1
    print(f"  {'OK  ' if c10 else 'FAIL'} Class B rows separable by source_mode + poll_id")
    ok &= bool(c10)

    # --- episode accounting ----------------------------------------------------------------
    w = Writer(con, persistent=True)
    t0 = 1_700_000_100.0                       # exact EPISODE_S boundary (t0 % 300 == 0)
    w.boot_ts = w.ep_start = t0
    for s in EXPECTED:                          # a fully healthy window
        v, st = s.split("/", 1)
        w.ep_counts[f"{v}/{st}"] = 10           # parsed
        w.ep_persisted[f"{v}/{st}"] = 10        # AND confirmed written (D4: only this qualifies)
    w.ep_ws_age, w.ep_rest_age = 25.0, 6_000.0  # both inside the frozen limits (D2)
    w.close_episode(now=t0 + EPISODE_S + 1)
    r = con.execute("SELECT qualifying, streams_live, exclusion_reason FROM venue_episodes "
                    "WHERE episode_start=?", (int(t0),)).fetchone()
    c11 = r and r[0] is True and r[1] == len(EXPECTED) and not r[2]
    print(f"  {'OK  ' if c11 else 'FAIL'} healthy full window qualifies"); ok &= bool(c11)

    w.ep_counts["binance_spot/bookTicker"] = 5  # one stream only -> must be excluded
    w.close_episode(now=t0 + 2 * EPISODE_S + 1)
    r = con.execute("SELECT qualifying, exclusion_reason FROM venue_episodes "
                    "WHERE episode_start=?", (int(t0 + EPISODE_S),)).fetchone()
    c12 = r and r[0] is False and "missing:" in (r[1] or "")
    print(f"  {'OK  ' if c12 else 'FAIL'} incomplete stream health excludes the episode"); ok &= bool(c12)

    # A stall must MATERIALISE as excluded rows, not vanish. Skipping 3 windows at once:
    before = con.execute("SELECT COUNT(*) FROM venue_episodes").fetchone()[0]
    w.close_episode(now=t0 + 6 * EPISODE_S + 1)
    after = con.execute("SELECT COUNT(*) FROM venue_episodes").fetchone()[0]
    gapq = con.execute("SELECT COUNT(*) FROM venue_episodes WHERE episode_start IN (?,?,?) "
                       "AND NOT qualifying",
                       (int(t0 + 3 * EPISODE_S), int(t0 + 4 * EPISODE_S),
                        int(t0 + 5 * EPISODE_S))).fetchone()[0]
    c13 = (after - before) == 4 and gapq == 3
    print(f"  {'OK  ' if c13 else 'FAIL'} collector stall materialises as excluded episodes")
    ok &= bool(c13)

    # Boot mid-window can never qualify, however healthy the streams look.
    w2 = Writer(con, persistent=True)
    w2.ep_start, w2.boot_ts = t0 + 100 * EPISODE_S, t0 + 100 * EPISODE_S + 130
    for s in EXPECTED:
        w2.ep_counts[s] = 10
    w2.close_episode(now=t0 + 101 * EPISODE_S + 1)
    r = con.execute("SELECT qualifying, exclusion_reason FROM venue_episodes "
                    "WHERE episode_start=?", (int(t0 + 100 * EPISODE_S),)).fetchone()
    c14 = r and r[0] is False and "partial_window" in (r[1] or "")
    print(f"  {'OK  ' if c14 else 'FAIL'} partial boot window cannot qualify"); ok &= bool(c14)

    # Feature age must not be pooled across classes: a 60s REST lag must not be reported as WS age.
    w3 = Writer(con, persistent=False)
    w3.add([{"recv_ts": 1000.0, "exch_ts": 999.98, "venue": "binance_spot",
             "stream": "bookTicker", "source_mode": "WS"},
            {"recv_ts": 1000.0, "exch_ts": 940.0, "venue": "binance_perp",
             "stream": "aggTrade_rest", "source_mode": "REST_POLL", "poll_id": 1.0}])
    c15 = abs(w3.ep_ws_age - 20.0) < 1e-6 and abs(w3.ep_rest_age - 60000.0) < 1e-6
    print(f"  {'OK  ' if c15 else 'FAIL'} WS and REST feature age tracked separately"); ok &= bool(c15)

    # A venue that sends no event time must yield NULL, never 0 - and must not pollute age/drift.
    sb = parse_binance({"data": {"u": 1, "s": "BTCUSDT", "b": "60000", "B": "1",
                                 "a": "60001", "A": "1"}}, 1_785_000_000.0, "binance_spot")
    c17 = sb and sb[0]["exch_ts"] is None
    print(f"  {'OK  ' if c17 else 'FAIL'} spot bookTicker (no E/T) -> exch_ts NULL, not 0")
    ok &= bool(c17)
    w4 = Writer(con, persistent=False)
    w4.add(sb)
    c18 = w4.ep_ws_age == 0.0 and not w4.drift.get("binance_spot")
    print(f"  {'OK  ' if c18 else 'FAIL'} timestamp-less rows excluded from age and drift")
    ok &= bool(c18)

    # Central stamping: a row cannot reach the buffer without a timestamp_basis, whatever the
    # parser did or forgot to do.
    w5 = Writer(con, persistent=False)
    w5.add([{"recv_ts": 1000.0, "exch_ts": 999.98, "venue": "v", "stream": "s",
             "source_mode": "WS"},                                   # healthy push feed
            {"recv_ts": 1000.0, "exch_ts": None, "venue": "v", "stream": "bookTicker",
             "source_mode": "WS"},                                   # spot quote, no event time
            {"recv_ts": 1000.0, "exch_ts": 940.0, "venue": "v", "stream": "r",
             "source_mode": "REST_POLL", "poll_id": 1.0}])           # polled
    bi = COLS.index("timestamp_basis")
    c19 = [row[bi] for row in w5.buf] == ["EXCHANGE_TIME", "RECEIVE_ONLY", "POLL_RECEIVE_TIME"]
    print(f"  {'OK  ' if c19 else 'FAIL'} every row stamped with a timestamp_basis at write time")
    ok &= bool(c19)
    # An implausible venue clock must be downgraded, never trusted as a common reference.
    w5.buf.clear()
    w5.add([{"recv_ts": 1000.0, "exch_ts": 500.0, "venue": "v", "stream": "s",
             "source_mode": "WS"}])
    c20 = w5.buf[0][bi] == "RECEIVE_TIME"
    print(f"  {'OK  ' if c20 else 'FAIL'} implausible venue clock downgraded, never trusted")
    ok &= bool(c20)

    # ---- stable natural event identity -----------------------------------------------------
    # `seq` is for GAP DETECTION; `event_key` is what dedupe partitions on, and it must come from
    # the VENUE. A poll-local counter resets on reconnect and could never recognise a re-fetched
    # observation as a repeat - which is the whole failure this guards.
    parsed = b + t + y + yt + cb
    c21 = all(r.get("event_key") for r in parsed)
    print(f"  {'OK  ' if c21 else 'FAIL'} every WS parser emits a venue-supplied event_key")
    ok &= bool(c21)
    keys = {r["stream"]: r["event_key"] for r in parsed}
    c22 = (keys.get("bookTicker") == "u:42" and keys.get("aggTrade") == "a:7"
           and keys.get("orderbook.1") == "u:9" and keys.get("ticker") == "s:5"
           and keys.get("publicTrade") == "i:2290000000123"
           and m[0].get("event_key") == "t:1700000000000")
    print(f"  {'OK  ' if c22 else 'FAIL'} event_key derives from the venue's own id, not a counter")
    ok &= bool(c22)
    # The slow REST observables were the real gap: polled every 5s against a venue that
    # republishes less often, so the same observation was stored (and would have been counted)
    # repeatedly. Identity = instrument + publication time; no venue time means no key at all.
    c23 = (_stamp_key({"time": 1700000000123, "symbol": "BTCUSDT"}) == "t:1700000000123"
           and _stamp_key({"symbol": "BTCUSDT"}) is None
           and _stamp_key({"time": 0}) is None)
    print(f"  {'OK  ' if c23 else 'FAIL'} slow REST observables keyed on publication time; "
          "absent time yields NO key")
    ok &= bool(c23)
    c24 = _stamp_key({"time": 1700000000123}) == _stamp_key({"time": 1700000000123})
    print(f"  {'OK  ' if c24 else 'FAIL'} identity survives a restart (same publication -> same key)")
    ok &= bool(c20)

    # A smoke run must never stamp the evidence clock.
    w3.mark_start()
    c16 = con.execute("SELECT COUNT(*) FROM venue_collection_meta "
                      "WHERE k='collection_start_ts'").fetchone()[0] == 0
    print(f"  {'OK  ' if c16 else 'FAIL'} non-persistent run does not start the evidence clock")
    ok &= bool(c16)

    con.close()
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="20s in-memory run, then stats")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seconds", type=float, default=20.0)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.report:
        return report() or 0
    con = init_db(":memory:" if a.smoke else None)
    print(f"[venues] db={'(memory)' if a.smoke else DB_PATH}")
    w = asyncio.run(_run(con, smoke_s=a.seconds if a.smoke else None))
    if a.smoke:
        print("\nevents captured:")
        for k, v in sorted(w.counts.items()):
            print(f"  {k:<28} {v:>8,}")
        missing = [s for s in EXPECTED if not w.counts.get(s)]
        print(f"\nstream health: {len(EXPECTED)-len(missing)}/{len(EXPECTED)} expected streams live"
              + (f"  MISSING: {missing}" if missing else "  (all healthy)"))
        import statistics
        for v, d in sorted(w.drift.items()):
            if d:
                print(f"  drift {v:<16} median {statistics.median(d):+.0f}ms  n={len(d)}")
        print(f"\ntotal rows: {con.execute('SELECT COUNT(*) FROM venue_events').fetchone()[0]:,}")
        print("provenance (admissibility class actually recorded):")
        for r in con.execute("""SELECT venue, stream, timestamp_basis, COUNT(*),
                                       ROUND(MAX(CASE WHEN exch_ts>0 THEN (recv_ts-exch_ts)*1000 END),0),
                                       SUM(CASE WHEN exch_ts IS NULL THEN 1 ELSE 0 END)
                                FROM venue_events GROUP BY 1,2,3 ORDER BY 1,2""").fetchall():
            cls = "B" if r[2] == "POLL_RECEIVE_TIME" else "A"
            age = f"max age {r[4]}ms" if r[4] is not None else "no venue timestamp"
            print(f"  [{cls}] {r[0]:<14}{r[1]:<16}{str(r[2]):<18}{r[3]:>8,}  {age}"
                  + (f"  ({r[5]:,} rows recv_ts-only)" if r[5] else ""))
        unstamped = con.execute("SELECT COUNT(*) FROM venue_events WHERE source_mode IS NULL "
                                "OR timestamp_basis IS NULL").fetchone()[0]
        print("event identity (dedupe key coverage - a REST stream without one is barred):")
        for r in con.execute("""SELECT venue, stream, source_mode, COUNT(*) n,
                                       SUM(CASE WHEN event_key IS NULL THEN 1 ELSE 0 END) missing,
                                       COUNT(DISTINCT event_key) uniq
                                FROM venue_events GROUP BY 1,2,3 ORDER BY 1,2""").fetchall():
            flag = ("  <- BARRED FROM FEATURES" if r[4] and r[2] == "REST_POLL" else
                    "  (no dedupe)" if r[4] else "")
            dup = r[3] - r[5] - r[4]
            print(f"  {r[0]:<14}{r[1]:<16}{r[3]:>7,} rows  {r[5]:>7,} unique  "
                  f"{dup:>5,} repeat  {r[4]:>5,} keyless{flag}")
        print(f"  unstamped rows: {unstamped}"
              + ("  <- BAD: a row with no provenance is inadmissible" if unstamped else "  (ok)"))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
