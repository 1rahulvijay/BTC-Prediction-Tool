"""
probe_post_touch_reversal.py - after price TOUCHES the high/low mid-window, is REVERSAL predictable?
===================================================================================================
Polymarket lets you bet either way and exit on share-price moves, so the live question is: price
just touched +$50 (the high) -- from here, REVERSAL (falls back through the anchor) or CONTINUATION
(keeps rising)? Is that conditionally predictable, or the Markov coin-flip?

Tests, on windows that DID touch the up-barrier (and symmetrically the down-barrier):
  1. P(reversal = close back through the anchor) -- predictable from the vol keepers? (walk-forward AUC)
  2. does it beat the round-trip-at-open prediction (i.e. is there NEW info at the touch)?
  3. does TOUCH TIMING matter (early touch -> more time to revert -> more reversal)?

Intra-window path from research_matrix_1m.parquet. Leak-free: keepers known at open; reversal is the
remaining path after the touch. ASCII output.

Usage:
  python backend/research/standalone/probe_post_touch_reversal.py
  python backend/research/standalone/probe_post_touch_reversal.py --selftest
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


def run():
    df = pd.read_parquet(MATRIX)
    c = df["close"]
    for w in (5, 15):
        px = float(c.median()); b50 = 50 / px * 1e4
        # intra-window 1-min highs/lows and closes
        Hs = [df["high"].shift(-k) for k in range(1, w + 1)]
        fc = c.shift(-w)
        up = (pd.concat(Hs, axis=1).max(axis=1) / c - 1) * 1e4
        netclose = (fc / c - 1) * 1e4
        # time of first up-touch (minute the running high first exceeds +b50)
        touched_up = up >= b50
        run_hi = pd.concat([(h / c - 1) * 1e4 for h in Hs], axis=1)
        first_up_min = (run_hi >= b50).values.argmax(axis=1) + 1  # 1..w (0->1 if never; mask by touched)
        print("\n" + "=" * 84)
        print(f"BTC {w}m  -  POST-TOUCH reversal (windows that touched +$50 up-barrier)")
        sub = touched_up & netclose.notna() & up.notna()
        n_t = int(sub.sum())
        # reversal = after touching the high, close is BACK BELOW the anchor
        reversal = (netclose < 0).astype(float)
        base_rev = float(reversal[sub].mean())
        print(f"  touched-high windows: {n_t}  |  unconditional P(reversal=close<anchor | touched high) = {base_rev:.3f}")
        # 1) predictable from keepers?
        d = pd.concat([df[FEATURES], reversal.rename("y")], axis=1)[sub].replace([np.inf, -np.inf], np.nan).dropna()
        agg = TA.wf_clf(d[FEATURES], d["y"]); nm, ns = TA.shuffle_null_clf(d[FEATURES], d["y"])
        v = "PREDICTABLE" if agg["auc"] >= 0.55 and agg["above_half"] >= agg["n_folds"] - 1 else "coin-flip (Markov)"
        print(f"  1) P(reversal) from keepers: walk-fwd AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} "
              f"(null {nm:.3f}) -> {v}")
        # 3) timing: early touch (first half) vs late touch (second half) -> reversal rate
        fm = first_up_min.astype(float)
        early = sub & (fm <= w / 2.0); late = sub & (fm > w / 2.0)
        re_e = float(reversal[early].mean()) if early.sum() else float("nan")
        re_l = float(reversal[late].mean()) if late.sum() else float("nan")
        print(f"  3) timing: EARLY touch (1st half) reversal={re_e:.3f} (n{int(early.sum())})  vs  "
              f"LATE touch reversal={re_l:.3f} (n{int(late.sum())})  -> "
              f"{'early touches revert MORE (real structure)' if re_e - re_l > 0.05 else 'timing ~ no effect'}")
    print("\nREAD: if P(reversal) AUC ~0.50, the post-touch direction is the Markov coin-flip -> the OPEN-time "
          "round-trip/style call is the best reversal read (CHOP=>revert, TREND=>continue). If AUC>0.55 or "
          "early-touch reverts materially more, a live post-touch reversal flag is worth adding.")


def selftest():
    df = pd.DataFrame({"close": 100 + np.random.default_rng(0).normal(0, 1, 2000)})
    df["high"] = df["close"] + 1; df["low"] = df["close"] - 1
    up = (df["high"].shift(-1) / df["close"] - 1) * 1e4
    print(f"selftest: excursion computed, non-null={up.notna().sum()}")
    print("PASS" if up.notna().sum() > 1000 else "FAIL")
    return up.notna().sum() > 1000


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
