"""Standalone recorder supervisor for a free always-on VM. Stdlib only.

WHY THIS EXISTS
    This repo lost 35 days of Polymarket capture (2026-07-04 -> 08-09) and did not notice
    until an analysis went looking for it. The official-settlement window fell entirely inside
    the hole, which blocked POLYMARKET_RESIDUAL_V1 until the settlements were backfilled by
    hand. Nothing was broken in the recorders - nothing was WATCHING them.

    So the job here is not "run recorders". It is "make a stopped recorder impossible to miss".

WHAT IT DOES
    - starts each recorder in the manifest as its own process
    - restarts on exit, with exponential backoff so a crash-looping recorder does not spin
    - writes a heartbeat JSON per recorder after every check
    - `--status` prints, and exits NONZERO on, any stream that is stale or dead

WHAT IT DELIBERATELY DOES NOT DO
    No trading, no credentials, no order placement. It launches read-only market-data
    recorders and watches them. Keep it that way: a supervisor with keys is a supervisor that
    can lose money while unattended.

    python deploy/recorder_host/supervisor.py --run
    python deploy/recorder_host/supervisor.py --status
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOST = Path(__file__).resolve().parent
REPO = HOST.parent.parent
STATE = Path(os.environ.get("BTC_RECORDER_STATE") or (HOST / "state"))
MANIFEST = HOST / "recorders.json"

#: A stream quiet for longer than this is treated as DOWN, not slow.
STALE_S = int(os.environ.get("BTC_RECORDER_STALE_S", "300"))
CHECK_S = 15
BACKOFF_MAX_S = 300


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        raise SystemExit(f"missing manifest: {MANIFEST}")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["recorders"]


def _beat_path(name: str) -> Path:
    return STATE / f"{name}.heartbeat.json"


def write_beat(name: str, **fields) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    p = _beat_path(name)
    prior = {}
    if p.exists():
        try:
            prior = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            prior = {}
    prior.update(fields)
    prior["updated_utc"] = time.time()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(prior, indent=2), encoding="utf-8")
    tmp.replace(p)          # atomic: a torn heartbeat reads as corrupt, not as healthy


def run() -> int:
    recorders = load_manifest()
    procs: dict[str, subprocess.Popen] = {}
    backoff: dict[str, float] = {r["name"]: 1.0 for r in recorders}
    next_try: dict[str, float] = {r["name"]: 0.0 for r in recorders}
    stopping = False

    def _stop(_sig, _frm):
        nonlocal stopping
        stopping = True
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _stop)
        except Exception:
            pass

    print(f"supervisor: {len(recorders)} recorders, state={STATE}", flush=True)
    while not stopping:
        now = time.time()
        for r in recorders:
            name = r["name"]
            p = procs.get(name)
            if p is not None and p.poll() is None:
                write_beat(name, alive=True, pid=p.pid)
                continue
            if p is not None:
                rc = p.poll()
                write_beat(name, alive=False, last_exit_code=rc,
                           last_exit_utc=now,
                           restarts=(json.loads(_beat_path(name).read_text(encoding="utf-8"))
                                     .get("restarts", 0) + 1) if _beat_path(name).exists() else 1)
                print(f"[{name}] exited rc={rc}; backoff {backoff[name]:.0f}s", flush=True)
                next_try[name] = now + backoff[name]
                backoff[name] = min(BACKOFF_MAX_S, backoff[name] * 2)
                procs.pop(name, None)
            if now < next_try.get(name, 0.0):
                continue
            log = STATE / f"{name}.log"
            STATE.mkdir(parents=True, exist_ok=True)
            fh = open(log, "ab")
            procs[name] = subprocess.Popen(
                [sys.executable, "-u", *r["args"]], cwd=str(REPO),
                stdout=fh, stderr=subprocess.STDOUT)
            write_beat(name, alive=True, pid=procs[name].pid, started_utc=now)
            print(f"[{name}] started pid={procs[name].pid}", flush=True)
            backoff[name] = 1.0
        time.sleep(CHECK_S)

    print("supervisor: stopping children", flush=True)
    for name, p in procs.items():
        try:
            p.terminate()
        except Exception:
            pass
    return 0


def status() -> int:
    """Print liveness. Exit nonzero if ANY stream is stale - that is the alerting hook."""
    recorders = load_manifest()
    now = time.time()
    bad = []
    print(f"{'recorder':<34}{'alive':>7}{'age':>10}{'restarts':>10}  state")
    for r in recorders:
        name = r["name"]
        p = _beat_path(name)
        if not p.exists():
            print(f"{name:<34}{'-':>7}{'-':>10}{'-':>10}  NEVER_STARTED")
            bad.append(name); continue
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            print(f"{name:<34}{'?':>7}{'?':>10}{'?':>10}  CORRUPT_HEARTBEAT")
            bad.append(name); continue
        age = now - float(b.get("updated_utc", 0))
        alive = bool(b.get("alive"))
        state = "OK" if (alive and age <= STALE_S) else ("STALE" if alive else "DOWN")
        if state != "OK":
            bad.append(name)
        print(f"{name:<34}{str(alive):>7}{age:>9.0f}s{b.get('restarts', 0):>10}  {state}")
    if bad:
        print(f"\nUNHEALTHY: {', '.join(bad)}")
        print("A stopped recorder produces a silent hole in the data. Investigate now, not "
              "when an analysis fails to find the rows.")
        return 1
    print("\nall recorders healthy")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.run:
        return run()
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
