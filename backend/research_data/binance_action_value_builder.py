"""Apply the Binance action-value engine to the 1-minute archive, on disjoint windows.

WHAT IT ESTABLISHES
    The same two ceilings the Polymarket engine established, for the perpetual:

        perfect exit timing   the maximum favourable excursion inside the window (this IS MFE)
        best fixed rule       long or short, held to the horizon, no foresight
        WAIT                  exactly zero

    If the best fixed rule is negative and WAIT wins, the lane pays nothing without a model. If
    the ceiling is also small relative to the round trip, no model can rescue it either, and
    that is worth knowing before building four heads to find out.

NON-OVERLAPPING WINDOWS, DELIBERATELY
    Striding by the horizon makes every window disjoint. Overlapping windows once let this
    repository report a +1230 bps result across "11 expiries" that carried roughly ONE
    independent observation. Dispersion computed on overlapping windows is not dispersion.

    The cost of the stride is sample size: 2h windows give 4,320 observations rather than
    518,400. That is the correct trade, and the count is printed so it can be judged.

    python backend/research_data/binance_action_value_builder.py --selftest
    python backend/research_data/binance_action_value_builder.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "backend" / "binance_alpha"))

from action_value import HORIZONS_M, round_trip_bps, select, value_actions  # noqa: E402

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
BARS = DATA_DIR / "btc_1m_data.csv"
OUTPUT = DATA_DIR / "research" / "binance_action_values_v1.manifest.json"


def load_bars():
    """ts_ms, open/high/low/close from the 1-minute archive, in time order."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        return con.execute(
            f"SELECT ts_ms, high, low, close FROM read_csv_auto('{BARS.as_posix()}') "
            f"ORDER BY ts_ms").df()
    finally:
        con.close()


def evaluate_horizon(frame, horizon_m: int, cost_bps: float) -> dict:
    """Value every DISJOINT window of `horizon_m` minutes."""
    close = frame["close"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    total = len(close)

    starts = np.arange(0, total - horizon_m, horizon_m)     # stride = horizon -> disjoint
    per_action: dict[str, list[float]] = {}
    chosen: dict[str, int] = {}
    for start in starts:
        stop = start + horizon_m
        # Extremes STRICTLY inside the window: bars after entry, up to and including the exit.
        values = value_actions(
            entry=close[start], close_at_horizon=close[stop],
            window_high=float(high[start + 1:stop + 1].max()),
            window_low=float(low[start + 1:stop + 1].min()),
            horizon_m=horizon_m, cost_bps=cost_bps)
        for value in values:
            if value.net_bps is None:
                continue
            per_action.setdefault(value.action.value, []).append(value.net_bps)
        best = select(values)
        chosen[best.action.value] = chosen.get(best.action.value, 0) + 1
        if best.net_bps is not None:
            per_action.setdefault("ORACLE_PICK_AMONG_TRADEABLE", []).append(best.net_bps)

    arms = []
    for name, values in sorted(per_action.items()):
        array = np.asarray(values, dtype=float)
        arms.append({
            "action": name, "n": int(len(array)),
            "mean_bps": float(array.mean()),
            "median_bps": float(np.median(array)),
            "p90_bps": float(np.quantile(array, 0.90)),
            "share_positive": float((array > 0).mean()),
        })
    return {"horizon_m": horizon_m, "windows": int(len(starts)),
            "arms": arms, "selected": chosen}


def selftest() -> int:
    """A synthetic saw-tooth with a known answer at every arm."""
    import pandas as pd

    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    # 30 bars. Price rises 1% inside each 15m window then returns to where it started, so the
    # HOLD arms must net exactly minus the round trip while the ceiling is strongly positive.
    close, high, low = [], [], []
    for index in range(31):
        base = 100.0
        close.append(base)
        high.append(base * (1.01 if index % 15 == 8 else 1.0))
        low.append(base * (0.99 if index % 15 == 8 else 1.0))
    frame = pd.DataFrame({"ts_ms": range(31), "close": close, "high": high, "low": low})

    result = evaluate_horizon(frame, 15, 12.0)
    arms = {arm["action"]: arm for arm in result["arms"]}
    check(result["windows"] == 2, "31 bars at a 15m stride give 2 DISJOINT windows, not 16")
    check(abs(arms["LONG_HOLD"]["mean_bps"] + 12.0) < 1e-6,
          "a flat close nets exactly minus the round trip, so the cost is really charged")
    check(abs(arms["SHORT_HOLD"]["mean_bps"] + 12.0) < 1e-6,
          "the short pays the same round trip on the same flat close")
    check(arms["ORACLE_BEST_EXIT_LONG"]["mean_bps"] > 80.0,
          "the ceiling captures the 1% intrawindow spike the close hides")
    check("ORACLE_BEST_EXIT_LONG" not in result["selected"],
          "the hindsight arm is never counted as a selected action")
    check(result["selected"].get("WAIT") == 2,
          "when every tradeable arm loses, WAIT is selected in every window")
    check(abs(arms["ORACLE_PICK_AMONG_TRADEABLE"]["mean_bps"]) < 1e-9,
          "the best TRADEABLE pick is WAIT at 0.0, not the untradeable ceiling")

    print(f"\nBINANCE BUILDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 100)
    print("BINANCE ACTION VALUES - disjoint windows on the 1-minute archive, after costs")
    print("=" * 100)
    if args.selftest:
        return selftest()
    if not BARS.is_file():
        print(f"  BLOCKED: {BARS.name} is missing.")
        return 0

    cost = round_trip_bps()
    frame = load_bars()
    span = (datetime.fromtimestamp(frame['ts_ms'].iloc[0] / 1000, timezone.utc).date(),
            datetime.fromtimestamp(frame['ts_ms'].iloc[-1] / 1000, timezone.utc).date())
    print(f"  bars {len(frame):,}  {span[0]} -> {span[1]}   round trip {cost:.1f} bps")

    summary = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "round_trip_bps": cost, "bars": int(len(frame)),
               "horizons": []}
    for horizon in HORIZONS_M:
        result = evaluate_horizon(frame, horizon, cost)
        summary["horizons"].append(result)
        arms = {arm["action"]: arm for arm in result["arms"]}
        print()
        print(f"  --- {horizon}m   {result['windows']:,} disjoint windows " + "-" * 40)
        print(f"{'action':<30}{'mean bps':>11}{'median':>10}{'p90':>10}{'win%':>8}")
        for name in ("LONG_HOLD", "SHORT_HOLD", "ORACLE_BEST_EXIT_LONG",
                     "ORACLE_BEST_EXIT_SHORT", "ORACLE_PICK_AMONG_TRADEABLE", "WAIT"):
            arm = arms.get(name)
            if not arm:
                continue
            mark = "*" if name.startswith("ORACLE") else " "
            print(f"{mark}{name:<29}{arm['mean_bps']:>11.2f}{arm['median_bps']:>10.2f}"
                  f"{arm['p90_bps']:>10.2f}{arm['share_positive']:>8.1%}")
        tradeable = [arms[n] for n in ("LONG_HOLD", "SHORT_HOLD") if n in arms]
        best = max(tradeable, key=lambda a: a["mean_bps"]) if tradeable else None
        ceiling = arms.get("ORACLE_BEST_EXIT_LONG")
        if best and ceiling:
            print(f"   best fixed rule {best['mean_bps']:+.2f} bps ({best['action']}) | "
                  f"long ceiling {ceiling['mean_bps']:+.2f} bps | WAIT +0.00")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print()
    print("  * = requires hindsight. A bound on what a head could win, never a strategy.")
    print(f"  wrote {OUTPUT.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
