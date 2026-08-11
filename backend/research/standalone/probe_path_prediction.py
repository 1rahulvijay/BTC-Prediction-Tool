"""
probe_path_prediction.py - the intra-window PATH / trendline test, measured honestly.
=====================================================================================
Instead of "up or down at expiry" (proven coin-flip), predict the PATH a day trader cares
about WITHIN the 5m/15m window:
  * how far price travels up / down       -> max_up_bps, max_down_bps   (the highs/lows)
  * total travel                          -> round_range_bps
  * "will it reach +$50 / -$50 / +$100"   -> barrier-TOUCH probabilities
  * which side travels further            -> path DIRECTION

The hypothesis this tests: the MAGNITUDE/TOUCH of the path is volatility (PREDICTABLE -- the
proven edge); the DIRECTION of the path is the coin-flip. Full TA + vol feature matrix, all
causal (known at the round's START = prior bar's close), targets are the NEXT round's path.
Walk-forward + shuffle-null. Regression skill vs persistence (last round's excursion).

Read-only; reuses probe_ta_matrix + probe_vol_features. ASCII output.

Usage:
  python backend/research/standalone/probe_path_prediction.py
  python backend/research/standalone/probe_path_prediction.py --selftest
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
import probe_ta_matrix as TA          # noqa: E402
import probe_vol_features as VF        # noqa: E402

warnings.filterwarnings("ignore")
ROUNDS = TA.ROUNDS


def load_rounds(horizon: int) -> pd.DataFrame:
    r = pd.read_parquet(ROUNDS)
    r = r[r["horizon_min"] == horizon].copy()
    r["ts"] = pd.to_datetime(r["round_start"], utc=True)
    # OHLCV aliases so the shared TA/vol feature builders work, path columns kept alongside
    r["open"] = r["anchor_price"].astype(float)
    r["high"] = r["round_high"].astype(float)
    r["low"] = r["round_low"].astype(float)
    r["close"] = r["expiry_close"].astype(float)
    r["volume"] = r["round_volume"].astype(float)
    return r.sort_values("ts").reset_index(drop=True)


def build_path_targets(r: pd.DataFrame):
    px = float(r["anchor_price"].median())
    b50, b100 = 50 / px * 1e4, 100 / px * 1e4          # $ -> bps at this price level
    up, dn, rng = r["max_up_bps"], r["max_down_bps"], r["round_range_bps"]
    nx = lambda s: s.shift(-1)                          # NEXT round's path (leak-free target)
    return {
        # --- VOL family: magnitude / highs-lows (expected PREDICTABLE) ---
        "max_up_bps (high)":  ("reg", "vol", nx(up), up),         # baseline: persistence (last round)
        "max_down_bps (low)": ("reg", "vol", nx(dn), dn),
        "range_bps (travel)": ("reg", "vol", nx(rng), rng),
        # --- TOUCH / barrier: "reach +$50 within the window" (expected PREDICTABLE) ---
        "touch +$50":   ("clf", "vol", (nx(up) >= b50).astype(float), None),
        "touch -$50":   ("clf", "vol", (nx(dn) <= -b50).astype(float), None),
        "touch +$100":  ("clf", "vol", (nx(up) >= b100).astype(float), None),
        "touch either$50": ("clf", "vol", ((nx(up) >= b50) | (nx(dn) <= -b50)).astype(float), None),
        # --- PATH DIRECTION: which side travels further (expected COIN-FLIP) ---
        "up_bigger_than_down": ("clf", "dir", (nx(up) > -nx(dn)).astype(float), None),
    }


def evaluate(horizon: int):
    r = load_rounds(horizon)
    # full feature matrix: TA + vol, causal at the round's completed bar
    base = TA.build_features(r)
    vol = VF.build_vol_features(r)
    X = pd.concat([base, vol], axis=1)
    rows = []
    for name, (kind, fam, y, basev) in build_path_targets(r).items():
        cols = [X, y.rename("__y__")] + ([basev.rename("__b__")] if basev is not None else [])
        d = pd.concat(cols, axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 800:
            continue
        Xc, yy = d[X.columns], d["__y__"]
        if kind == "clf":
            agg = TA.wf_clf(Xc, yy)
            nm, ns = TA.shuffle_null_clf(Xc, yy)
            real = agg["auc"] > nm + 3 * ns and agg["above_half"] >= agg["n_folds"] - 1 and agg["auc"] > 0.515
            verdict = (("REAL (predictable)" if real else "coin-flip") if fam == "vol"
                       else ("edge?? VERIFY" if real and agg["auc"] >= 0.55 else "coin-flip (as expected)"))
            base_rate = float(max(yy.mean(), 1 - yy.mean()))
            rows.append((name, fam, f"AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f}",
                         f"null {nm:.3f}", f"base {base_rate:.2f}", verdict))
        else:
            agg = TA.wf_reg(Xc, yy, d["__b__"])
            real = agg["skill"] > 0.02
            verdict = "REAL (beats persistence)" if real else "no lift over persistence"
            rows.append((name, fam, f"skill {agg['skill']:+.3f}", "vs persist",
                         f"R^2 {agg['r2']:+.2f}", verdict))
    return rows, len(r)


def _print(horizon, rows, n):
    print("\n" + "=" * 96)
    print(f"BTC {horizon}m  -  PATH / trendline prediction (highs, lows, touches, direction)  n={n}")
    print(f"{'path target':<22}{'family':<8}{'walk-fwd metric':<22}{'null/base':<14}{'detail':<14}verdict")
    print("-" * 96)
    for nm, fam, met, nb, det, ver in rows:
        print(f"{nm:<22}{fam:<8}{met:<22}{nb:<14}{det:<14}{ver}")


def selftest():
    rng = np.random.default_rng(0); n = 3000
    base = np.cumsum(rng.normal(0, 1, n)) + 60000
    r = pd.DataFrame({"round_start": pd.date_range("2026-03-20", periods=n, freq="5min", tz="UTC"),
                      "anchor_price": base, "expiry_close": base + rng.normal(0, 5, n),
                      "round_high": base + np.abs(rng.normal(0, 30, n)),
                      "round_low": base - np.abs(rng.normal(0, 30, n)),
                      "round_volume": np.abs(rng.normal(100, 20, n)), "horizon_min": 5})
    r["open"] = r["anchor_price"]; r["close"] = r["expiry_close"]
    r["high"] = r["round_high"]; r["low"] = r["round_low"]; r["volume"] = r["round_volume"]
    r["max_up_bps"] = (r["round_high"] / r["anchor_price"] - 1) * 1e4
    r["max_down_bps"] = (r["round_low"] / r["anchor_price"] - 1) * 1e4
    r["round_range_bps"] = r["max_up_bps"] - r["max_down_bps"]
    r["ts"] = pd.to_datetime(r["round_start"], utc=True)
    tg = build_path_targets(r)
    ok = len(tg) == 8 and all(t[2].notna().sum() > 1000 for t in tg.values())
    print(f"selftest: built {len(tg)} path targets, all populated={ok}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    for hz in ([a.h] if a.h else [5, 15]):
        rows, n = evaluate(hz)
        _print(hz, rows, n)
    print("\nREAD: 'vol' family (highs/lows/touches) = the path's MAGNITUDE = volatility -> predictable "
          "and tradeable as touch/no-touch or 'will it move enough'. 'dir' family (which way travels "
          "further) = the coin-flip. You can predict HOW FAR, not WHICH WAY.")


if __name__ == "__main__":
    main()
