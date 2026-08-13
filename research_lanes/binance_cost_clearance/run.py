"""BINANCE_COST_CLEARANCE_V1 — does BTC move far enough, often enough, to pay for a trade?

THE QUESTION
    Not "will BTC go up". A directional model can be right and still lose money if the move
    it predicts is smaller than the round trip. This lane measures the move distribution
    itself and derives the directional accuracy required for positive EV at each horizon.

    It trains nothing. Everything below is an empirical property of the price series, so it
    cannot be overfitted — and it bounds every model that trades this instrument.

WHY THIS RUNS FIRST
    It is the cheapest test that can falsify the premise. If the median move at a horizon is
    below the round-trip cost, no classifier at that horizon earns money, and knowing that
    before building one saves the effort.

    python research_lanes/binance_cost_clearance/run.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LANE = Path(__file__).resolve().parent
sys.path.insert(0, str(LANE.parent))
from common.scorecard import (  # noqa: E402
    breakeven_accuracy, day_block_bootstrap, day_index, ev_bps,
    forward_abs_move_bps, forward_signed_move_bps, md_table,
)

REPO = LANE.parent.parent
MATRIX = REPO / "data" / "research_matrix_1m.parquet"

#: Horizons in 1-minute bars. Deliberately spans well beyond the app's 5m/15m, because
#: "is 5m even the right horizon" is part of the question.
HORIZONS = (1, 2, 3, 5, 10, 15, 30, 60, 120)

#: Round-trip costs in bps. 12 is StrategyBase.assumed_round_trip_bps; the rest bracket it so
#: the answer is visible as a function of execution quality rather than one assumption.
COSTS = (2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0)
SHIPPED_COST = 12.0


def main() -> int:
    if not MATRIX.exists():
        print(f"missing {MATRIX}")
        return 1
    mf = json.loads((REPO / "data" / "research_matrix_1m.manifest.json").read_text())
    df = pd.read_parquet(MATRIX, columns=["ts_ms", "close"])
    ts = df["ts_ms"].to_numpy("int64")
    close = df["close"].to_numpy(float)
    days = day_index(ts)
    span_d = (ts.max() - ts.min()) / 86_400_000

    print(f"matrix requested_days={mf.get('requested_days')}  rows={len(df):,}  "
          f"span={span_d:.0f}d  days={len(np.unique(days)):,}")

    rows, ev_rows = [], []
    for h in HORIZONS:
        a = forward_abs_move_bps(close, h)
        s = forward_signed_move_bps(close, h)
        ok = np.isfinite(a)
        boot = day_block_bootstrap(a[ok], days[ok], np.mean, n_boot=500)
        med = float(np.nanmedian(a))
        p_be = breakeven_accuracy(boot["lcb"], SHIPPED_COST)   # LCB = conservative
        up = float((s[ok] > 0).mean())
        rows.append({
            "h": f"{h}m", "median": f"{med:.1f}", "mean": f"{boot['point']:.1f}",
            "mean LCB": f"{boot['lcb']:.1f}",
            "P(|move|>12bps)": f"{float((a[ok] > SHIPPED_COST).mean()):.1%}",
            "break-even acc": f"{p_be:.1%}" if p_be <= 1 else "IMPOSSIBLE",
            "base rate up": f"{up:.1%}",
        })
        for c in COSTS:
            ev_rows.append({"h": h, "cost": c,
                            "clear": float((a[ok] > c).mean()),
                            "be": breakeven_accuracy(boot["lcb"], c)})

    print("\n" + md_table(rows, ["h", "median", "mean", "mean LCB", "P(|move|>12bps)",
                                 "break-even acc", "base rate up"]))

    # Break-even accuracy surface: the number that decides whether a horizon is worth modelling.
    print("\nDirectional accuracy required for EV>0 (using the day-bootstrap LOWER bound on "
          "mean |move|):")
    grid = [{"h": f"{h}m", **{f"{c:.0f}bps": (
        f"{next(r['be'] for r in ev_rows if r['h'] == h and r['cost'] == c):.1%}"
        if next(r["be"] for r in ev_rows if r["h"] == h and r["cost"] == c) <= 1.0
        else "impossible")
        for c in COSTS}} for h in HORIZONS]
    print(md_table(grid, ["h"] + [f"{c:.0f}bps" for c in COSTS]))

    # What the app's own reported accuracy would earn at each horizon.
    print(f"\nEV per trade at the shipped {SHIPPED_COST:.0f}bps round trip, by assumed accuracy:")
    accs = (0.52, 0.55, 0.58, 0.60, 0.65, 0.70)
    ev_grid = []
    for h in HORIZONS:
        a = forward_abs_move_bps(close, h)
        ok = np.isfinite(a)
        lcb = day_block_bootstrap(a[ok], days[ok], np.mean, n_boot=300)["lcb"]
        ev_grid.append({"h": f"{h}m",
                        **{f"{p:.0%}": f"{ev_bps(p, lcb, SHIPPED_COST):+.1f}" for p in accs}})
    print(md_table(ev_grid, ["h"] + [f"{p:.0%}" for p in accs]))

    out = {"matrix_days": mf.get("requested_days"), "rows": int(len(df)),
           "span_days": round(span_d, 1), "shipped_cost_bps": SHIPPED_COST,
           "by_horizon": rows}
    (LANE / "results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {LANE / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
