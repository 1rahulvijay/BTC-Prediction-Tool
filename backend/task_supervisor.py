"""Ownership, criticality and health for long-running asyncio tasks.

WHY THIS EXISTS
    server.py made 14 asyncio.create_task calls and retained a handle for 4 of them. The other
    ten - the main loop, the price broadcaster, the Binance spot/futures and Coinbase feeds, the
    Polymarket client, the cross-asset client and the paper-trading service - were detached:

      * The event loop keeps only a WEAK reference to a bare task, so a task with no strong
        reference can be garbage-collected mid-flight. This is documented Python behaviour, not
        a theoretical risk.
      * An exception inside a detached task is never observed. It surfaces at best as
        "Task exception was never retrieved" during interpreter teardown, long after the feed
        went silent.
      * Shutdown never awaited them, so in-flight work was abandoned rather than closed.

    The result is a server that answers HTTP 200 with a cheerful status page while the feed
    that populates it is dead. That is worse than a crash: a crash is noticed.

WHAT THIS ADDS
    One owner that keeps a strong reference to every task, restarts crashed tasks with backoff,
    counts restarts, and reports per-task state. Criticality is DECLARED, not inferred:

        CRITICAL   the system cannot be trusted without it - its death is a trust blocker
        IMPORTANT  degrades quality; restarted and surfaced, but not a blocker
        BEST_EFFORT auxiliary; failure is logged and counted only

    An excessive restart rate is itself a fault. A task that dies and is restarted every five
    seconds forever is not healthy merely because something keeps restarting it, so a task that
    exceeds its restart budget is marked FLAPPING and, if CRITICAL, blocks trust.

    python backend/task_supervisor.py --selftest
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Awaitable, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CRITICAL = "CRITICAL"
IMPORTANT = "IMPORTANT"
BEST_EFFORT = "BEST_EFFORT"

RESTART_DELAY_S = 5.0
FLAP_WINDOW_S = 300.0
FLAP_THRESHOLD = 5          # restarts within FLAP_WINDOW_S before a task is called FLAPPING


class SupervisedTask:
    def __init__(self, name: str, factory: Callable[[], Awaitable[Any]], criticality: str,
                 restart: bool) -> None:
        self.name = name
        self.factory = factory
        self.criticality = criticality
        self.restart = restart
        self.task: asyncio.Task | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self.last_error: str | None = None
        self.last_exit_at: float | None = None
        self._recent_restarts: list[float] = []

    @property
    def flapping(self) -> bool:
        cutoff = time.time() - FLAP_WINDOW_S
        recent = [t for t in self._recent_restarts if t >= cutoff]
        return len(recent) >= FLAP_THRESHOLD

    @property
    def alive(self) -> bool:
        return bool(self.task and not self.task.done())

    def note_restart(self) -> None:
        now = time.time()
        self.restarts += 1
        self._recent_restarts.append(now)
        cutoff = now - FLAP_WINDOW_S
        self._recent_restarts = [t for t in self._recent_restarts if t >= cutoff]

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "criticality": self.criticality,
            "alive": self.alive,
            "restarts": self.restarts,
            "flapping": self.flapping,
            "uptime_s": round(time.time() - self.started_at, 1) if self.started_at else None,
            "last_error": self.last_error,
            "last_exit_age_s": (
                round(time.time() - self.last_exit_at, 1) if self.last_exit_at else None
            ),
            "healthy": self.alive and not self.flapping,
        }


class TaskSupervisor:
    """Owns every long-running task. Nothing here is fire-and-forget."""

    def __init__(self) -> None:
        self._tasks: dict[str, SupervisedTask] = {}
        self._stopping = False

    def spawn(self, name: str, factory: Callable[[], Awaitable[Any]], *,
              criticality: str = IMPORTANT, restart: bool = True) -> SupervisedTask:
        """Register and start a task. `factory` is called again on each restart.

        A coroutine FACTORY, not a coroutine: a coroutine object can only be awaited once, so a
        restart needs a fresh one. Passing the object would restart the task exactly zero times
        while appearing to support restarts."""
        if name in self._tasks and self._tasks[name].alive:
            return self._tasks[name]
        entry = SupervisedTask(name, factory, criticality, restart)
        self._tasks[name] = entry
        entry.task = asyncio.ensure_future(self._run(entry))
        entry.started_at = time.time()
        return entry

    async def _run(self, entry: SupervisedTask) -> None:
        while not self._stopping:
            try:
                await entry.factory()
                entry.last_exit_at = time.time()
                entry.last_error = "returned unexpectedly"
            except asyncio.CancelledError:
                raise
            except BaseException as exc:            # noqa: BLE001 - a supervisor survives anything
                entry.last_exit_at = time.time()
                entry.last_error = f"{type(exc).__name__}: {exc}"
            if not entry.restart or self._stopping:
                return
            entry.note_restart()
            try:
                await asyncio.sleep(RESTART_DELAY_S)
            except asyncio.CancelledError:
                raise

    def status(self) -> dict[str, Any]:
        tasks = [entry.status() for entry in self._tasks.values()]
        blockers = [
            f"task_{t['name']}_{'flapping' if t['flapping'] else 'dead'}"
            for t in tasks
            if t["criticality"] == CRITICAL and not t["healthy"]
        ]
        return {
            "tasks": tasks,
            "total": len(tasks),
            "alive": sum(1 for t in tasks if t["alive"]),
            "blockers": blockers,
            "healthy": not blockers,
        }

    async def shutdown(self, timeout: float = 5.0) -> dict[str, Any]:
        """Cancel every task and AWAIT it. Returns what did not stop in time."""
        self._stopping = True
        pending = [e.task for e in self._tasks.values() if e.task and not e.task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        stuck = [e.name for e in self._tasks.values() if e.task and not e.task.done()]
        return {"cancelled": len(pending), "stuck": stuck, "clean": not stuck}


SUPERVISOR = TaskSupervisor()


async def _selftest() -> int:
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("a supervised task is owned, not detached")
    supervisor = TaskSupervisor()
    beats = []

    async def heartbeat() -> None:
        while True:
            beats.append(time.time())
            await asyncio.sleep(0.01)

    entry = supervisor.spawn("heartbeat", heartbeat, criticality=CRITICAL)
    await asyncio.sleep(0.1)
    chk(entry.alive, "the task is running")
    chk(supervisor.status()["healthy"], "and the supervisor reports healthy")
    chk(len(beats) > 3, f"it is actually doing work ({len(beats)} beats)")

    print("a crash is observed, counted and restarted")
    crashes = []

    async def crasher() -> None:
        crashes.append(1)
        raise RuntimeError("boom")

    global RESTART_DELAY_S
    original_delay = RESTART_DELAY_S
    RESTART_DELAY_S = 0.01
    try:
        crashed = supervisor.spawn("crasher", crasher, criticality=IMPORTANT)
        await asyncio.sleep(0.15)
        chk(len(crashes) > 1, f"the task was restarted after crashing ({len(crashes)} runs)")
        chk(crashed.restarts > 0, f"restarts are counted ({crashed.restarts})")
        chk("boom" in (crashed.last_error or ""),
            f"and the error is retained, not swallowed ({crashed.last_error})")

        print("relentless restarting is itself reported as a fault")
        await asyncio.sleep(0.2)
        chk(crashed.flapping, f"a task crashing repeatedly is FLAPPING ({crashed.restarts})")
        chk(crashed.status()["healthy"] is False, "and is not reported healthy")
    finally:
        RESTART_DELAY_S = original_delay

    print("a dead CRITICAL task blocks trust")
    fatal = TaskSupervisor()

    async def dies() -> None:
        raise RuntimeError("gone")

    fatal.spawn("critical_feed", dies, criticality=CRITICAL, restart=False)
    await asyncio.sleep(0.05)
    status = fatal.status()
    chk(status["healthy"] is False, "the supervisor is unhealthy")
    chk(any("critical_feed" in b for b in status["blockers"]),
        f"and names the task as a blocker ({status['blockers']})")

    print("a BEST_EFFORT failure does not block trust")
    lenient = TaskSupervisor()
    lenient.spawn("aux", dies, criticality=BEST_EFFORT, restart=False)
    await asyncio.sleep(0.05)
    chk(lenient.status()["healthy"] is True,
        "a best-effort task's death is counted but is not a blocker")

    print("shutdown cancels and AWAITS every task")
    result = await supervisor.shutdown(timeout=2.0)
    chk(result["clean"], f"shutdown is clean ({result})")
    chk(not entry.alive, "the heartbeat is stopped")
    before = len(beats)
    await asyncio.sleep(0.05)
    chk(len(beats) == before, "and it is really stopped - no beats after shutdown")

    await fatal.shutdown(timeout=1.0)
    await lenient.shutdown(timeout=1.0)

    print("\nTASK SUPERVISOR", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_selftest()))
