"""Disk/readiness preflight for a long-window (>=1000d) retrain.

WHY THIS EXISTS
    The original gate asked one question: "are >=1000 raw aggTrade CSVs cached?" If not, it
    demanded 300GB free and aborted. That is right for a machine that has never downloaded
    anything, and WRONG for this one.

    Measured 2026-07-26: `data/backfill_cache/` holds 0 CSVs, but the DERIVED sources already
    span the full window -

        trade_features_backfill.parquet   1,854,720 rows   2023-01-15 -> 2026-07-25   1287d
        crossvenue_flow.parquet           1,851,635 rows   2023-01-16 -> 2026-07-24   1285d

    The bulk download already happened and the cache was pruned afterwards. `build_research_matrix`
    reads those parquets directly, so rebuilding the 1m matrix to 1000d needs NO bulk re-download -
    yet the CSV-count gate rejected the run for lack of 300GB, permanently, with no way forward
    except deleting the very thing that made the run cheap.

    So the question this asks instead is: "can the requested window actually be SERVED from what is
    already on disk?" Three outcomes:

        REBUILD    derived sources already cover the window -> 80GB floor, no bulk download
        RESUME     >=1000 raw CSVs cached                   -> 80GB floor
        FIRST      neither                                  -> 300GB floor, bulk download expected

Exit codes: 0 = proceed, 2 = insufficient disk (start.bat aborts), 3 = bad usage.

    python backend/preflight_longwindow.py --days 1000
    python backend/preflight_longwindow.py --selftest
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")

# Sources build_research_matrix.py actually reads. If these already span the window, the matrix
# rebuild is CPU-bound, not download-bound.
DERIVED_SOURCES = {
    "trade_features_backfill.parquet": "candle_ts",
    "crossvenue_flow.parquet": "ts_ms",
}

LONG_WINDOW_DAYS = 1000        # threshold at which the heavy guard applies at all
FIRST_BUILD_FREE_GB = 300      # bulk aggTrade download + sequence memmap + parquet rewrites
RESUME_FREE_GB = 80            # sequence memmap, staged bundles, DuckDB/parquet rewrites
CACHED_FILES_FOR_RESUME = 1000
COVERAGE_TOLERANCE = 0.98      # a 1000d request is served by 980d of data; archives have gaps


def derived_span_days(data_dir: Path = DATA) -> dict[str, float]:
    """Days covered by each derived source. Missing/unreadable -> 0.0, never an optimistic guess."""
    import pandas as pd

    spans: dict[str, float] = {}
    for name, column in DERIVED_SOURCES.items():
        path = data_dir / name
        try:
            series = pd.read_parquet(path, columns=[column])[column]
            spans[name] = max(0.0, (int(series.max()) - int(series.min())) / 86_400_000.0)
        except Exception:
            spans[name] = 0.0
    return spans


def classify(days: int, free_gb: float, cached_files: int, spans: dict[str, float]) -> dict:
    """Decide build mode and the disk floor that applies. Pure function - see selftest."""
    if days < LONG_WINDOW_DAYS:
        return {
            "mode": "SHORT_WINDOW", "required_gb": 0, "ok": True,
            "reason": f"{days}d is below the {LONG_WINDOW_DAYS}d long-window guard",
        }
    # Every derived source must cover the window; the weakest one decides.
    covered = min(spans.values()) if spans else 0.0
    if spans and covered >= days * COVERAGE_TOLERANCE:
        mode, required = "REBUILD", RESUME_FREE_GB
        reason = (
            f"derived sources already span {covered:.0f}d >= {days}d requested "
            f"(x{COVERAGE_TOLERANCE:.2f}); no bulk download needed"
        )
    elif cached_files >= CACHED_FILES_FOR_RESUME:
        mode, required = "RESUME", RESUME_FREE_GB
        reason = f"{cached_files} daily source files cached; this is a resume, not a first build"
    else:
        mode, required = "FIRST_BUILD", FIRST_BUILD_FREE_GB
        reason = (
            f"derived sources cover only {covered:.0f}d of {days}d and {cached_files} files are "
            f"cached; a bulk download is expected"
        )
    return {
        "mode": mode,
        "required_gb": required,
        "ok": free_gb >= required,
        "reason": reason,
        "covered_days": round(covered, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=int(os.environ.get("BTC_HISTORICAL_DAYS") or 0))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.days <= 0:
        print("[preflight] usage: --days N (or set BTC_HISTORICAL_DAYS)")
        return 3

    free_gb = shutil.disk_usage(str(ROOT.anchor or "C:/")).free / 1e9
    cache = DATA / "backfill_cache"
    cached = len(list(cache.glob("BTCUSDT*aggTrades-*.csv"))) if cache.is_dir() else 0
    spans = derived_span_days()
    verdict = classify(args.days, free_gb, cached, spans)

    print(f"[preflight] window={args.days}d free={free_gb:.0f}GB cached_csv={cached}")
    for name, span in sorted(spans.items()):
        mark = "OK " if span >= args.days * COVERAGE_TOLERANCE else "SHORT"
        print(f"[preflight]   {mark} {name:34} {span:7.0f}d")
    print(f"[preflight] mode={verdict['mode']} requires>={verdict['required_gb']}GB - "
          f"{verdict['reason']}")
    if not verdict["ok"]:
        print(f"[preflight] ERROR: {verdict['mode']} needs {verdict['required_gb']}GB free, "
              f"have {free_gb:.0f}GB.")
        return 2
    print("[preflight] OK - proceeding.")
    return 0


def selftest() -> int:
    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    full = {"trade_features_backfill.parquet": 1287.0, "crossvenue_flow.parquet": 1285.0}
    none = {"trade_features_backfill.parquet": 0.0, "crossvenue_flow.parquet": 0.0}
    partial = {"trade_features_backfill.parquet": 1287.0, "crossvenue_flow.parquet": 360.0}

    # The selected production window must stay behind the heavy-build disk guard.
    v = classify(1000, free_gb=150, cached_files=0, spans=full)
    chk(v["mode"] == "REBUILD", "1000d derived coverage -> REBUILD, never SHORT_WINDOW")
    chk(v["required_gb"] == RESUME_FREE_GB, "1000d rebuild keeps the 80GB safety floor")

    # THE ORIGINAL 1265d CASE THIS MACHINE WAS IN, and the one the old gate got wrong.
    v = classify(1265, free_gb=150, cached_files=0, spans=full)
    chk(v["mode"] == "REBUILD", "derived sources covering the window -> REBUILD, not FIRST_BUILD")
    chk(v["ok"], "150GB is enough for a REBUILD (old gate wrongly demanded 300GB and aborted)")
    chk(v["required_gb"] == RESUME_FREE_GB, "REBUILD uses the 80GB floor")

    v = classify(1265, free_gb=150, cached_files=0, spans=none)
    chk(v["mode"] == "FIRST_BUILD", "no derived coverage and no cache -> FIRST_BUILD")
    chk(not v["ok"], "a genuine first build is still refused at 150GB")

    v = classify(1265, free_gb=150, cached_files=1200, spans=none)
    chk(v["mode"] == "RESUME", "a large CSV cache is still recognised as a resume")
    chk(v["ok"], "resume proceeds at 150GB")

    # The weakest source decides - one long source must not vouch for a short one.
    v = classify(1265, free_gb=150, cached_files=0, spans=partial)
    chk(v["mode"] == "FIRST_BUILD", "one SHORT source blocks REBUILD even if the other is long")
    chk(v["covered_days"] == 360.0, "coverage reports the weakest source, not the best")

    v = classify(400, free_gb=20, cached_files=0, spans=none)
    chk(v["mode"] == "SHORT_WINDOW" and v["ok"], "sub-1000d runs are not gated by this guard")

    v = classify(1265, free_gb=79, cached_files=0, spans=full)
    chk(not v["ok"], "REBUILD still refuses below the 80GB floor")
    v = classify(1265, free_gb=81, cached_files=0, spans=full)
    chk(v["ok"], "REBUILD proceeds just above the floor")

    # Tolerance: archives have gaps, so demanding an exact match would never pass.
    v = classify(1265, free_gb=150, cached_files=0,
                 spans={"a": 1265 * 0.99, "b": 1265 * 0.99})
    chk(v["mode"] == "REBUILD", "a 1% archive gap still counts as covering the window")
    v = classify(1265, free_gb=150, cached_files=0,
                 spans={"a": 1265 * 0.90, "b": 1265 * 0.90})
    chk(v["mode"] == "FIRST_BUILD", "a 10% shortfall does NOT count as covering the window")

    # Real data, if present: this is the machine's actual state.
    spans = derived_span_days()
    if any(spans.values()):
        real = classify(1000, free_gb=150, cached_files=0, spans=spans)
        chk(real["mode"] == "REBUILD",
            f"LIVE: this machine classifies as {real['mode']} at {real['covered_days']}d")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
