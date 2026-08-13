"""WAIT_VS_BUY_V1 and POLY_SETTLEMENT_CONVEXITY_V1 — both from PM data already on disk.

WAIT_VS_BUY_V1
    Model says buy. Should you cross the ask NOW, or wait? Measures the best executable ask
    reachable within the next N seconds of the SAME round, against crossing immediately.
    Waiting is not free: the ask can rise, and the round can end.

POLY_SETTLEMENT_CONVEXITY_V1
    dP/dBTC is the contract's delta; how that delta grows as settlement nears and the anchor
    is close is its gamma. High gamma plus a stale quote is where a small BTC move should
    reprice the contract most.

    python research_lanes/run_batch3.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LANES = Path(__file__).resolve().parent
sys.path.insert(0, str(LANES))
from common.pm_data import load_official, round_bootstrap  # noqa: E402


TAKER_COEF = 0.07


def _fee(price: np.ndarray) -> np.ndarray:
    price = np.asarray(price, float)
    return TAKER_COEF * price * (1.0 - price)


def _future_value_by_round(frame: pd.DataFrame, column: str, horizon_s: int) -> np.ndarray:
    """Return the first observed value at or after t+horizon, inside the same round."""
    result = np.full(len(frame), np.nan, dtype=float)
    for positions in frame.groupby("round_id", sort=False).indices.values():
        positions = np.asarray(positions, dtype=int)
        ts = frame.loc[positions, "ts"].to_numpy(float)
        values = frame.loc[positions, column].to_numpy(float)
        targets = np.searchsorted(ts, ts + horizon_s, side="left")
        valid = targets < len(ts)
        result[positions[valid]] = values[targets[valid]]
    return result


def _clustered_slope_samples(
    x: np.ndarray,
    y: np.ndarray,
    rounds: np.ndarray,
    *,
    n_boot: int = 4_000,
    seed: int = 0,
) -> tuple[float, np.ndarray, int]:
    """Bootstrap an OLS slope by whole rounds using sufficient statistics."""
    frame = pd.DataFrame({"x": np.asarray(x, float), "y": np.asarray(y, float), "r": rounds})
    frame = frame[np.isfinite(frame["x"]) & np.isfinite(frame["y"])]
    frame["xx"] = frame["x"] * frame["x"]
    frame["xy"] = frame["x"] * frame["y"]
    grouped = frame.groupby("r", sort=False).agg(
        n=("x", "size"), sx=("x", "sum"), sy=("y", "sum"),
        sxx=("xx", "sum"), sxy=("xy", "sum"),
    )
    stats = grouped[["n", "sx", "sy", "sxx", "sxy"]].to_numpy(float)

    def slope(total: np.ndarray) -> np.ndarray:
        n, sx, sy, sxx, sxy = (total[..., i] for i in range(5))
        denominator = sxx - sx * sx / n
        return np.divide(
            sxy - sx * sy / n,
            denominator,
            out=np.full_like(denominator, np.nan, dtype=float),
            where=np.abs(denominator) > 1e-12,
        )

    point = float(slope(stats.sum(axis=0)))
    rng = np.random.default_rng(seed)
    estimates = np.empty(n_boot, dtype=float)
    n_rounds = len(stats)
    chunk = 500
    for start in range(0, n_boot, chunk):
        stop = min(start + chunk, n_boot)
        chosen = rng.integers(0, n_rounds, size=(stop - start, n_rounds))
        estimates[start:stop] = slope(stats[chosen].sum(axis=1))
    return point, estimates[np.isfinite(estimates)], n_rounds


def wait_vs_buy(d) -> dict:
    """Report both a hindsight-minimum bound and a causal fixed-delay policy."""
    x = d.sort_values(["round_id", "ts"]).reset_index(drop=True).copy()
    out = []
    for horizon_s in (10, 30, 60):
        best, rose, ended = [], [], []
        for _, g in x.groupby("round_id", sort=False):
            ts = g["ts"].to_numpy(float)
            ask = g["up_ask"].to_numpy(float)
            for i in range(len(g)):
                w = (ts > ts[i]) & (ts <= ts[i] + horizon_s)
                if not w.any():
                    ended.append(1.0); continue
                ended.append(0.0)
                fut = ask[w].min()
                best.append(ask[i] - fut)          # >0 means waiting got a better price
                rose.append(1.0 if ask[w].min() > ask[i] else 0.0)
        arr = np.array(best, float)
        delayed = _future_value_by_round(x, "up_ask", horizon_s)
        valid = np.isfinite(delayed)
        immediate_net = x["settled_up"].to_numpy(float) - x["up_ask"].to_numpy(float) - _fee(
            x["up_ask"].to_numpy(float)
        )
        # A causal wait policy cannot silently drop states whose round ends before t+horizon.
        # It executes at the first quote at/after the delay when one exists; otherwise it skips
        # and earns zero. Excluding no-quote states would condition on future availability and
        # make waiting look better than it was.
        delayed_net = np.zeros(len(x), dtype=float)
        delayed_net[valid] = x.loc[valid, "settled_up"].to_numpy(float) - delayed[valid] - _fee(
            delayed[valid]
        )
        delta = delayed_net - immediate_net
        fixed = round_bootstrap(
            delta,
            x["round_id"].to_numpy(),
            np.mean,
            n_boot=2_000,
            seed=horizon_s,
        )
        out.append({"horizon_s": horizon_s, "n": int(len(arr)),
                    "mean_improvement": float(arr.mean()),
                    "median_improvement": float(np.median(arr)),
                    "pct_improved": float((arr > 0).mean()),
                    "pct_ask_rose": float(np.mean(rose)),
                    "pct_no_future_quote": float(np.mean(ended)),
                    "fixed_delay_candidates": int(len(x)),
                    "fixed_delay_n": int(valid.sum()),
                    "fixed_delay_net_delta": fixed["point"],
                    "fixed_delay_lcb": fixed["lcb"],
                    "fixed_delay_ucb": fixed["ucb"]})
    return {"by_horizon": out}


def settlement_convexity(d) -> dict:
    """Delta = dP_poly / dBTC(bps), by time remaining and distance from anchor."""
    x = d.sort_values(["round_id", "ts"]).copy()
    x["mid"] = (x["up_bid"] + x["up_ask"]) / 2.0
    x["d_mid"] = x.groupby("round_id")["mid"].diff()
    x["d_btc"] = x.groupby("round_id")["btc_price"].diff()
    x["d_btc_bps"] = x["d_btc"] / x["btc_price"] * 1e4
    m = x["d_btc_bps"].abs().between(0.5, 200) & x["d_mid"].notna()
    x = x[m]
    x["b_time"] = pd.cut(x["seconds_left"], [0, 60, 120, 300, 600, 1e9],
                         labels=["<60s", "60-120s", "2-5m", "5-10m", ">10m"])
    x["b_dist"] = pd.cut(x["distance_bps"].abs(), [0, 3, 8, 20, 1e9],
                         labels=["0-3bps", "3-8bps", "8-20bps", ">20bps"])
    candidates = [
        (key, sub.copy())
        for key, sub in x.groupby(["b_time", "b_dist"], observed=True)
        if len(sub) >= 300
    ]
    family_size = len(candidates)
    rows = []
    for position, (key, sub) in enumerate(candidates):
        slope, estimates, n_rounds = _clustered_slope_samples(
            sub["d_btc_bps"].to_numpy(float),
            sub["d_mid"].to_numpy(float),
            sub["round_id"].to_numpy(),
            seed=position,
        )
        adjusted_alpha = 0.05 / max(1, family_size)
        rows.append({"cell": " | ".join(map(str, key)), "n": len(sub),
                     "n_rounds": int(n_rounds),
                     "delta_cents_per_bp": slope * 100.0,
                     "lcb_cents_per_bp": float(np.quantile(estimates, adjusted_alpha / 2)) * 100,
                     "ucb_cents_per_bp": float(np.quantile(estimates, 1 - adjusted_alpha / 2)) * 100})
    rows.sort(key=lambda r: -abs(r["delta_cents_per_bp"]))
    return {"cells": rows, "multiple_testing": "Bonferroni family-wise alpha=0.05"}


def maker_markout_surface(d) -> dict:
    """Hypothetical bid-fill markout; explicitly not a fill or queue simulation."""
    x = d.sort_values(["round_id", "ts"]).reset_index(drop=True).copy()
    x["b_time"] = pd.cut(
        x["seconds_left"], [0, 60, 120, 300, 1e9],
        labels=["<60s", "60-120s", "2-5m", ">5m"],
    )
    x["b_dist"] = pd.cut(
        x["distance_bps"].abs(), [0, 3, 8, 1e9],
        labels=["0-3bps", "3-8bps", ">8bps"],
        include_lowest=True,
    )
    for horizon_s in (5, 15, 30):
        future_mid = _future_value_by_round(x, "up_mid", horizon_s)
        x[f"markout_{horizon_s}s"] = future_mid - x["up_bid"].to_numpy(float)
    rows = []
    for key, sub in x.groupby(["b_time", "b_dist"], observed=True):
        row = {"cell": " | ".join(map(str, key)), "n_rounds": int(sub["round_id"].nunique())}
        for horizon_s in (5, 15, 30):
            valid = sub[f"markout_{horizon_s}s"].notna()
            values = sub.loc[valid, f"markout_{horizon_s}s"].to_numpy(float)
            row[f"markout_{horizon_s}s_cents"] = float(np.mean(values) * 100) if len(values) else None
            row[f"n_{horizon_s}s"] = int(len(values))
            row[f"n_rounds_{horizon_s}s"] = int(sub.loc[valid, "round_id"].nunique())
        rows.append(row)
    rows.sort(
        key=lambda row: -(row["markout_30s_cents"] if row["markout_30s_cents"] is not None else -1e9)
    )
    return {
        "status": "PARTIAL_DATA_BLOCKED",
        "fill_observed": False,
        "rows": rows,
        "note": (
            "Conditional markout if every resting UP bid were filled. Actual fill probability, "
            "queue position and fill-conditioned adverse selection are unavailable, so this is "
            "an optimistic diagnostic and not maker PnL."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--output", type=Path, default=LANES / "batch3_results.json")
    args = parser.parse_args()
    load_kwargs = {}
    if args.snapshots:
        load_kwargs["snapshots_path"] = args.snapshots
    if args.settlements:
        load_kwargs["settlements_path"] = args.settlements
    d = load_official(**load_kwargs)
    print(f"rows={len(d):,} rounds={d.round_id.nunique():,} days={d.day.nunique()}\n")

    print("=== WAIT_VS_BUY_V1 ===")
    w = wait_vs_buy(d)
    print(f"{'wait':>6}{'n':>10}{'oracle':>12}{'fixed delay':>14}{'fixed 95% CI':>22}")
    for r in w["by_horizon"]:
        print(f"{r['horizon_s']:>5}s{r['n']:>10,}{r['mean_improvement']:>+12.4f}"
              f"{r['fixed_delay_net_delta']:>+14.4f}"
              f" [{r['fixed_delay_lcb']:+.4f},{r['fixed_delay_ucb']:+.4f}]")

    print("\n=== POLY_SETTLEMENT_CONVEXITY_V1 ===")
    c = settlement_convexity(d)
    print(f"{'cell':<26}{'n':>8}{'rounds':>8}{'delta c/bp':>14}{'family-wise CI':>25}")
    for r in c["cells"][:12]:
        print(f"{r['cell']:<26}{r['n']:>8,}{r['n_rounds']:>8}"
              f"{r['delta_cents_per_bp']:>14.4f}"
              f" [{r['lcb_cents_per_bp']:+.4f},{r['ucb_cents_per_bp']:+.4f}]")

    print("\n=== MAKER_MARKOUT_SURFACE_V1 (HYPOTHETICAL FILLS ONLY) ===")
    maker = maker_markout_surface(d)
    for r in [row for row in maker["rows"] if row["n_30s"] >= 300][:12]:
        print(f"{r['cell']:<20} 5s={r['markout_5s_cents']!s:<9} "
              f"15s={r['markout_15s_cents']!s:<9} 30s={r['markout_30s_cents']!s:<9} "
              f"n30={r['n_30s']:,}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"wait_vs_buy": w, "convexity": c, "maker_markout": maker}, indent=2, default=float)
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
