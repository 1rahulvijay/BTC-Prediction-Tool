"""Four horizons, seven pairs, two exchanges, seven model families and voting. Scored once.

PROTOCOL
    docs/active/PREREG_MULTIHORIZON_DIRECTION_V1.md, sha256 2fb2a481..., frozen before any
    analysis of this dataset.

WHY LONGER HORIZONS COULD SUCCEED WHERE 15 MINUTES FAILED
    Cost does not scale with horizon. A 600-minute trade pays the same 14 bps round trip as a
    60-minute one, so a signal an order of magnitude weaker per unit time can still clear it if
    the move is large enough. That is the entire hypothesis being tested.

MULTIPLICITY
    Four horizons are tested, so the primary interval is 98.75%, not 95% - Bonferroni at
    0.05/4. Reporting the best of four horizons at 95% would be a search over horizons.

    python -m research.multihorizon.run --selftest
    python -m research.multihorizon.run
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from direction_ensemble_v1 import build_heads, null_floor                 # noqa: E402
from tradability_head_v1 import auc                                       # noqa: E402

from .features import FEATURES, HORIZON_BARS, HORIZON_MINUTES, build      # noqa: E402
from .fetch import load                                                   # noqa: E402

PROTOCOL = "PREREG_MULTIHORIZON_DIRECTION_V1.md"
COST_BPS = 14.0
TRAIN_DAYS = 30
CI_PERCENTILES = (0.625, 99.375)      # 98.75% = Bonferroni 0.05/4
BONFERRONI_N = 4


def day_block_ci(values: np.ndarray, days: np.ndarray, iterations: int = 1500,
                 seed: int = 131, percentiles=CI_PERCENTILES) -> tuple:
    unique = np.unique(days)
    if len(unique) < 2 or len(values) == 0:
        return (float("nan"), float("nan"))
    groups = [values[days == d] for d in unique]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in pick]).mean()
    return (float(np.percentile(means, percentiles[0])),
            float(np.percentile(means, percentiles[1])))


def non_overlapping_mask(frame: pd.DataFrame, bars: int) -> np.ndarray:
    """One trade per symbol per horizon-length. Overlapping windows are the same move."""
    keep = np.zeros(len(frame), dtype=bool)
    for symbol in frame["symbol"].unique():
        rows = np.flatnonzero((frame["symbol"] == symbol).to_numpy())
        last = -10**9
        for i in rows:
            if i - last > bars:
                keep[i] = True
                last = i
    return keep


def walk_forward(frame: pd.DataFrame, horizon: int) -> dict:
    """Train on each 30-day block, predict the next, purged by one full horizon."""
    bars = HORIZON_BARS[horizon]
    label_column = f"fwd_ret_bps_{horizon}"
    usable = frame.dropna(subset=list(FEATURES) + [label_column]).reset_index(drop=True)
    if usable.empty:
        return {}
    days = usable["day"].to_numpy()
    start, end = days.min(), days.max()
    blocks = list(range(start + TRAIN_DAYS, end + 1, TRAIN_DAYS))

    stacked_probs, stacked_labels, stacked_days, stacked_rows = [], [], [], []
    per_head = {name: [] for name in build_heads()}
    for block_start in blocks:
        train = usable[days < block_start - 0]
        test = usable[(days >= block_start) & (days < block_start + TRAIN_DAYS)]
        # Purge: drop training rows whose label window reaches into the test block.
        purge_days = max(1, int(np.ceil(bars * 15 / 1440)))
        train = train[train["day"] < block_start - purge_days]
        if len(train) < 5000 or len(test) < 500:
            continue
        Xtr = train[list(FEATURES)].to_numpy(float)
        ytr = (train[label_column].to_numpy(float) > 0).astype(int)
        Xte = test[list(FEATURES)].to_numpy(float)
        if len(np.unique(ytr)) < 2:
            continue
        block_probs = []
        for name, model in build_heads().items():
            try:
                model.fit(Xtr, ytr)
                p = model.predict_proba(Xte)[:, 1]
            except Exception:
                p = np.full(len(Xte), 0.5)
            block_probs.append(p)
            per_head[name].append((p, (test[label_column].to_numpy(float) > 0).astype(int)))
        stacked_probs.append(np.vstack(block_probs).mean(axis=0))
        stacked_labels.append((test[label_column].to_numpy(float) > 0).astype(int))
        stacked_days.append(test["day"].to_numpy())
        stacked_rows.append(test)

    if not stacked_probs:
        return {}
    soft = np.concatenate(stacked_probs)
    labels = np.concatenate(stacked_labels)
    day_index = np.concatenate(stacked_days)
    rows = pd.concat(stacked_rows, ignore_index=True)

    head_auc = {}
    for name, parts in per_head.items():
        if parts:
            p = np.concatenate([x for x, _ in parts])
            y = np.concatenate([y for _, y in parts])
            head_auc[name] = auc(p, y)

    keep = non_overlapping_mask(rows, bars)
    sides = np.where(soft[keep] >= 0.5, 1, -1)
    net = sides * rows[label_column].to_numpy(float)[keep] - COST_BPS
    net_ci = day_block_ci(net, day_index[keep])
    floor = null_floor(soft, labels, day_index, replications=200)

    per_pair = {}
    symbols = rows["symbol"].to_numpy()[keep]
    for symbol in np.unique(symbols):
        sub = net[symbols == symbol]
        per_pair[symbol] = (len(sub), float(sub.mean()))

    return {"horizon": horizon, "soft_auc": auc(soft, labels), "head_auc": head_auc,
            "floor": floor, "n": int(keep.sum()), "net": float(net.mean()),
            "net_ci": net_ci, "per_pair": per_pair, "blocks": len(stacked_probs)}


def verdict_for(result: dict) -> tuple[str, str]:
    if not result:
        return "HORIZON_NO_DATA", "no usable walk-forward block"
    lo, hi = result["net_ci"]
    floor_hi = result["floor"][2]
    if np.isfinite(lo) and lo > 0:
        return ("HORIZON_PROFITABLE",
                f"net {result['net']:+.2f} bps, 98.75% CI lower bound {lo:+.2f} > 0")
    if np.isfinite(floor_hi) and result["soft_auc"] > floor_hi:
        return ("HORIZON_SIGNAL_ONLY",
                f"AUC {result['soft_auc']:.4f} above the null floor {floor_hi:.4f}, but net CI "
                f"[{lo:+.2f}, {hi:+.2f}] does not clear zero")
    return ("HORIZON_NO_SIGNAL",
            f"AUC {result['soft_auc']:.4f} is inside the null floor (upper {floor_hi:.4f})")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(CI_PERCENTILES == (0.625, 99.375),
          "the primary interval is 98.75% - Bonferroni across four horizons")
    check(BONFERRONI_N == len(HORIZON_MINUTES), "the correction matches the horizon count")
    check(COST_BPS == 14.0, "cost is charged identically at every horizon")

    frame = pd.DataFrame({"symbol": ["A"] * 10 + ["B"] * 10,
                          "ts_ms": list(range(10)) * 2})
    keep = non_overlapping_mask(frame, 4)
    # 10 bars per symbol, 4-bar horizon: entries at 0 and 5, so two per symbol.
    check(keep.sum() == 4, "each symbol contributes its own non-overlapping trades")
    check(bool(keep[0]) and bool(keep[10]),
          "...and every symbol's first bar is eligible independently")
    check(bool(keep[5]) and not bool(keep[4]),
          "the next entry waits a full horizon - overlapping windows are one move")

    rng = np.random.default_rng(0)
    values = rng.normal(1.0, 1.0, 4000)
    days = np.repeat(np.arange(40), 100)
    lo95, hi95 = day_block_ci(values, days, percentiles=(2.5, 97.5))
    lo99, hi99 = day_block_ci(values, days)
    check(lo99 < lo95 and hi99 > hi95,
          "the Bonferroni interval is STRICTLY WIDER than a 95% interval")

    check(verdict_for({})[0] == "HORIZON_NO_DATA", "an empty result is not a pass")
    good = {"net_ci": (1.0, 3.0), "net": 2.0, "floor": (0.49, 0.50, 0.505), "soft_auc": 0.52}
    check(verdict_for(good)[0] == "HORIZON_PROFITABLE", "a positive lower bound passes")
    signal = {"net_ci": (-2.0, 3.0), "net": 0.5, "floor": (0.49, 0.50, 0.505),
              "soft_auc": 0.52}
    check(verdict_for(signal)[0] == "HORIZON_SIGNAL_ONLY",
          "AUC above floor without post-cost value is SIGNAL_ONLY")
    none = {"net_ci": (-2.0, 3.0), "net": 0.5, "floor": (0.49, 0.50, 0.52), "soft_auc": 0.505}
    check(verdict_for(none)[0] == "HORIZON_NO_SIGNAL", "AUC inside the floor is NO_SIGNAL")

    print(f"\nMULTIHORIZON RUN SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    frame = build(load())
    print("=" * 104)
    print(f"MULTIHORIZON DIRECTION V1 - protocol {PROTOCOL} (frozen before analysis)")
    print("=" * 104)
    import datetime as dt
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print(f"  {len(frame):,} rows, {frame.symbol.nunique()} pairs, "
          f"{fmt(frame.ts_ms.min())} -> {fmt(frame.ts_ms.max())}")
    print(f"  {len(FEATURES)} features   cost {COST_BPS:.0f} bps at EVERY horizon   "
          f"walk-forward {TRAIN_DAYS}d blocks   primary CI 98.75% (Bonferroni /4)")

    results = []
    for horizon in HORIZON_MINUTES:
        result = walk_forward(frame, horizon)
        results.append(result)
        verdict, reason = verdict_for(result)
        print()
        if not result:
            print(f"  --- {horizon:>3}m: {verdict} - {reason}")
            continue
        print(f"  --- {horizon:>3}m horizon   {result['blocks']} walk-forward blocks   "
              f"{result['n']:,} non-overlapping trades")
        heads = "  ".join(f"{k[:4]} {v:.4f}" for k, v in result["head_auc"].items())
        print(f"      heads: {heads}")
        print(f"      SOFT_VOTE AUC {result['soft_auc']:.4f}   "
              f"null floor 95% [{result['floor'][0]:.4f}, {result['floor'][2]:.4f}]")
        print(f"      net {result['net']:+.2f} bps   98.75% CI "
              f"[{result['net_ci'][0]:+.2f}, {result['net_ci'][1]:+.2f}]   "
              f"implied gross {result['net'] + COST_BPS:+.2f} bps")
        pairs = "  ".join(f"{s[:3]} {m:+.1f}" for s, (n, m) in
                          sorted(result["per_pair"].items()))
        print(f"      per pair: {pairs}")
        print(f"      VERDICT: {verdict} - {reason}")

    profitable = [r for r in results if r and verdict_for(r)[0] == "HORIZON_PROFITABLE"]
    print()
    print("=" * 104)
    if profitable:
        print(f"  MULTIHORIZON_PROFITABLE: {len(profitable)} horizon(s) cleared costs at the "
              f"Bonferroni-corrected interval.")
    else:
        print("  MULTIHORIZON_NO_TRADABLE_EDGE: no horizon cleared costs.")
        print("  Per the kill rule, direction trading on this information set is closed across")
        print("  all tested horizons. The remaining hypothesis is order-book microstructure,")
        print("  which this archive does not contain.")
    print("  Refinement after seeing these numbers requires a NEW preregistration - adjusting")
    print("  anything now and re-reporting converts a frozen test into a search.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
