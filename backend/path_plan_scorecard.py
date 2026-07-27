"""
path_plan_scorecard.py - LIVE metrics for the path-forecaster head (read-only).
===============================================================================
Reads the rounds the live tracker logged (data/path_plan_outcomes.csv, written by
price_to_beat._log_path_plan_outcome at each resolution) and grades the SERVED plans against
realized window extremes -- the ongoing production scorecard the offline verifier flagged:

  * touch P(move>=$50)  -- predicted vs realized touch rate, by confidence bucket (calibration)
  * round-trip          -- predicted P(both +/-$50) vs realized
  * CHOP/TREND style    -- realized round-trip rate per style (two_sided should >> one_sided)
  * high/low band       -- realized-extreme coverage (target ~0.50)
  * play breakdown      -- count + realized round-trip per play (FADE-SETUP/RIDE/SKIP/WATCH)

Accumulates as the app runs; needs only a handful of resolved rounds to start, hundreds to trust.

Usage:
  python backend/path_plan_scorecard.py
  python backend/path_plan_scorecard.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data"), "path_plan_outcomes.csv")


def _grade(df):
    out = []
    df = df.copy()
    df["touched_either"] = ((df["touched_up_50"] == 1) | (df["touched_dn_50"] == 1)).astype(int)
    out.append(f"rounds graded: {len(df)}  ({sorted(df['horizon'].dropna().unique().tolist())} min)")
    # touch calibration
    out.append("\nP(move>=$50) calibration (predicted bucket -> realized touch rate):")
    for lo, hi in ((0, .33), (.33, .66), (.66, 1.01)):
        m = (df["p_move_50"] >= lo) & (df["p_move_50"] < hi)
        if m.sum():
            out.append(f"  pred[{lo:.2f}-{hi:.2f}]: realized {df.loc[m,'touched_either'].mean():.2f}  (n={int(m.sum())})")
    # round-trip
    out.append("\nround-trip P(both +/-$50) vs realized:")
    for lo, hi in ((0, .2), (.2, .5), (.5, 1.01)):
        m = (df["p_roundtrip"] >= lo) & (df["p_roundtrip"] < hi)
        if m.sum():
            out.append(f"  pred[{lo:.2f}-{hi:.2f}]: realized {df.loc[m,'roundtrip_realized'].mean():.2f}  (n={int(m.sum())})")
    # style separation (the trade rule)
    out.append("\nstyle -> realized round-trip rate (TWO_SIDED should >> ONE_SIDED/QUIET):")
    for s in ("two_sided", "one_sided", "quiet", "mixed"):
        m = df["style"] == s
        if m.sum():
            out.append(f"  {s:<10}: round-trip {df.loc[m,'roundtrip_realized'].mean():.2f}  move>=$50 {df.loc[m,'touched_either'].mean():.2f}  (n={int(m.sum())})")
    # band coverage
    bc = df["band_cover_hi"]
    bc = bc[bc.isin([0, 1, "0", "1"])].astype(float) if bc.dtype == object else bc.dropna()
    if len(bc):
        out.append(f"\nhigh band coverage: {bc.mean():.2f}  (target ~0.50, n={len(bc)})")
    # play breakdown
    out.append("\nplay -> count, realized round-trip:")
    for pl in df["play"].dropna().unique():
        m = df["play"] == pl
        out.append(f"  {str(pl):<11}: n={int(m.sum())}  round-trip {df.loc[m,'roundtrip_realized'].mean():.2f}")
    return "\n".join(out)


def run():
    import pandas as pd
    if not os.path.exists(LOG):
        print(f"no live log yet at {LOG}\n(it fills as the app resolves rounds; run again after a few windows.)")
        return
    df = pd.read_csv(LOG)
    if len(df) < 1:
        print("log is empty -- no resolved rounds yet.")
        return
    print("=" * 78)
    print("PATH-FORECASTER LIVE SCORECARD")
    print("=" * 78)
    print(_grade(df))


def selftest():
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(0); n = 400
    p = rng.uniform(0, 1, n)
    df = pd.DataFrame({"horizon": rng.choice([5, 15], n), "p_move_50": p,
                       "p_roundtrip": p * 0.5, "style": rng.choice(["two_sided", "one_sided", "quiet"], n),
                       "play": rng.choice(["FADE-SETUP", "RIDE", "SKIP"], n),
                       "touched_up_50": (rng.uniform(0, 1, n) < p).astype(int),
                       "touched_dn_50": (rng.uniform(0, 1, n) < p * 0.6).astype(int),
                       "band_cover_hi": rng.integers(0, 2, n)})
    df["roundtrip_realized"] = ((df["touched_up_50"] == 1) & (df["touched_dn_50"] == 1)).astype(int)
    txt = _grade(df)
    ok = "calibration" in txt and "round-trip" in txt and len(txt) > 100
    print(txt[:200] + " ...")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    run()


if __name__ == "__main__":
    main()
