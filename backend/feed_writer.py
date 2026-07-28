"""Bounded, non-blocking persistence queue for WebSocket feed callbacks.

`handle_trade` and `handle_depth` are plain synchronous callbacks invoked from the feed path, and
each called `database.log_*_parquet()` directly under a comment reading "fire and forget". It was
not fire and forget - it was an ordinary blocking call. A slow disk, a parquet flush or a file
lock stalled the callback, and with it the feed.

The callback's job is to validate, timestamp and hand off. Persistence belongs to a worker.

Design choices worth stating:

  * BOUNDED queue. An unbounded queue converts a disk stall into unbounded memory growth and
    turns a slow writer into an OOM kill. When the queue is full the newest event is DROPPED and
    counted - losing a diagnostic row is strictly better than stalling the feed or dying.
  * Drops are COUNTED and surfaced, never silent. `stats()` reports depth, oldest queued age,
    drops and last error, so a stalled writer is visible rather than inferred.
  * The worker is a daemon thread: it must never keep a shutting-down process alive.
  * Diagnostics only. This queue carries raw trade/depth logging. It is NOT the evidence path -
    complete-trade evidence goes through log_forward_prediction_v2, which is transactional and
    must not be made lossy.

    python backend/feed_writer.py --selftest
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_MAXSIZE = 10_000


class FeedWriter:
    """One background writer draining a bounded queue of (handler, payload) jobs."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, name: str = "feed-writer") -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.enqueued = 0
        self.written = 0
        self.dropped = 0
        self.failed = 0
        self.last_error: str | None = None
        self.last_write_ts: float | None = None
        self._oldest_enqueued_ts: float | None = None

    def start(self) -> "FeedWriter":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._drain, name=self._name, daemon=True)
        self._thread.start()
        return self

    def submit(self, handler: Callable[[Any], Any], payload: Any) -> bool:
        """Enqueue without blocking. False means the event was dropped (queue full)."""
        try:
            self._queue.put_nowait((handler, payload, time.time()))
        except queue.Full:
            with self._lock:
                self.dropped += 1
            return False
        with self._lock:
            self.enqueued += 1
            if self._oldest_enqueued_ts is None:
                self._oldest_enqueued_ts = time.time()
        return True

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                handler, payload, _ = self._queue.get(timeout=0.25)
            except queue.Empty:
                with self._lock:
                    self._oldest_enqueued_ts = None
                continue
            try:
                handler(payload)
                with self._lock:
                    self.written += 1
                    self.last_write_ts = time.time()
            except Exception as exc:                       # noqa: BLE001 - counted, not hidden
                with self._lock:
                    self.failed += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._queue.task_done()
                with self._lock:
                    self._oldest_enqueued_ts = (
                        time.time() if not self._queue.empty() else None
                    )

    def stats(self) -> dict[str, Any]:
        with self._lock:
            oldest = self._oldest_enqueued_ts
            return {
                "depth": self._queue.qsize(),
                "maxsize": self._queue.maxsize,
                "enqueued": self.enqueued,
                "written": self.written,
                "dropped": self.dropped,
                "failed": self.failed,
                "last_error": self.last_error,
                "last_write_ts": self.last_write_ts,
                "oldest_queued_age_s": (
                    round(time.time() - oldest, 3) if oldest else 0.0
                ),
                # A queue that is filling, or has ever dropped, is the signal that matters.
                "healthy": self.dropped == 0 and self.failed == 0,
            }

    def drain_for(self, seconds: float = 2.0) -> None:
        """Wait for the queue to empty. Test and shutdown helper only."""
        deadline = time.time() + seconds
        while time.time() < deadline and not self._queue.empty():
            time.sleep(0.01)

    def stop(self, timeout: float = 2.0) -> None:
        self.drain_for(timeout)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)


FEED_WRITER = FeedWriter().start()


def selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("non-blocking hand-off")
    seen: list[Any] = []
    writer = FeedWriter(maxsize=100, name="test-writer").start()
    started = time.perf_counter()
    for i in range(50):
        writer.submit(seen.append, i)
    elapsed = time.perf_counter() - started
    chk(elapsed < 0.05, f"50 submits returned in {elapsed * 1000:.1f}ms (callback never blocks)")
    writer.drain_for(2.0)
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
    slow.stop(timeout=0.1)

    print("bounded, and drops are counted")
    tiny = FeedWriter(maxsize=2, name="tiny-writer")        # not started: nothing drains
    accepted = [tiny.submit(lambda _p: None, i) for i in range(10)]
    stats = tiny.stats()
    chk(sum(accepted) == 2, f"only maxsize events accepted ({sum(accepted)})")
    chk(stats["dropped"] == 8, f"the other 8 are counted as dropped ({stats['dropped']})")
    chk(stats["healthy"] is False, "a writer that has dropped is NOT healthy")
    chk(stats["depth"] == 2, "queue depth is reported")

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

    writer.stop(timeout=0.5)
    tiny.stop(timeout=0.1)
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
