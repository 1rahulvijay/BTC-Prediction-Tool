"""Bounded startup wait for the required forward-evidence recorders."""
from __future__ import annotations

import argparse
import time

from forward_evidence_gate import evidence_status


def wait_until_ready(*, timeout: float, interval: float, status_fn=evidence_status) -> dict:
    deadline = time.monotonic() + max(0.0, float(timeout))
    last: dict = {}
    while True:
        last = status_fn()
        if last.get("forward_evidence") == "ADVANCING":
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(max(0.05, float(interval)))


def selftest() -> int:
    calls = iter([
        {"forward_evidence": "DARK", "recorders": {}},
        {"forward_evidence": "ADVANCING", "recorders": {}},
    ])
    ready = wait_until_ready(timeout=1.0, interval=0.01, status_fn=lambda: next(calls))
    assert ready["forward_evidence"] == "ADVANCING"
    dark = wait_until_ready(
        timeout=0.0,
        interval=0.01,
        status_fn=lambda: {"forward_evidence": "DARK", "recorders": {}},
    )
    assert dark["forward_evidence"] == "DARK"
    print("forward evidence startup wait: ALL PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    status = wait_until_ready(timeout=args.timeout, interval=args.interval)
    for name, row in (status.get("recorders") or {}).items():
        print(f"[recorder] {name}: {row.get('status')} {row.get('detail') or ''}")
    if status.get("forward_evidence") == "ADVANCING":
        print("[recorder] required forward evidence is advancing.")
        return 0
    print(f"[recorder] timeout: {status.get('banner') or 'forward evidence is dark'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
