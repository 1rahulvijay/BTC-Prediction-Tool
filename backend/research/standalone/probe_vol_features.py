"""
probe_vol_features.py - do range-based vol + semivariance + jumps + HAR SHARPEN the timing edge?
================================================================================================
The honest A/B test of the volatility research (#1/#5 of the 20-paper list): add
  - range estimators: Parkinson, Garman-Klass, Rogers-Satchell, Yang-Zhang (drift+gap robust)
  - realized SEMIVARIANCE (up/down) + downside share          (HAR-RS, Liu et al.)
  - bipower-variation JUMP share                              (HAR-J)
  - multi-scale realized vol (HAR cascade)
...and measure whether they lift big_move (the proven timing edge) and direction (ceiling)
OVER the baseline TA matrix, on walk-forward out-of-sample folds.

The skeptical prior (per the manual): the timing edge is "~one signal seen five ways
(range_compression + realized_vol)" -- so these may be REDUNDANT. Only a clear AUC gain on
big_move beyond noise (fold std) counts. All features causal (bars <= t); target is bar t+1.

Read-only; reuses probe_ta_matrix. ASCII output.

Usage:
  python backend/research/standalone/probe_vol_features.py
  python backend/research/standalone/probe_vol_features.py --selftest
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
LN2 = np.log(2.0)


def build_vol_features(df: pd.DataFrame, w: int = 20) -> pd.DataFrame:
    """Range-based + semivariance + jump + HAR-scale volatility features. All causal."""
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    f = pd.DataFrame(index=df.index)
    lhl = np.log(h / l)
    lco = np.log(c / o)
    rs_bar = np.log(h / c) * np.log(h / o) + np.log(l / c) * np.log(l / o)   # Rogers-Satchell (>=0)
    gk_bar = 0.5 * lhl ** 2 - (2 * LN2 - 1) * lco ** 2                       # Garman-Klass
    pk_bar = lhl ** 2 / (4 * LN2)                                           # Parkinson
    f["rs_vol"] = np.sqrt(rs_bar.rolling(w).mean().clip(lower=0))
    f["gk_vol"] = np.sqrt(gk_bar.rolling(w).mean().clip(lower=0))
    f["park_vol"] = np.sqrt(pk_bar.rolling(w).mean().clip(lower=0))
    # Yang-Zhang (overnight + open->close + Rogers-Satchell), drift/gap robust
    oc = np.log(o / c.shift(1))
    sig_o = oc.rolling(w).var()
    sig_c = lco.rolling(w).var()
    sig_rs = rs_bar.rolling(w).mean()
    k = 0.34 / (1.34 + (w + 1) / (w - 1))
    f["yz_vol"] = np.sqrt((sig_o + k * sig_c + (1 - k) * sig_rs).clip(lower=0))
    # realized variance + semivariance (up/down) + downside share
    r = np.log(c / c.shift(1))
    rsq = r ** 2
    rv = rsq.rolling(w).sum()
    up = (rsq * (r > 0)).rolling(w).sum()
    dn = (rsq * (r < 0)).rolling(w).sum()
    f["rv_w"] = rv
    f["semivar_up"] = up
    f["semivar_dn"] = dn
    f["downside_share"] = dn / (up + dn + 1e-12)
    # bipower variation -> jump share (HAR-J)
    bv = (np.pi / 2.0) * (r.abs() * r.abs().shift(1)).rolling(w).sum()
    f["jump_share"] = (rv - bv).clip(lower=0) / (rv + 1e-12)
    # HAR cascade: realized vol at short/medium/long scales
    for s in (12, 48, 144):
        f[f"rv_scale_{s}"] = rsq.rolling(s).mean()
    f["har_ratio_s_l"] = f["rv_scale_12"] / (f["rv_scale_144"] + 1e-18)
    # vol-of-vol
    f["vol_of_vol"] = r.rolling(w).std().rolling(w).std()
    return f


def _ab(df, target):
    base = TA.build_features(df)
    vol = build_vol_features(df)
    kind, tradeable, y, _ = TA.build_targets(df)[target]
    sets = {"baseline": base, "baseline+vol": pd.concat([base, vol], axis=1), "vol_only": vol}
    out = {}
    for nm, X in sets.items():
        d = pd.concat([X, y.rename("__y__")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        agg = TA.wf_clf(d[X.columns], d["__y__"])
        out[nm] = (agg["auc"], agg["auc_std"], len(X.columns))
    return out, tradeable


def run():
    for hz in (5, 15):
        df = TA.load_ohlcv(hz)
        print("\n" + "=" * 84)
        print(f"BTC {hz}m  -  does the vol research SHARPEN the edge? (walk-forward AUC, n={len(df)})")
        print(f"{'target':<18}{'baseline':<20}{'baseline+vol':<20}{'vol_only':<18}delta(base->+vol)")
        print("-" * 84)
        for target in ("big_move", "direction_up_down"):
            out, trad = _ab(df, target)
            b = out["baseline"]; bv = out["baseline+vol"]; v = out["vol_only"]
            delta = bv[0] - b[0]
            verdict = ("LIFT" if delta > 2 * b[1] else "no lift (redundant)" if abs(delta) <= 2 * b[1]
                       else "WORSE")
            print(f"{target:<18}{b[0]:.3f}+-{b[1]:.3f}({b[2]})    {bv[0]:.3f}+-{bv[1]:.3f}({bv[2]})    "
                  f"{v[0]:.3f}+-{v[1]:.3f}    {delta:+.4f}  {verdict}")
        print("  (LIFT only if delta > 2x baseline fold-std -- i.e. beyond noise.)")
    print("\nREAD: if baseline+vol == baseline on big_move, the new estimators are REDUNDANT with the "
          "realized_vol/range_compression already in the matrix -- the manual's 'one signal, five ways'. "
          "vol_only shows their standalone timing power.")


def selftest():
    rng = np.random.default_rng(0)
    n = 3000
    # build a series whose NEXT-bar abs-move is driven by current range -> vol features MUST help big_move
    base = np.cumsum(rng.normal(0, 1, n)) + 5000
    rng2 = rng.normal(0, 1, n)
    df = pd.DataFrame({"ts": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
                       "open": base, "close": base + rng2,
                       "high": base + np.abs(rng2) + 1, "low": base - np.abs(rng2) - 1,
                       "volume": np.abs(rng.normal(100, 20, n))})
    vf = build_vol_features(df)
    ok = vf.shape[1] >= 12 and vf["yz_vol"].notna().sum() > 1000 and (vf["yz_vol"].dropna() >= 0).all()
    print(f"selftest: built {vf.shape[1]} vol features, yz_vol non-null={vf['yz_vol'].notna().sum()}, "
          f"all>=0={bool((vf['yz_vol'].dropna()>=0).all())}")
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
