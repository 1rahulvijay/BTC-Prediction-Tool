"""
probe_path_shape.py - the HONEST version of "up then down": round-trip + path style.
====================================================================================
"Starts up then down" = (a) the SEQUENCE [coin-flip, direction-coupled] + (b) the SET of
levels touched + (c) the path STYLE. (b) and (c) are different questions -- test if predictable:

  * ROUND-TRIP: P(touch BOTH +X and -X within the window). The honest "up AND down" -- tells you
    the window visits both sides (catch either touch on early exit), without claiming the order.
  * PATH STYLE: trend_efficiency = |net move| / range. Low = CHOP (round-trip, fade extremes),
    high = clean TREND (one-way, exit at the extreme). A volatility-structure question.
  * |net move| magnitude: is the directional displacement SIZE (not sign) predictable?

All from the parity-proven keepers (rv_15m.. shock_magnitude), walk-forward, leak-free
(targets strictly inside the future window). If a target clears its null robustly it can join
the trade plan. ASCII output.

Usage:
  python backend/research/standalone/probe_path_shape.py
  python backend/research/standalone/probe_path_shape.py --selftest
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(TA.ROOT, "data", "research_matrix_1m.parquet")
FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _fut(df, w):
    fc = df["close"].shift(-w)
    fhi = df["high"].rolling(w).max().shift(-w)
    flo = df["low"].rolling(w).min().shift(-w)
    return fc, fhi, flo


def run():
    df = pd.read_parquet(MATRIX)
    for w in (5, 15):
        c = df["close"]
        fc, fhi, flo = _fut(df, w)
        px = float(c.median()); b50 = 50 / px * 1e4; b30 = 30 / px * 1e4
        rng = (fhi - flo) / c * 1e4
        net = (fc - c) / c * 1e4
        up = (fhi / c - 1) * 1e4; dn = (flo / c - 1) * 1e4
        eff = np.abs(net) / (rng + 1e-9)                         # trend efficiency (0=chop,1=clean trend)
        rt50 = ((up >= b50) & (-dn >= b50)).astype(float)        # round-trip both +/-$50
        rt_asym = ((up >= b50) & (-dn >= b30)).astype(float)     # touch +$50 AND -$30
        print("\n" + "=" * 84)
        print(f"BTC {w}m  -  PATH SHAPE (round-trip + style), walk-forward from keepers")
        print(f"  base rates: round-trip+/-$50={rt50.mean():.2f}  touch+$50&-$30={rt_asym.mean():.2f}  "
              f"median trend_eff={np.nanmedian(eff):.2f}")
        # classification targets
        for name, y in (("round-trip +/-$50", rt50), ("touch +$50 & -$30", rt_asym)):
            d = pd.concat([df[FEATURES], pd.Series(y, name="y")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
            agg = TA.wf_clf(d[FEATURES], d["y"]); nm, ns = TA.shuffle_null_clf(d[FEATURES], d["y"])
            base = float(max(d["y"].mean(), 1 - d["y"].mean()))
            v = "PREDICTABLE" if agg["auc"] >= 0.55 and agg["above_half"] >= agg["n_folds"] - 1 else "weak/coin-flip"
            print(f"    {name:<20} AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} (null {nm:.3f}, base {base:.2f}) -> {v}")
        # regression targets: trend efficiency + |net move| magnitude, skill vs unconditional mean
        for name, y in (("trend_efficiency", eff), ("|net move| bps", np.abs(net))):
            d = pd.concat([df[FEATURES], pd.Series(y, name="y")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
            base = pd.Series(d["y"].expanding().mean().shift(1).fillna(d["y"].mean()).values, index=d.index)
            agg = TA.wf_reg(d[FEATURES], d["y"], base)
            v = "PREDICTABLE" if agg["skill"] > 0.02 else "no lift (random)"
            print(f"    {name:<20} skill {agg['skill']:+.3f}  R^2 {agg['r2']:+.3f} -> {v}")
    print("\nREAD: round-trip PREDICTABLE => add P(visits both sides) to the plan (the honest 'up & down'). "
          "trend_efficiency PREDICTABLE => add a CHOP-vs-TREND style tag (chop=fade extremes / both exits; "
          "trend=ride to the extreme). |net move| is the coin-flip's magnitude, expected weak.")


def selftest():
    df = pd.DataFrame({"close": 100 + np.arange(2000) * 0.0 + np.random.default_rng(0).normal(0, 1, 2000)})
    df["high"] = df["close"] + 1; df["low"] = df["close"] - 1
    fc, fhi, flo = _fut(df, 5)
    ok = fc.notna().sum() > 1000 and fhi.notna().sum() > 1000
    print(f"selftest: future cols computed = {ok}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    run()


if __name__ == "__main__":
    main()
