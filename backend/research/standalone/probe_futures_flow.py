"""
probe_futures_flow.py - can PERP-FUTURES flow + basis break the direction ceiling?
==================================================================================
This is the honest "new information" test. Spot OHLCV is information-capped (~0.52 AUC,
proven ~15 ways). The only lever left is data the SPOT bars do not contain. crossvenue_flow
.parquet has exactly that -- perpetual-FUTURES CVD, spot-vs-perp flow divergence, and the
perp-spot BASIS (geo-blocked live on this box, but backfilled). Futures positioning/basis is
the single most-cited directional signal absent from spot bars.

Test: align the 1-minute futures features to each 5m round at its ANCHOR (causal: only data
<= round start), then predict the round's direction (expiry vs anchor) with walk-forward +
shuffle-null + the cost-aware bar (AUC>=0.55 to be tradeable after the Polymarket spread).
Three feature sets: futures_only, ohlcv_baseline, ohlcv+futures.

Prior (manual): xvenue_divergence is already-dead for direction; basis untested rigorously.
A clear AUC>=0.55 here that clears the shuffle null = a genuine ceiling break (verify hard).

Read-only. Reuses probe_ta_matrix. ASCII output.

Usage:
  python backend/research/standalone/probe_futures_flow.py
  python backend/research/standalone/probe_futures_flow.py --selftest
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
ROOT = TA.ROOT
CV = os.path.join(ROOT, "data", "crossvenue_flow.parquet")


def build_futures_features(cv: pd.DataFrame) -> pd.DataFrame:
    """Causal 1-minute futures-flow features (rolling backward only)."""
    cv = cv.sort_values("ts").reset_index(drop=True)
    b = cv["perp_spot_basis_bps"]
    cp, csp, cd = cv["cvd_perp"], cv["cvd_spot"], cv["cvd_divergence"]
    vp, vs = cv["vol_perp"], cv["vol_spot"]
    f = pd.DataFrame({"ts": cv["ts"]})
    f["fx_basis"] = b
    f["fx_basis_mean15"] = b.rolling(15).mean()
    f["fx_basis_slope15"] = b - b.shift(15)
    f["fx_basis_z120"] = (b - b.rolling(120).mean()) / (b.rolling(120).std() + 1e-9)
    f["fx_cvdperp_sum15"] = cp.rolling(15).sum()
    f["fx_cvdperp_mean60"] = cp.rolling(60).mean()
    f["fx_cvddiv_mean15"] = cd.rolling(15).mean()
    f["fx_cvdgap"] = cp - csp                              # perp vs spot flow (perp leading?)
    f["fx_cvdgap_sum15"] = (cp - csp).rolling(15).sum()
    f["fx_volratio"] = vp / (vs + 1e-9)
    f["fx_volratio_mean15"] = (vp / (vs + 1e-9)).rolling(15).mean()
    return f


def _join(horizon=5):
    cv = pd.read_parquet(CV)
    cv["ts"] = pd.to_datetime(cv["ts_ms"], unit="ms", utc=True)
    fx = build_futures_features(cv).sort_values("ts")
    df = TA.load_ohlcv(horizon)
    df = df.sort_values("ts").reset_index(drop=True)
    # OHLCV baseline features + target, indexed by ts
    base = TA.build_features(df)
    base["ts"] = df["ts"]                              # keep tz-aware (index-aligned with df)
    base["__y__"] = (df["close"].shift(-1) > df["close"]).astype(float)
    # merge_asof: each round gets the most recent futures state at/<= its anchor (causal)
    merged = pd.merge_asof(base.sort_values("ts"), fx, on="ts", direction="backward")
    fx_cols = [c for c in fx.columns if c.startswith("fx_")]
    base_cols = [c for c in base.columns if c not in ("ts", "__y__")]
    return merged, base_cols, fx_cols


def run(horizon=5):
    merged, base_cols, fx_cols = _join(horizon)
    print("\n" + "=" * 84)
    print(f"BTC {horizon}m DIRECTION -- can PERP-FUTURES flow/basis break the ceiling?  "
          f"(walk-forward, n={len(merged)})")
    print(f"{'feature set':<22}{'walk-fwd AUC':<20}{'shuffle null':<16}verdict (cost bar 0.55)")
    print("-" * 84)
    sets = {"futures_only": fx_cols, "ohlcv_baseline": base_cols, "ohlcv+futures": base_cols + fx_cols}
    for nm, cols in sets.items():
        d = merged[cols + ["__y__"]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 800:
            print(f"{nm:<22} insufficient rows ({len(d)})")
            continue
        Xc, yy = d[cols], d["__y__"]
        agg = TA.wf_clf(Xc, yy)
        nm_, ns_ = TA.shuffle_null_clf(Xc, yy)
        auc = agg["auc"]
        clears = auc > nm_ + 3 * ns_ and agg["above_half"] >= agg["n_folds"] - 1
        verdict = ("CEILING BROKEN -- VERIFY" if (auc >= 0.55 and clears)
                   else "real but sub-cost" if (auc >= 0.515 and clears)
                   else "coin-flip / ceiling")
        print(f"{nm:<22}{auc:.3f}+-{agg['auc_std']:.3f} ({agg['above_half']}/{agg['n_folds']})   "
              f"{nm_:.3f}          {verdict}")
    print("\nREAD: if ohlcv+futures AUC ~= ohlcv_baseline (~0.52), perp flow/basis adds NO directional "
          "signal beyond spot -- the ceiling holds even with futures information (the geo-blocked feed "
          "would not help direction). Only ohlcv+futures >= 0.55 clearing the null = a real break.")


def selftest():
    # synthetic: futures feature that DOES predict the round -> must register a break
    rng = np.random.default_rng(0); n = 4000
    signal = rng.normal(0, 1, n)
    y = (signal > 0).astype(float)
    X = pd.DataFrame({"fx_basis": signal + rng.normal(0, 0.3, n),
                      "fx_cvdgap": rng.normal(0, 1, n)})
    agg = TA.wf_clf(X, pd.Series(y), folds=4)
    print(f"selftest: predictive-futures-feature AUC={agg['auc']:.3f} (expect > 0.8)")
    ok = agg["auc"] > 0.8
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(CV):
        print(f"missing {CV}"); sys.exit(2)
    for hz in ([a.h] if a.h else [5, 15]):
        run(hz)


if __name__ == "__main__":
    main()
