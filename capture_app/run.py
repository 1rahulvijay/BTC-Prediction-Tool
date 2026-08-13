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
import os
import signal
import sys
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
from recorder.storage import (  # noqa: E402
    dir_size_bytes, enforce_cap, is_archived, mark_archived, partitions, status_dir,
    write_status,
)
from recorder.futures import (  # noqa: E402
    funding_history, futures_depth, futures_trades, liquidations, mark_funding,
    open_interest, positioning,
)
from recorder.settlements import poll_settlements  # noqa: E402
from recorder.streams import (  # noqa: E402
    binance_depth, binance_trades, polymarket_books, polymarket_reference,
)
from recorder.archive import archive_completed, archive_loop, verify_archives  # noqa: E402
from recorder.health import runtime_metrics  # noqa: E402
from recorder.options import deribit_options  # noqa: E402
from recorder.pyth import pyth_reference  # noqa: E402
from recorder.quality import quality_report, write_quality_report  # noqa: E402
from recorder.venues import (  # noqa: E402
    bybit_funding_history, bybit_open_interest, bybit_quotes, bybit_trades,
    coinbase_ticker,
)

CONFIG = json.loads((APP / "config.json").read_text(encoding="utf-8"))
_data_setting = os.environ.get("CAPTURE_DATA_DIR") or CONFIG.get("data_dir")
DATA = Path(_data_setting) if _data_setting else APP / "data"
if not DATA.is_absolute():
    DATA = (APP / DATA).resolve()
STATE = status_dir(DATA)
STALE_S = int(CONFIG.get("stale_seconds", 300))
ARCHIVE_BUCKET = os.environ.get("CAPTURE_GCS_BUCKET") or CONFIG.get("archive_gcs_bucket")
ARCHIVE_PREFIX = os.environ.get("CAPTURE_GCS_PREFIX") or CONFIG.get(
    "archive_gcs_prefix", "btc-capture",
)


def _validate_config() -> None:
    symbol = str(CONFIG.get("binance_symbol") or "")
    if not symbol.isalnum():
        raise ValueError("binance_symbol must be alphanumeric")
    for key in ("cap_gb", "protect_recent_hours", "stale_seconds",
                "settlement_poll_seconds", "open_interest_poll_seconds",
                "funding_history_poll_seconds", "positioning_poll_seconds",
                "polymarket_discovery_seconds", "deribit_poll_seconds",
                "pyth_poll_seconds", "bybit_open_interest_poll_seconds",
                "bybit_funding_poll_seconds"):
        if float(CONFIG.get(key, 0)) <= 0:
            raise ValueError(f"{key} must be positive")
    if ARCHIVE_BUCKET:
        for key in ("archive_after_hours", "archive_interval_seconds"):
            if float(CONFIG.get(key, 0)) <= 0:
                raise ValueError(f"{key} must be positive when archival is enabled")
        target = int(CONFIG.get("archive_target_file_mb", 256))
        if not 32 <= target <= 512:
            raise ValueError("archive_target_file_mb must be between 32 and 512")


def _enabled_streams() -> list[str]:
    streams = CONFIG.get("streams", {})
    names = []
    if streams.get("binance_depth", True):
        names += ["binance_depth", "binance_depth_snapshot"]
    if streams.get("binance_trades", True):
        names.append("binance_trades")
    if streams.get("futures_depth", True):
        names += ["futures_depth", "futures_depth_snapshot"]
    if streams.get("futures_trades", True):
        names.append("futures_trades")
    if streams.get("futures_mark", True):
        names.append("futures_mark")
    if streams.get("futures_liquidations", True):
        names.append("futures_liquidations")
    if streams.get("futures_open_interest", True):
        names.append("futures_open_interest")
    if streams.get("futures_funding_history", True):
        names.append("futures_funding_history")
    if streams.get("futures_positioning", True):
        names.append("futures_positioning")
    if streams.get("bybit_quotes", True):
        names.append("bybit_quotes")
    if streams.get("bybit_trades", True):
        names.append("bybit_trades")
    if streams.get("bybit_open_interest", True):
        names.append("bybit_open_interest")
    if streams.get("bybit_funding_history", True):
        names.append("bybit_funding_history")
    if streams.get("coinbase_ticker", True):
        names.append("coinbase_ticker")
    if streams.get("deribit_options", True):
        names.append("deribit_options")
    if streams.get("pyth_reference", True):
        names.append("pyth_reference")
    if streams.get("polymarket_book", True):
        names += ["polymarket_book", "polymarket_trades", "polymarket_market_meta",
                  "polymarket_market_events"]
    if streams.get("polymarket_reference", True):
        names.append("polymarket_reference")
    if streams.get("polymarket_settlement", True):
        names.append("polymarket_settlement")
    if ARCHIVE_BUCKET:
        names.append("archive_uploader")
    names.append("collector_runtime")
    return names


def _data_streams() -> list[str]:
    return [name for name in _enabled_streams()
            if name != "archive_uploader"]


async def _record() -> int:
    _validate_config()
    DATA.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:          # Windows
            signal.signal(s, lambda *_: stop.set())

    sym = CONFIG.get("binance_symbol", "BTCUSDT")
    jobs = []
    if CONFIG.get("streams", {}).get("binance_depth", True):
        jobs.append(("binance_depth", binance_depth(sym, DATA, stop)))
    if CONFIG.get("streams", {}).get("binance_trades", True):
        jobs.append(("binance_trades", binance_trades(sym, DATA, stop)))
    # FUTURES. Separate host from spot, and the instrument the paper lane actually trades.
    # Five blocked lanes need data that exists only here; spot alone would leave them blocked
    # while looking like progress.
    S = CONFIG.get("streams", {})
    if S.get("futures_depth", True):
        jobs.append(("futures_depth", futures_depth(sym, DATA, stop)))
    if S.get("futures_trades", True):
        jobs.append(("futures_trades", futures_trades(sym, DATA, stop)))
    if S.get("futures_mark", True):
        jobs.append(("futures_mark", mark_funding(sym, DATA, stop)))
    if S.get("futures_liquidations", True):
        jobs.append(("futures_liquidations", liquidations(
            DATA, stop, symbol_filter=CONFIG.get("liquidation_symbol_filter"))))
    if S.get("futures_open_interest", True):
        jobs.append(("futures_open_interest", open_interest(
            sym, DATA, stop, int(CONFIG.get("open_interest_poll_seconds", 60)))))
    if S.get("futures_funding_history", True):
        jobs.append(("futures_funding_history", funding_history(
            sym, DATA, stop, int(CONFIG.get("funding_history_poll_seconds", 300)))))
    if S.get("futures_positioning", True):
        jobs.append(("futures_positioning", positioning(
            sym, DATA, stop, interval_s=int(CONFIG.get("positioning_poll_seconds", 300)))))
    if S.get("bybit_quotes", True):
        jobs.append(("bybit_quotes", bybit_quotes(DATA, stop)))
    if S.get("bybit_trades", True):
        jobs.append(("bybit_trades", bybit_trades(DATA, stop)))
    if S.get("bybit_open_interest", True):
        jobs.append(("bybit_open_interest", bybit_open_interest(
            DATA, stop, int(CONFIG.get("bybit_open_interest_poll_seconds", 60)))))
    if S.get("bybit_funding_history", True):
        jobs.append(("bybit_funding_history", bybit_funding_history(
            DATA, stop, int(CONFIG.get("bybit_funding_poll_seconds", 300)))))
    if S.get("coinbase_ticker", True):
        jobs.append(("coinbase_ticker", coinbase_ticker(DATA, stop)))
    if S.get("deribit_options", True):
        jobs.append(("deribit_options", deribit_options(
            DATA, stop, int(CONFIG.get("deribit_poll_seconds", 60)))))
    if S.get("pyth_reference", True):
        jobs.append(("pyth_reference", pyth_reference(
            DATA, stop, float(CONFIG.get("pyth_poll_seconds", 2)),
            str(os.environ.get("PYTH_ENDPOINT") or CONFIG.get("pyth_endpoint") or
                "https://hermes.pyth.network/v2/updates/price/latest"))))
    if CONFIG.get("streams", {}).get("polymarket_book", True):
        jobs.append(("polymarket_book", polymarket_books(
            DATA, stop, int(CONFIG.get("polymarket_discovery_seconds", 15)))))
    if S.get("polymarket_reference", True):
        jobs.append(("polymarket_reference", polymarket_reference(DATA, stop)))
    # Settlements run in the SAME process as the quotes deliberately. Two jobs with independent
    # lifetimes are what produced 916 rounds of quotes and 6,725 settlements with a zero-row
    # intersection.
    if CONFIG.get("streams", {}).get("polymarket_settlement", True):
        jobs.append(("polymarket_settlement", poll_settlements(
            DATA, stop, interval_s=int(CONFIG.get("settlement_poll_seconds", 300)))))
    if ARCHIVE_BUCKET:
        jobs.append(("archive_uploader", archive_loop(
            DATA, stop, bucket_name=str(ARCHIVE_BUCKET),
            prefix=str(ARCHIVE_PREFIX or "btc-capture"),
            older_than_hours=float(CONFIG.get("archive_after_hours", 6)),
            interval_s=int(CONFIG.get("archive_interval_seconds", 900)),
            target_file_mb=int(CONFIG.get("archive_target_file_mb", 256)),
        )))

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

    jobs.append(("diskguard", janitor()))

    jobs.append(("collector_runtime", runtime_metrics(DATA, stop)))
    tasks = {asyncio.create_task(coro, name=name): name for name, coro in jobs}
    print(f"capture: {len(jobs) - 1} stream groups -> {DATA}", flush=True)
    try:
        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                name = tasks.pop(task)
                if stop.is_set():
                    continue
                exc = task.exception()
                raise RuntimeError(f"capture task {name} exited unexpectedly") from exc
            if stop.is_set():
                break
    finally:
        stop.set()
        # Give every recorder a chance to synchronously flush its final buffer. Immediate task
        # cancellation previously discarded up to one flush interval from most streams on a
        # service restart even though the shutdown itself looked clean.
        pending = set(tasks)
        if pending:
            _, pending = await asyncio.wait(pending, timeout=30)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    print("capture: stopped", flush=True)
    return 0


def status() -> int:
    _validate_config()
    if not STATE.exists():
        print("no state yet - has --record ever run?")
        return 1
    now, bad = time.time(), []
    print(f"{'stream':<22}{'rows':>12}{'files':>8}{'gaps':>7}{'age':>9}  state")
    high_rate = {"binance_depth", "binance_trades", "futures_depth", "futures_trades",
                 "futures_mark", "polymarket_book", "polymarket_trades",
                 "polymarket_reference", "bybit_quotes", "bybit_trades",
                 "coinbase_ticker"}
    polled_fresh = {"pyth_reference", "deribit_options"}
    snapshots = {"binance_depth_snapshot", "futures_depth_snapshot"}
    websocket = high_rate | {"futures_liquidations", "polymarket_trades",
                             "polymarket_market_meta", "polymarket_market_events"}
    for name in _enabled_streams():
        p = STATE / f"{name}.json"
        if not p.exists():
            print(f"{name:<22}{'-':>12}{'-':>8}{'-':>7}{'-':>9}  NEVER_STARTED")
            bad.append(name); continue
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            print(f"{name:<22}{'-':>12}{'-':>8}{'-':>7}{'-':>9}  CORRUPT_STATE")
            bad.append(name)
            continue
        age = now - float(s.get("updated_utc", 0))
        data_age = now - float(s.get("last_data_utc") or 0)
        connection_age = now - float(s.get("connected_since_utc") or s.get("updated_utc", 0))
        if age > STALE_S:
            st = "STALE"
        elif s.get("fallback_active"):
            st = "DEGRADED"
        elif s.get("last_error") and name in {
            "futures_open_interest", "futures_funding_history", "futures_positioning",
            "polymarket_settlement", "archive_uploader", "deribit_options",
            "pyth_reference", "bybit_open_interest", "bybit_funding_history"
        }:
            st = "ERROR"
        elif name == "collector_runtime" and s.get("resource_pressure"):
            st = "DEGRADED"
        elif name in snapshots:
            st = "READY" if int(s.get("rows", 0)) > 0 else "NO_DATA"
        elif name in websocket and s.get("connected") is not True:
            st = "DISCONNECTED"
        elif name in (high_rate | polled_fresh) and not s.get("last_data_utc") \
                and connection_age > STALE_S:
            st = "NO_DATA"
        elif name in (high_rate | polled_fresh) and s.get("last_data_utc") \
                and data_age > STALE_S:
            st = "NO_DATA"
        else:
            st = "OK"
        if st not in ("OK", "READY"):
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


def archive_older_than(hours: float, confirmed: bool = False) -> int:
    if not confirmed:
        print("REFUSING: manual archive marking can make data deletable.")
        print("Verify the upload independently, then add --confirm-uploaded.")
        return 2
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


def archive_once() -> int:
    _validate_config()
    if not ARCHIVE_BUCKET:
        print("REFUSING: configure archive_gcs_bucket or CAPTURE_GCS_BUCKET first.")
        return 2
    report = archive_completed(
        DATA, str(ARCHIVE_BUCKET), str(ARCHIVE_PREFIX or "btc-capture"),
        float(CONFIG.get("archive_after_hours", 6)),
        int(CONFIG.get("archive_target_file_mb", 256)),
    )
    print(json.dumps(report, indent=2))
    return 1 if report.get("errors") else 0


def verify_archive() -> int:
    _validate_config()
    if not ARCHIVE_BUCKET:
        print("REFUSING: configure archive_gcs_bucket or CAPTURE_GCS_BUCKET first.")
        return 2
    report = verify_archives(DATA, str(ARCHIVE_BUCKET))
    print(json.dumps(report, indent=2))
    if not report.get("checked"):
        print("No locally marked archive partitions exist; nothing was verified.")
        return 1
    return 1 if report.get("failed") else 0


def quality() -> int:
    _validate_config()
    report = quality_report(DATA, _data_streams(), STALE_S)
    path = write_quality_report(DATA, report)
    print(json.dumps(report, indent=2))
    print(f"quality report: {path}")
    return 0 if report.get("ok") else 1


def selftest() -> int:
    suite = unittest.defaultTestLoader.discover(str(APP / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--disk", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--archive-once", action="store_true")
    ap.add_argument("--verify-archive", action="store_true")
    ap.add_argument("--quality", action="store_true")
    ap.add_argument("--archive-older-than", type=float, metavar="HOURS")
    ap.add_argument("--confirm-uploaded", action="store_true")
    a = ap.parse_args()
    if a.record:
        return asyncio.run(_record())
    if a.status:
        return status()
    if a.disk:
        return disk()
    if a.selftest:
        return selftest()
    if a.archive_once:
        return archive_once()
    if a.verify_archive:
        return verify_archive()
    if a.quality:
        return quality()
    if a.archive_older_than is not None:
        return archive_older_than(a.archive_older_than, a.confirm_uploaded)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
