"""Standalone capture app. Records market data and nothing else.

    python capture_app/run.py --record         # start capture
    python capture_app/run.py --status         # liveness; exit 1 if any stream is stale
    python capture_app/run.py --disk           # usage, partitions, what the cap would remove
    python capture_app/run.py --archive-older-than 24    # mark partitions archivable

DELIBERATE NON-CAPABILITIES
    No trading, no credentials, no order placement, no model loading, no imports from the
    trading application. It captures, and it reports whether it is still capturing. Analysis
    happens elsewhere against copied partitions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
from recorder.storage import (  # noqa: E402
    dir_size_bytes, enforce_cap, is_archived, mark_archived, partitions, write_status,
)
from recorder.streams import binance_depth, binance_trades, polymarket_books  # noqa: E402

CONFIG = json.loads((APP / "config.json").read_text(encoding="utf-8"))
DATA = Path(CONFIG.get("data_dir") or (APP / "data"))
STATE = DATA.parent / "state"
STALE_S = int(CONFIG.get("stale_seconds", 300))


async def _record() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:          # Windows
            signal.signal(s, lambda *_: stop.set())

    sym = CONFIG.get("binance_symbol", "BTCUSDT")
    tasks = []
    if CONFIG.get("streams", {}).get("binance_depth", True):
        tasks.append(binance_depth(sym, DATA, stop))
    if CONFIG.get("streams", {}).get("binance_trades", True):
        tasks.append(binance_trades(sym, DATA, stop))
    if CONFIG.get("streams", {}).get("polymarket_book", True):
        tasks.append(polymarket_books(DATA, stop))

    async def janitor():
        """Enforce the disk cap on a schedule, and shout if it cannot."""
        while not stop.is_set():
            rep = enforce_cap(DATA, float(CONFIG.get("cap_gb", 25)),
                              int(CONFIG.get("protect_recent_hours", 6)))
            write_status(DATA, "diskguard", rep)
            if rep.get("blocked"):
                print("[diskguard] OVER CAP AND NOTHING ARCHIVED IS SAFE TO DELETE. "
                      "Capture continues; disk will fill. Archive partitions or raise the cap.",
                      flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass

    tasks.append(janitor())
    print(f"capture: {len(tasks) - 1} streams -> {DATA}", flush=True)
    await asyncio.gather(*tasks, return_exceptions=True)
    print("capture: stopped", flush=True)
    return 0


def status() -> int:
    if not STATE.exists():
        print("no state yet - has --record ever run?")
        return 1
    now, bad = time.time(), []
    print(f"{'stream':<22}{'rows':>12}{'files':>8}{'gaps':>7}{'age':>9}  state")
    for name in ("binance_depth", "binance_trades", "polymarket_book"):
        p = STATE / f"{name}.json"
        if not p.exists():
            print(f"{name:<22}{'-':>12}{'-':>8}{'-':>7}{'-':>9}  NEVER_STARTED")
            bad.append(name); continue
        s = json.loads(p.read_text(encoding="utf-8"))
        age = now - float(s.get("updated_utc", 0))
        st = "OK" if age <= STALE_S else "STALE"
        if st != "OK":
            bad.append(name)
        print(f"{name:<22}{s.get('rows', 0):>12,}{s.get('files', 0):>8}"
              f"{s.get('gaps', 0):>7}{age:>8.0f}s  {st}")
    dg = STATE / "diskguard.json"
    if dg.exists():
        r = json.loads(dg.read_text(encoding="utf-8"))
        print(f"\ndisk {r.get('used_bytes', 0)/1e9:.2f} GB / cap {r.get('cap_bytes', 0)/1e9:.0f} GB"
              f"   blocked={r.get('blocked')}")
        if r.get("blocked"):
            bad.append("diskguard")
    if bad:
        print(f"\nUNHEALTHY: {', '.join(sorted(set(bad)))}")
        print("A stopped stream is a silent hole in the data. This project already lost 35 days "
              "that way.")
        return 1
    print("\nhealthy")
    return 0


def disk() -> int:
    if not DATA.exists():
        print("no data yet"); return 1
    parts = partitions(DATA)
    used = dir_size_bytes(DATA)
    arch = sum(1 for p in parts if is_archived(p))
    print(f"data {used/1e9:.2f} GB across {len(parts)} hour-partitions "
          f"({arch} archived, {len(parts)-arch} not)")
    print("\noldest 10 partitions:")
    for p in parts[:10]:
        print(f"  {'ARCH' if is_archived(p) else '    '}  "
              f"{dir_size_bytes(p)/1e6:>8.1f} MB  {p.relative_to(DATA)}")
    print("\nOnly ARCHIVED partitions are ever deleted. Upload first, then --archive-older-than.")
    return 0


def archive_older_than(hours: float) -> int:
    cutoff = time.time() - hours * 3600
    n = 0
    for p in partitions(DATA):
        if is_archived(p):
            continue
        newest = max((f.stat().st_mtime for f in p.rglob("*.parquet")), default=0)
        if newest and newest < cutoff:
            mark_archived(p); n += 1
    print(f"marked {n} partitions archived (older than {hours}h)")
    print("They are now eligible for deletion by the disk guard. Ensure they are uploaded.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--disk", action="store_true")
    ap.add_argument("--archive-older-than", type=float, metavar="HOURS")
    a = ap.parse_args()
    if a.record:
        return asyncio.run(_record())
    if a.status:
        return status()
    if a.disk:
        return disk()
    if a.archive_older_than is not None:
        return archive_older_than(a.archive_older_than)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
