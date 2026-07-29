"""Bounded, non-blocking persistence queue for WebSocket feed callbacks.

`handle_trade` and `handle_depth` are plain synchronous callbacks invoked from the feed path, and
each called `database.log_*_parquet()` directly under a comment reading "fire and forget". It was
not fire and forget - it was an ordinary blocking call. A slow disk, a parquet flush or a file
lock stalled the callback, and with it the feed.

The callback's job is to validate, timestamp and hand off. Persistence belongs to a worker.

Design choices worth stating:

  * BOUNDED. An unbounded queue converts a disk stall into unbounded memory growth and turns a
    slow writer into an OOM kill. When a lane is full the event is DROPPED and counted - losing
    a diagnostic row is strictly better than stalling the feed or dying.
  * TWO LANES, because the two streams have different value under overload.
        trades - bounded FIFO. Every trade is a distinct irreplaceable event; a dropped trade is
                 lost information, so trades are never coalesced, only counted when dropped.
        depth  - COALESCED, latest-wins per key. A depth snapshot is a picture of the book now,
                 so a newer snapshot for the same symbol makes the older one worthless. Under a
                 burst, superseding is not data loss in the way dropping a trade is.
    A single shared FIFO let a depth burst fill the queue and drop TRADES - the stream whose loss
    actually matters. Separate lanes with separate budgets remove that coupling. Every supersede
    and every drop is counted per lane.
  * DROPS ARE COUNTED PER LANE and surfaced, never silent.
  * The worker is a daemon thread: it must never keep a shutting-down process alive.
  * OWNERSHIP IS EXPLICIT. Nothing starts at import. The application starts and stops this in its
    lifespan, so tests, hot reloads, pre-fork servers and modules importing server.py for
    inspection do not silently spawn a writer thread.
  * Diagnostics only. This queue carries raw trade/depth logging. It is NOT the evidence path -
    complete-trade evidence goes through log_forward_prediction_v2, which is transactional and
    must not be made lossy.

SHUTDOWN IS DETERMINISTIC, AND REPORTS WHAT IT LEFT BEHIND
    stop() closes submissions, enqueues a sentinel, and lets the worker finish every job queued
    before it, including the one in flight. It returns a result rather than a silent None:

        clean=True   -> the sentinel was reached; nothing queued was abandoned
        clean=False  -> the timeout expired first; `abandoned` says exactly how many were lost

    The previous implementation set a stop Event that the worker loop tested each iteration, so
    jobs still queued when the timeout expired were discarded with no count and no signal.

    python backend/feed_writer.py --selftest
"""
from __future__ import annotations

import collections
import sys
import threading
import time
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_MAXSIZE = 10_000
DEFAULT_DEPTH_MAXSIZE = 2_000
TRADE_BUDGET = 8            # trades drained per depth item, so neither lane can starve the other

_SENTINEL = object()


class ShutdownResult(dict):
    """dict subclass so callers may log it directly, with `clean` easy to assert on."""

    @property
    def clean(self) -> bool:
        return bool(self["clean"])


class FeedWriter:
    """One background writer draining a bounded trade lane and a coalescing depth lane."""

    def __init__(
        self,
        maxsize: int = DEFAULT_MAXSIZE,
        name: str = "feed-writer",
        depth_maxsize: int = DEFAULT_DEPTH_MAXSIZE,
    ) -> None:
        self._name = name
        self._maxsize = int(maxsize)
        self._depth_maxsize = int(depth_maxsize)
        self._trades: collections.deque = collections.deque()
        self._depth: collections.OrderedDict = collections.OrderedDict()
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._sentinel_seen = False
        self._stopping = False
        self._in_flight = 0

        self.enqueued = 0
        self.written = 0
        self.dropped = 0                 # total, preserved for existing callers
        self.dropped_trades = 0
        self.dropped_depth = 0
        self.dropped_not_accepting = 0
        self.superseded_depth = 0
        self.abandoned_on_shutdown = 0
        self.failed = 0
        self.last_error: str | None = None
        self.last_write_ts: float | None = None
        self._recent_drops: collections.deque = collections.deque()   # timestamps, for drop rate

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> "FeedWriter":
        with self._cv:
            if self._thread and self._thread.is_alive():
                return self
            self._accepting = True
            self._sentinel_seen = False
            self._stopping = False
            self._thread = threading.Thread(target=self._drain, name=self._name, daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> ShutdownResult:
        """Close submissions, drain what is already queued, and report what was left."""
        with self._cv:
            self._accepting = False
            self._stopping = True
            self._trades.append(_SENTINEL)
            self._cv.notify_all()
            thread = self._thread
        if thread:
            thread.join(timeout=timeout)
        with self._cv:
            pending = len(self._trades) + len(self._depth)
            if self._trades and self._trades[-1] is _SENTINEL:
                pending -= 1
            clean = self._sentinel_seen and pending == 0 and self._in_flight == 0
            if not clean:
                self.abandoned_on_shutdown += max(0, pending)
            result = ShutdownResult({
                "clean": bool(clean),
                "abandoned": max(0, pending) if not clean else 0,
                "in_flight_at_timeout": self._in_flight,
                "written": self.written,
                "failed": self.failed,
                "dropped": self.dropped,
                "worker_alive": bool(thread and thread.is_alive()),
            })
            self._thread = None if not (thread and thread.is_alive()) else thread
        return result

    # -- submission --------------------------------------------------------------------------

    def _record_drop(self) -> None:
        now = time.time()
        self._recent_drops.append(now)
        cutoff = now - 60.0
        while self._recent_drops and self._recent_drops[0] < cutoff:
            self._recent_drops.popleft()

    def submit(self, handler: Callable[[Any], Any], payload: Any) -> bool:
        """Enqueue a TRADE-lane job without blocking. False means it was dropped."""
        with self._cv:
            if not self._accepting:
                # A writer that is not running must say so loudly. Silently queueing into a
                # thread that will never start is how a stalled feed looks healthy.
                self.dropped_not_accepting += 1
                self.dropped += 1
                self._record_drop()
                return False
            if len(self._trades) >= self._maxsize:
                self.dropped_trades += 1
                self.dropped += 1
                self._record_drop()
                return False
            self._trades.append((handler, payload, time.time()))
            self.enqueued += 1
            self._cv.notify()
        return True

    def submit_depth(self, handler: Callable[[Any], Any], payload: Any, key: Any) -> bool:
        """Enqueue a DEPTH-lane job, superseding any pending job for the same key.

        `key` identifies the book being described (symbol, or symbol+update class). A newer
        snapshot for the same key replaces the older pending one: writing a stale picture of the
        book has no value once a fresher one has arrived."""
        with self._cv:
            if not self._accepting:
                self.dropped_not_accepting += 1
                self.dropped += 1
                self._record_drop()
                return False
            if key in self._depth:
                # Preserve the ORIGINAL enqueue time: the point of the age metric is how long
                # this book has gone unwritten, which a refresh does not reset.
                _, _, first_ts = self._depth[key]
                self._depth[key] = (handler, payload, first_ts)
                self.superseded_depth += 1
                self._cv.notify()
                return True
            if len(self._depth) >= self._depth_maxsize:
                self.dropped_depth += 1
                self.dropped += 1
                self._record_drop()
                return False
            self._depth[key] = (handler, payload, time.time())
            self.enqueued += 1
            self._cv.notify()
        return True

    # -- worker ------------------------------------------------------------------------------

    def _next_job(self, budget: list[int]) -> Any:
        """Caller holds the lock. Returns a job tuple, _SENTINEL, or None if both lanes empty.

        THE SENTINEL ENDS BOTH LANES, SO IT MAY ONLY BE CONSUMED ONCE BOTH ARE EMPTY.
            stop() appends the sentinel to the TRADE lane. Consuming it as soon as the trade lane
            reached it abandoned every coalesced depth job still pending: measured, 20 queued
            depth writes produced 0 writes and 20 abandoned. The loss was reported rather than
            silent, but a shutdown that CAN drain must drain."""
        if self._trades and self._trades[0] is _SENTINEL and self._depth:
            _key, job = self._depth.popitem(last=False)
            return job
        take_depth = (budget[0] <= 0 and self._depth) or not self._trades
        if take_depth and self._depth:
            budget[0] = TRADE_BUDGET
            _key, job = self._depth.popitem(last=False)
            return job
        if self._trades:
            budget[0] -= 1
            return self._trades.popleft()
        return None

    def _drain(self) -> None:
        budget = [TRADE_BUDGET]
        while True:
            with self._cv:
                while True:
                    job = self._next_job(budget)
                    if job is not None:
                        break
                    if self._stopping:
                        return
                    self._cv.wait(timeout=0.25)
                if job is _SENTINEL:
                    # Everything enqueued before shutdown has now been processed.
                    self._sentinel_seen = True
                    self._cv.notify_all()
                    return
                self._in_flight += 1
            handler, payload, _enqueued_ts = job
            try:
                handler(payload)
                with self._cv:
                    self.written += 1
                    self.last_write_ts = time.time()
            except Exception as exc:                       # noqa: BLE001 - counted, not hidden
                with self._cv:
                    self.failed += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                with self._cv:
                    self._in_flight -= 1
                    self._cv.notify_all()

    # -- observability -----------------------------------------------------------------------

    def _oldest_enqueued_ts(self) -> float | None:
        """Caller holds the lock. The enqueue time of the OLDEST job still waiting.

        Read from the head of each lane rather than tracked in a variable. The tracked variable
        was reset to `now` after every completed write, so during a sustained backlog it reported
        one job's service time instead of the true wait - a 2.0s backlog read as 0.25s, which
        defeated the only metric that reveals a stalling writer."""
        candidates = []
        if self._trades and self._trades[0] is not _SENTINEL:
            candidates.append(self._trades[0][2])
        if self._depth:
            candidates.append(next(iter(self._depth.values()))[2])
        return min(candidates) if candidates else None

    def stats(self) -> dict[str, Any]:
        with self._cv:
            now = time.time()
            oldest = self._oldest_enqueued_ts()
            depth_total = len(self._trades) + len(self._depth)
            if self._trades and self._trades[-1] is _SENTINEL:
                depth_total -= 1
            capacity = max(1, self._maxsize + self._depth_maxsize)
            alive = bool(self._thread and self._thread.is_alive())
            cutoff = now - 60.0
            while self._recent_drops and self._recent_drops[0] < cutoff:
                self._recent_drops.popleft()
            return {
                "depth": depth_total,
                "trade_depth": len(self._trades) - (
                    1 if self._trades and self._trades[-1] is _SENTINEL else 0),
                "depth_depth": len(self._depth),
                "maxsize": self._maxsize,
                "depth_maxsize": self._depth_maxsize,
                "utilization": round(depth_total / capacity, 6),
                "enqueued": self.enqueued,
                "written": self.written,
                "in_flight": self._in_flight,
                "dropped": self.dropped,
                "dropped_trades": self.dropped_trades,
                "dropped_depth": self.dropped_depth,
                "dropped_not_accepting": self.dropped_not_accepting,
                "dropped_on_shutdown": self.abandoned_on_shutdown,
                "superseded_depth": self.superseded_depth,
                "drop_rate_1m": len(self._recent_drops),
                "failed": self.failed,
                "last_error": self.last_error,
                "last_write_ts": self.last_write_ts,
                "last_success_age_s": (
                    round(now - self.last_write_ts, 3) if self.last_write_ts else None
                ),
                "oldest_queued_age_s": round(now - oldest, 3) if oldest else 0.0,
                "worker_alive": alive,
                "accepting": self._accepting,
                # A DEAD worker is unhealthy even with zero drops and zero failures. That is the
                # case the old flag could not express: it read healthy while nothing was written.
                "healthy": (
                    alive and self.dropped == 0 and self.failed == 0
                ) if not self._stopping else (self.dropped == 0 and self.failed == 0),
            }

    def drain_for(self, seconds: float = 2.0) -> bool:
        """Wait until both lanes are empty AND no job is in flight. True if fully drained.

        The old helper returned once the queue looked empty, which was true the instant the last
        job was dequeued - while that job was still being written."""
        deadline = time.time() + seconds
        with self._cv:
            while time.time() < deadline:
                pending = len(self._trades) + len(self._depth)
                if self._trades and self._trades[-1] is _SENTINEL:
                    pending -= 1
                if pending == 0 and self._in_flight == 0:
                    return True
                self._cv.wait(timeout=0.01)
            pending = len(self._trades) + len(self._depth)
            return pending == 0 and self._in_flight == 0


# Constructed, NOT started. The application owns the lifecycle in its lifespan; see server.py.
FEED_WRITER = FeedWriter()


def selftest() -> int:  # noqa: C901 - a flat list of independent checks reads better than nesting
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("ownership")
    chk(FEED_WRITER.stats()["worker_alive"] is False,
        "importing this module starts NO thread - the app owns start/stop")
    chk(FEED_WRITER.submit(lambda _p: None, 1) is False,
        "submitting to a writer that was never started is refused, not silently queued")
    chk(FEED_WRITER.stats()["dropped_not_accepting"] == 1, "and that refusal is counted")

    print("non-blocking hand-off")
    seen: list[Any] = []
    writer = FeedWriter(maxsize=100, name="test-writer").start()
    started = time.perf_counter()
    for i in range(50):
        writer.submit(seen.append, i)
    elapsed = time.perf_counter() - started
    chk(elapsed < 0.05, f"50 submits returned in {elapsed * 1000:.1f}ms (callback never blocks)")
    chk(writer.drain_for(2.0), "drain_for waits for in-flight work, not just an empty queue")
    chk(len(seen) == 50, f"all 50 payloads persisted by the worker ({len(seen)})")
    chk(writer.stats()["written"] == 50, "written count matches")

    print("a slow writer does not block the feed")
    slow = FeedWriter(maxsize=100, name="slow-writer").start()
    started = time.perf_counter()
    for _ in range(20):
        slow.submit(lambda _p: time.sleep(0.05), None)      # 1.0s of work
    elapsed = time.perf_counter() - started
    chk(elapsed < 0.05,
        f"20 submits of 50ms work returned in {elapsed * 1000:.1f}ms, not 1000ms")

    print("queue age reflects the OLDEST WAITING job, not the last write")
    backlog = FeedWriter(maxsize=100, name="age-writer").start()
    for _ in range(12):
        backlog.submit(lambda _p: time.sleep(0.25), None)    # 3.0s of work
    time.sleep(1.5)
    age = backlog.stats()["oldest_queued_age_s"]
    chk(age > 1.0,
        f"after 1.5s of sustained backlog the age reads {age:.2f}s, not one job's 0.25s")
    backlog.stop(timeout=0.1)

    print("bounded, and drops are counted per lane")
    tiny = FeedWriter(maxsize=2, name="tiny-writer").start()
    tiny._accepting = True
    with tiny._cv:                       # freeze the worker so nothing drains during the test
        accepted = []
        for i in range(10):
            if len(tiny._trades) >= tiny._maxsize:
                tiny.dropped_trades += 1
                tiny.dropped += 1
                accepted.append(False)
            else:
                tiny._trades.append((lambda _p: None, i, time.time()))
                accepted.append(True)
    stats = tiny.stats()
    chk(sum(accepted) == 2, f"only maxsize events accepted ({sum(accepted)})")
    chk(stats["dropped_trades"] == 8, f"the other 8 counted in the TRADE lane ({stats['dropped_trades']})")
    chk(stats["healthy"] is False, "a writer that has dropped is NOT healthy")
    tiny.stop(timeout=0.5)

    print("a depth burst cannot drop trades")
    mixed = FeedWriter(maxsize=50, depth_maxsize=4, name="mixed-writer")
    mixed._accepting = True
    for i in range(500):
        mixed.submit_depth(lambda _p: None, i, key=f"BTCUSDT-{i % 4}")
    for i in range(50):
        mixed.submit(lambda _p: None, i)
    stats = mixed.stats()
    chk(stats["dropped_trades"] == 0,
        f"500 depth events dropped ZERO trades ({stats['dropped_trades']})")
    chk(stats["depth_depth"] == 4, f"depth coalesced to 4 live keys ({stats['depth_depth']})")
    chk(stats["superseded_depth"] == 496,
        f"the rest superseded their predecessor ({stats['superseded_depth']})")

    print("depth is latest-wins per key")
    latest: dict[str, Any] = {}
    coalesce = FeedWriter(maxsize=10, depth_maxsize=10, name="coalesce-writer")
    coalesce._accepting = True
    for value in range(20):
        coalesce.submit_depth(lambda p: latest.__setitem__("BTCUSDT", p), value, key="BTCUSDT")
    coalesce.start()
    coalesce.drain_for(2.0)
    chk(latest.get("BTCUSDT") == 19, f"only the newest snapshot was written ({latest})")
    chk(coalesce.stats()["written"] == 1, "one write, not twenty")
    coalesce.stop(timeout=0.5)

    print("a failing write is counted, never raised into the feed")
    bad = FeedWriter(maxsize=10, name="bad-writer").start()

    def explode(_payload: Any) -> None:
        raise RuntimeError("disk on fire")

    bad.submit(explode, None)
    bad.drain_for(2.0)
    stats = bad.stats()
    chk(stats["failed"] == 1, "the failure is counted")
    chk("disk on fire" in (stats["last_error"] or ""), "and the reason is retained")
    chk(stats["healthy"] is False, "the writer reports unhealthy")
    bad.stop(timeout=0.5)

    print("a dead worker is visible even with zero drops and zero failures")
    dead = FeedWriter(maxsize=10, name="dead-writer").start()
    with dead._cv:
        dead._stopping = True
        dead._cv.notify_all()
    time.sleep(0.4)
    stats = dead.stats()
    chk(stats["worker_alive"] is False, "worker_alive reports the thread is gone")
    chk(stats["dropped"] == 0 and stats["failed"] == 0, "with no drops and no failures")

    print("shutdown drains deterministically and reports what it left")
    graceful = FeedWriter(maxsize=100, name="graceful-writer").start()
    done: list[int] = []
    for i in range(10):
        graceful.submit(lambda p: (time.sleep(0.01), done.append(p)), i)
    result = graceful.stop(timeout=5.0)
    chk(result.clean is True, f"clean shutdown reported ({dict(result)})")
    chk(len(done) == 10, f"every queued job ran before exit ({len(done)}/10)")
    chk(result["abandoned"] == 0, "nothing abandoned")

    print("an INCOMPLETE drain is reported as incomplete, not as success")
    stuck = FeedWriter(maxsize=100, name="stuck-writer").start()
    for _ in range(20):
        stuck.submit(lambda _p: time.sleep(0.20), None)      # 4.0s of work
    result = stuck.stop(timeout=0.3)                         # deliberately too short
    chk(result.clean is False, "an incomplete drain is NOT reported clean")
    chk(result["abandoned"] > 0, f"and the abandoned count is exact ({result['abandoned']})")
    chk(stuck.stats()["dropped_on_shutdown"] > 0, "surfaced in stats as dropped_on_shutdown")

    print("submissions after stop are refused and counted")
    chk(graceful.submit(lambda _p: None, 1) is False, "submit after stop returns False")
    chk(graceful.stats()["dropped_not_accepting"] >= 1, "and is counted, not silently dropped")

    print("start / stop / start again (hot reload)")
    cycled = FeedWriter(maxsize=10, name="cycle-writer")
    landed: list[int] = []
    for _ in range(3):
        cycled.start()
        cycled.submit(landed.append, 1)
        cycled.drain_for(1.0)
        cycled.stop(timeout=1.0)
    chk(len(landed) == 3, f"each cycle wrote once ({len(landed)}/3)")
    chk(cycled.stats()["worker_alive"] is False, "and the writer is stopped at the end")

    writer.stop(timeout=0.5)
    slow.stop(timeout=0.5)
    mixed.stop(timeout=0.5)
    dead.stop(timeout=0.5)
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
