"""DuckDB writes leave the websocket thread - but only the ones that decide nothing. (4.16)

    python backend/test_maintenance_journal_split.py

THE DEFECT

`data_ingestion._emit` calls every book callback SYNCHRONOUSLY inside the websocket read loop:

    for cb in self.callbacks.get(event, []):
        cb(data)

`BinancePaperService.on_book` then performed portfolio marking, funding application, governor
queries, transactional persistence and equity snapshots on that thread. A slow DuckDB operation
delays consumption of the very futures feed whose freshness the engine depends on.

WHY THIS IS NOT "MOVE ALL DB WORK TO A WORKER"

The audit proposed enqueueing an immutable event and letting a worker own evaluation and all
DuckDB writes. Most of the work cannot move without trading a correctness property for a
throughput one:

    _process_pending      fill latency is deliberately faithful to the live quote stream
    portfolio.mark        the exit triggers read the marked position
    apply_funding         changes realised P&L, which the governor then reads
    _governor_decision    decides can_open / must_flatten
    _queue_*_exits        risk exits must be queued promptly

What IS deferrable: the funding AUDIT LOG and the EQUITY TELEMETRY. Nothing reads either back to
make a decision, and they are the writes that grow without bound.

This test pins the split in both directions - the two writes that moved, and the five that did
not. The second half matters more: a future "optimisation" that moves an exit onto the worker
would pass a test that only checked the queue works.
"""
from __future__ import annotations

import ast
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def main() -> int:
    from binance_paper.service import _MaintenanceJournal

    print("1. the journal never blocks its caller")
    j = _MaintenanceJournal(maxlen=8).start()
    try:
        gate = threading.Event()
        done = threading.Event()
        j.submit(lambda: gate.wait(timeout=5.0))       # occupies the worker
        t0 = time.time()
        for _ in range(6):
            j.submit(lambda: done.set())
        submit_elapsed = time.time() - t0
        chk(submit_elapsed < 0.25,
            f"six submits behind a blocked worker returned in {submit_elapsed*1000:.0f}ms - the "
            f"websocket thread is never held by a slow DuckDB write")
        gate.set()
        deadline = time.time() + 5.0
        while j.written < 7 and time.time() < deadline:
            time.sleep(0.01)
        chk(j.written == 7, f"and all 7 items were eventually written ({j.written})")
    finally:
        j.stop()

    print("2. a full queue drops the OLDEST and COUNTS it")
    j2 = _MaintenanceJournal(maxlen=4)                  # deliberately not started
    for _ in range(10):
        j2.submit(lambda: None)
    chk(j2.dropped == 6,
        f"10 submits into a 4-slot queue drop 6 ({j2.dropped}) - counted, not silent. A journal "
        f"that quietly discards is worse than one that blocks, because the gap is invisible")
    drain = j2.stop()
    chk(drain["abandoned_at_shutdown"] == 4,
        f"and stop() reports what it never got to write ({drain['abandoned_at_shutdown']})")

    print("3. a failing write is counted, not swallowed, and does not kill the worker")
    j3 = _MaintenanceJournal().start()
    try:
        def boom():
            raise RuntimeError("duckdb is locked")
        j3.submit(boom)
        j3.submit(lambda: None)
        deadline = time.time() + 5.0
        while (j3.errors + j3.written) < 2 and time.time() < deadline:
            time.sleep(0.01)
        chk(j3.errors == 1 and j3.written == 1,
            f"one error and one success ({j3.errors}/{j3.written}) - the worker survives a bad "
            f"write and keeps draining")
    finally:
        j3.stop()

    print("4. shutdown DRAINS rather than abandoning")
    written = []
    j4 = _MaintenanceJournal().start()
    for i in range(25):
        j4.submit(lambda n=i: written.append(n))
    res = j4.stop(timeout=5.0)
    chk(len(written) == 25 and res["abandoned_at_shutdown"] == 0,
        f"all 25 queued rows were written before shutdown returned ({len(written)}, "
        f"abandoned={res['abandoned_at_shutdown']})")

    print("5. THE SPLIT: only decision-free writes moved off the callback")
    src = (BACKEND / "binance_paper" / "service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    on_book = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "on_book")
    body = ast.unparse(on_book)

    for call, why in (
        ("append_equity_snapshots", "equity telemetry"),
        ("append_event", "the funding audit row"),
    ):
        chk("self.journal.submit" in body and call in body,
            f"{why} goes through the journal")

    # THE HALF THAT MATTERS MORE. These must remain INLINE - each one feeds a decision made
    # later in the same callback, and deferring any of them changes behaviour rather than
    # latency.
    for call, why in (
        ("_process_pending", "fill processing stays latency-faithful to the quote stream"),
        ("self.portfolio.mark", "marking feeds the exit triggers below it"),
        ("apply_funding", "funding changes realised P&L, which the governor reads"),
        ("_governor_decision", "the governor decides can_open / must_flatten"),
        ("_queue_triggered_exits", "risk exits must be queued promptly"),
    ):
        idx = body.find(call)
        chk(idx > 0, f"{call} is still called from on_book")
        if idx > 0:
            # It must not be wrapped in a journal.submit(...) lambda.
            window = body[max(0, idx - 160):idx]
            chk("journal.submit" not in window,
                f"and it is INLINE, not deferred - {why}")

    print("6. lifecycle is wired to the service, not left to chance")
    chk("self.journal.start()" in src, "the journal starts with the service")
    chk("self.journal.stop()" in src, "and is drained on shutdown")
    _stop = src.index("self.journal.stop()")
    _close = src.index("self.persistence.close()")
    chk(_stop < _close,
        "the drain happens BEFORE persistence.close() - queued rows are written through that "
        "same connection, so closing first would abandon them")

    print("\nMAINTENANCE JOURNAL SPLIT:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
