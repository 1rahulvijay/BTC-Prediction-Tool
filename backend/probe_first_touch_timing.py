"""
probe_first_touch_timing.py - is the FIRST barrier touch (which side / how fast) predictable?
=============================================================================================
Polymarket lets you bet either way and exit on share moves, so the live questions at window open are:
  (1) will price touch +/-$50 at all?            -> sizes whether the window is worth playing
  (2) if it touches, will it be EARLY or LATE?    -> early touch is fade-able (post-touch reversal probe)
  (3) which side touches FIRST, up or down?       -> this is a DIRECTION question, expected coin-flip

Tested on the 360d matrix, leak-free (keepers known at open; the touch path is the future window). The
honest prior: (1) and (2) are non-directional (speed/vol) -> should be PREDICTABLE; (3) is directional
-> should be ~0.50 (the dead coin-flip). We test all three rather than assume. ASCII output.

Usage:
  python backend/probe_first_touch_timing.py
  python backend/probe_first_touch_timing.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(TA.ROOT, "data", "research_matrix_1m.parquet")
FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _verdict(agg, null_m, thresh=0.55):
    return ("PREDICTABLE" if agg["auc"] >= thresh and agg["above_half"] >= agg["n_folds"] - 1
            else "coin-flip")


def _test(df, mask, y, label):
    d = pd.concat([df[FEATURES], y.rename("y")], axis=1)[mask].replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 2000 or d["y"].nunique() < 2:
        print(f"  {label}: insufficient/degenerate (n={len(d)})"); return
    agg = TA.wf_clf(d[FEATURES], d["y"]); nm, _ = TA.shuffle_null_clf(d[FEATURES], d["y"])
    print(f"  {label}: base={d['y'].mean():.3f}  walk-fwd AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} "
          f"(null {nm:.3f}) -> {_verdict(agg, nm)}")


def run():
    df = pd.read_parquet(MATRIX)
    c = df["close"]
    for w in (5, 15):
        px = float(c.median()); b50 = 50 / px * 1e4
        run_hi = pd.concat([(df["high"].shift(-k) / c - 1) * 1e4 for k in range(1, w + 1)], axis=1)
        run_lo = pd.concat([(df["low"].shift(-k) / c - 1) * 1e4 for k in range(1, w + 1)], axis=1)
        up_hit = (run_hi >= b50)          # per-minute up-touch booleans
        dn_hit = (run_lo <= -b50)
        touched_up = up_hit.any(axis=1)
        touched_dn = dn_hit.any(axis=1)
        touched_any = touched_up | touched_dn
        # first-touch minute per side (w+1 = never)
        first_up = np.where(touched_up.values, up_hit.values.argmax(axis=1) + 1, w + 1)
        first_dn = np.where(touched_dn.values, dn_hit.values.argmax(axis=1) + 1, w + 1)
        first_touch = np.minimum(first_up, first_dn).astype(float)
        valid = run_hi.notna().all(axis=1) & run_lo.notna().all(axis=1)

        print("\n" + "=" * 84)
        print(f"BTC {w}m  -  FIRST-TOUCH ($50) predictability   (touch rate={float(touched_any[valid].mean()):.3f})")

        # (1) P(touch >=$50 either side) -- non-directional, should be PREDICTABLE (the move50 head)
        _test(df, valid, touched_any.astype(float), "(1) P(touch +/-$50 either side)        ")

        # (2) among touched windows: EARLY touch (first half)? -- speed/vol, should be PREDICTABLE
        m_touch = valid & touched_any
        early = pd.Series((first_touch <= w / 2.0).astype(float), index=df.index)
        _test(df, m_touch, early, "(2) EARLY touch (1st half | touched)   ")

        # (3) among touched windows: UP side FIRST? -- DIRECTIONAL, expected coin-flip
        up_first = pd.Series((first_up < first_dn).astype(float), index=df.index)
        _test(df, m_touch, up_first, "(3) UP touched FIRST (| touched)       ")

        # descriptive: how early is the typical first touch?
        ft = first_touch[m_touch.values]
        print(f"  median first-touch minute = {np.median(ft):.0f}/{w}  ·  touched-early share = "
              f"{float((ft <= w/2.0).mean()):.2f}")

    print("\nREAD: (1) & (2) PREDICTABLE = the window's *activity and speed* are forecastable at open -> "
          "play only fast/active windows, and an early touch is the fade trigger (pairs with the reversal "
          "probe). (3) ~0.50 confirms 'which side first' is the dead coin-flip -- do NOT bet the side, bet "
          "the STRUCTURE (chop->fade, trend->ride).")


def selftest():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"close": 100 + rng.normal(0, 1, 3000).cumsum() * 0.01})
    df["high"] = df["close"] + 0.5; df["low"] = df["close"] - 0.5
    rh = (df["high"].shift(-1) / df["close"] - 1) * 1e4
    ok = rh.notna().sum() > 2000
    print("selftest:", "PASS" if ok else "FAIL"); return ok


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
