"""
probe_trade_plan.py - STABLE intra-window trade plan (high/low/touch) + honest backtest.
========================================================================================
Polymarket lets you EXIT MID-WINDOW, so the dead close-direction doesn't matter -- the
predictable intra-window PATH does. This builds ONE stable signal per window (computed once
at the open from bar-close features, NOT a per-second probability):

    predicted HIGH (max_up), predicted LOW (max_down), predicted RANGE,
    P(touch +$50/-$50/+$100/move-at-all),  + a (clearly weak) directional lean.

...then BACKTESTS it honestly out-of-sample:
  * model bake-off on touch-either (pick the best of the boosting family)
  * touch-probability CALIBRATION (does predicted 70% touch -> ~70% realized?)
  * predicted HIGH/LOW reached-rate + MAE in bps (did the trendline get hit within the window?)
  * range skill vs persistence
Time-of-day / day-of-week are in the feature matrix (the 1.9x vol seasonality carries here).

Read-only; reuses probe_ta_matrix + probe_vol_features + probe_path_prediction. ASCII output.

Usage:
  python backend/research/standalone/probe_trade_plan.py
  python backend/research/standalone/probe_trade_plan.py --selftest
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
import probe_ta_matrix as TA           # noqa: E402
import probe_vol_features as VF         # noqa: E402
import probe_path_prediction as PP      # noqa: E402

warnings.filterwarnings("ignore")


def _models():
    import xgboost as xgb
    import lightgbm as lgb
    from catboost import CatBoostClassifier, CatBoostRegressor
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    clf = {
        "HistGBM": lambda: HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_depth=4, random_state=0),
        "XGBoost": lambda: xgb.XGBClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, subsample=0.8,
                                             eval_metric="logloss", verbosity=0, n_jobs=2),
        "LightGBM": lambda: lgb.LGBMClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, subsample=0.8,
                                               verbose=-1, n_jobs=2),
        "CatBoost": lambda: CatBoostClassifier(iterations=200, depth=4, learning_rate=0.05, verbose=0,
                                               allow_writing_files=False),
    }
    reg = {
        "HistGBM": lambda: HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_depth=4, random_state=0),
        "XGBoost": lambda: xgb.XGBRegressor(n_estimators=250, max_depth=4, learning_rate=0.05, subsample=0.8,
                                            verbosity=0, n_jobs=2),
        "LightGBM": lambda: lgb.LGBMRegressor(n_estimators=250, max_depth=4, learning_rate=0.05, subsample=0.8,
                                              verbose=-1, n_jobs=2),
        "CatBoost": lambda: CatBoostRegressor(iterations=200, depth=4, learning_rate=0.05, verbose=0,
                                              allow_writing_files=False),
    }
    return clf, reg


def _prep(horizon):
    r = PP.load_rounds(horizon)
    X = pd.concat([TA.build_features(r), VF.build_vol_features(r)], axis=1)
    px = float(r["anchor_price"].median())
    b50, b100 = 50 / px * 1e4, 100 / px * 1e4
    up, dn, rng = r["max_up_bps"], r["max_down_bps"], r["round_range_bps"]
    nx = lambda s: s.shift(-1)
    tgt = {
        "max_up": ("reg", nx(up), up), "max_down": ("reg", nx(dn), dn), "range": ("reg", nx(rng), rng),
        "touch+50": ("clf", (nx(up) >= b50).astype(float)), "touch-50": ("clf", (nx(dn) <= -b50).astype(float)),
        "touch+100": ("clf", (nx(up) >= b100).astype(float)), "touch_either": ("clf", ((nx(up) >= b50) | (nx(dn) <= -b50)).astype(float)),
        "dir": ("clf", (r["close"].shift(-1) > r["close"]).astype(float)),
    }
    return r, X, tgt, px, b50, b100


def bakeoff(horizon=5):
    r, X, tgt, *_ = _prep(horizon)
    clf, _ = _models()
    y = tgt["touch_either"][1]
    d = pd.concat([X, y.rename("y")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    print(f"\n=== PATH-TARGET BAKEOFF ({horizon}m, target=touch_either +/-$50, walk-forward) ===")
    best, best_auc = None, -1
    for nm, fac in clf.items():
        agg = TA.wf_clf(d[X.columns], d["y"], factory=fac)
        print(f"  {nm:<10} AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} ({agg['above_half']}/{agg['n_folds']})")
        if agg["auc"] > best_auc:
            best, best_auc = nm, agg["auc"]
    print(f"  -> best: {best} (AUC {best_auc:.3f})")
    return best


def backtest(horizon, model_name):
    r, X, tgt, px, b50, b100 = _prep(horizon)
    clf, reg = _models()
    print(f"\n=== TRADE-PLAN BACKTEST ({horizon}m, model={model_name}, temporal 80/20) ===")
    # classification: AUC + calibration on the test fold
    print("  CLASSIFICATION (touch) -- AUC + calibration (predicted-bin -> realized rate):")
    for name in ("touch+50", "touch-50", "touch+100", "touch_either", "dir"):
        y = tgt[name][1]
        d = pd.concat([X, y.rename("y")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        n = len(d); cut = int(n * 0.8)
        m = clf[model_name](); m.fit(d[X.columns].iloc[:cut], d["y"].iloc[:cut])
        p = m.predict_proba(d[X.columns].iloc[cut:])[:, 1]
        yte = d["y"].iloc[cut:].values
        from sklearn.metrics import roc_auc_score
        try:
            auc = roc_auc_score(yte, p)
        except ValueError:
            auc = float("nan")
        # calibration: low/mid/high predicted buckets
        cal = []
        for lo, hi in ((0, .33), (.33, .66), (.66, 1.01)):
            msk = (p >= lo) & (p < hi)
            cal.append(f"[{lo:.2f}-{hi:.2f}]={yte[msk].mean():.2f}(n{msk.sum()})" if msk.sum() else f"[{lo:.2f}-{hi:.2f}]=-")
        tag = "  (WEAK/coin-flip)" if name == "dir" else ""
        print(f"    {name:<14} AUC {auc:.3f}  calib {' '.join(cal)}{tag}")
    # regression: predicted high/low/range -- MAE + reached-rate
    print("  REGRESSION (high/low/range) -- skill vs persistence + 'predicted level reached within window':")
    for name in ("max_up", "max_down", "range"):
        _, y, base = tgt[name]
        d = pd.concat([X, y.rename("y"), base.rename("b"), r["max_up_bps"].rename("au"),
                       r["max_down_bps"].rename("ad")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        n = len(d); cut = int(n * 0.8)
        m = reg[model_name](); m.fit(d[X.columns].iloc[:cut], d["y"].iloc[:cut])
        pred = m.predict(d[X.columns].iloc[cut:])
        yte = d["y"].iloc[cut:].values
        mae = np.mean(np.abs(yte - pred))
        mae_b = np.mean(np.abs(yte - d["b"].iloc[cut:].values))
        skill = 1 - np.mean((yte - pred) ** 2) / (np.mean((yte - d["b"].iloc[cut:].values) ** 2) + 1e-30)
        reached = ""
        if name == "max_up":   # did price actually reach the predicted high? (pred is conservative 70%)
            actual_high = d["au"].iloc[cut:].values
            reached = f"  predHigh reached {float((actual_high >= pred * 0.7).mean()):.2f} of windows"
        if name == "max_down":
            actual_low = d["ad"].iloc[cut:].values
            reached = f"  predLow reached {float((actual_low <= pred * 0.7).mean()):.2f} of windows"
        print(f"    {name:<10} MAE {mae:.1f}bps (persist {mae_b:.1f})  skill {skill:+.3f}{reached}")


def sample_signal(horizon, model_name, k=4):
    """Emit a few STABLE trade-plan signals from the test tail (computed once per window)."""
    r, X, tgt, px, b50, b100 = _prep(horizon)
    clf, reg = _models()
    d_idx = X.replace([np.inf, -np.inf], np.nan).dropna().index
    cut = int(len(d_idx) * 0.8)
    tr_idx, te_idx = d_idx[:cut], d_idx[cut:]
    Xtr = X.loc[tr_idx]
    preds = {}
    for name in ("max_up", "max_down", "range"):
        m = reg[model_name](); m.fit(Xtr, tgt[name][1].loc[tr_idx]); preds[name] = m
    for name in ("touch+50", "touch-50", "touch_either", "dir"):
        m = clf[model_name](); m.fit(Xtr, tgt[name][1].loc[tr_idx]); preds[name] = m
    print(f"\n=== SAMPLE STABLE TRADE-PLAN SIGNALS ({horizon}m, last {k} test windows) ===")
    for i in te_idx[-k:]:
        anchor = r["close"].loc[i]
        xi = X.loc[[i]]
        hi = preds["max_up"].predict(xi)[0]; lo = preds["max_down"].predict(xi)[0]
        rg = preds["range"].predict(xi)[0]
        p50 = preds["touch+50"].predict_proba(xi)[0, 1]; pm50 = preds["touch-50"].predict_proba(xi)[0, 1]
        pe = preds["touch_either"].predict_proba(xi)[0, 1]; pdir = preds["dir"].predict_proba(xi)[0, 1]
        lean = "UP" if pdir > 0.5 else "DOWN"
        print(f"  @${anchor:,.0f}: HIGH ~+${hi/1e4*anchor:,.0f} (touch+$50 {p50*100:.0f}%) | "
              f"LOW ~-${abs(lo)/1e4*anchor:,.0f} (touch-$50 {pm50*100:.0f}%) | range ~${rg/1e4*anchor:,.0f} | "
              f"P(moves>=$50) {pe*100:.0f}% | lean {lean} (WEAK {abs(pdir-0.5)*200:.0f}%)")
    print("  PLAN: enter the cheap side, take profit near the predicted HIGH/LOW *before expiry* (early exit); "
          "size by P(moves) and the touch odds, NOT the coin-flip lean.")


def selftest():
    r, X, tgt, px, b50, b100 = _prep(5)
    ok = "touch_either" in tgt and X.shape[1] > 40 and tgt["touch_either"][1].notna().sum() > 1000
    print(f"selftest: features={X.shape[1]}, touch_either populated={tgt['touch_either'][1].notna().sum()}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    best = bakeoff(a.h)
    backtest(a.h, best)
    sample_signal(a.h, best)


if __name__ == "__main__":
    main()
