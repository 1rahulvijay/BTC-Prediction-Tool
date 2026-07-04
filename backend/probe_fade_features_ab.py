"""
probe_fade_features_ab.py - does ADDING features beat the timing-only fade model? (honest A/B)
==============================================================================================
The fade model was 99% touch_frac. This tests whether MORE features add real, out-of-sample lift:

  BASELINE  = keepers(5) + touch_frac + side_up            (the timing-only model, 7 feats)
  +TOUCH    = baseline + pre-opp / pre-range from completed bars before the touch
  +RESEARCH = +TOUCH + 17 research-matrix features         (vol/flow/vpin/cvd/funding/basis/ret)

Same ensemble (CatBoost+LightGBM+HistGBM), same temporal 98/2 split. Compares OOS AUC +
precision-at-coverage. A feature set only "helps" if it beats baseline beyond fold noise -- else
the honest conclusion stands: fade success is touch timing, not a rich signal.

Read-only; reuses probe_fade_entry_exit for the leak-free first-passage outcome. ASCII output.

Usage:
  python backend/probe_fade_features_ab.py            # 5m A/B (stride-subsampled for speed)
  python backend/probe_fade_features_ab.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_fade_entry_exit as FE  # noqa: E402
from probe_roundtrip_and_timing import _fade_strict  # noqa: E402  (honest label: must reach anchor TP)
from train_fade_model import _ambiguous_touch_bar, _touch_ctx  # noqa: E402

warnings.filterwarnings("ignore")
STRICT = os.environ.get("BTC_FADE_STRICT", "1") == "1"   # 1 = honest label (reach anchor before stop)
MATRIX = os.path.join(FE.TA.ROOT, "data", "research_matrix_1m.parquet")
KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
TOUCH_CTX = ["overshoot_bps", "pre_opp_bps", "pre_range_bps"]
# NOTE: ret_5m EXCLUDED -- it is a FORWARD 5m return in the matrix (corr 0.976 w/ future ret) = a LABEL, not
# a feature. Including it leaked the outcome (AUC jumped to 0.94). accel/vol/flow are trailing/current = safe.
RESEARCH = ["rv_term", "vol_accel", "log_vol", "range_15m", "micro_range_15m", "vol_spot", "vol_perp",
            "trade_count", "count_accel_5m", "vpin_15m", "vpin_30m", "cvd_1m", "cvd_5m",
            "large_trade_imbalance", "funding_velocity", "perp_spot_basis_bps"]
BASE = KEEPERS + ["touch_frac", "side_up"]
L = 50.0


def build(df, w, stride=3):
    c = df["close"].values
    H = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
    Lo = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
    avail = [r for r in RESEARCH if r in df.columns]
    kv = df[KEEPERS].values
    rv = df[avail].values
    ok = (~np.isnan(H).any(1)) & (~np.isnan(Lo).any(1)) & (~np.isnan(kv).any(1))
    rows = []
    for i in np.where(ok)[0][::stride]:
        anc = c[i]
        for side, su in (("down", 1), ("up", 0)):
            e, win, xm, tm = FE._first_passage_fade(H[i], Lo[i], anc, L, side)
            if not e:
                continue
            if _ambiguous_touch_bar(H[i], Lo[i], anc, side, tm, L):
                continue
            if STRICT:                                   # honest label: reached anchor TP before the stop
                _, win, _ = _fade_strict(H[i], Lo[i], anc, L, side)
            # A 1m touch candle is not an ordered event stream. Use the exact barrier crossing
            # plus completed pre-touch bars, and discard candles that also contain TP/stop.
            overshoot, pre_opp, pre_range = _touch_ctx(H[i], Lo[i], anc, side, tm, L)
            rows.append(list(kv[i]) + [(w - tm) / w, su, overshoot, pre_opp, pre_range] + list(rv[i]) + [int(win)])
    cols = KEEPERS + ["touch_frac", "side_up"] + TOUCH_CTX + avail + ["fade_win"]
    return pd.DataFrame(rows, columns=cols)


def _fit_eval(d, feats):
    from catboost import CatBoostClassifier
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score
    dd = d[feats + ["fade_win"]].replace([np.inf, -np.inf], np.nan).dropna()
    X = dd[feats].values; y = dd["fade_win"].values.astype(int)
    n = len(dd); a, b = int(n * 0.96), int(n * 0.98)
    models = [CatBoostClassifier(iterations=250, depth=4, learning_rate=0.05, random_seed=0, verbose=0, allow_writing_files=False),
              lgb.LGBMClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, verbose=-1, n_jobs=2),
              HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_depth=4, random_state=0)]
    for m in models:
        m.fit(X[:a], y[:a])
    pr = lambda XX: np.mean([m.predict_proba(XX)[:, 1] for m in models], axis=0)
    iso = IsotonicRegression(out_of_bounds="clip").fit(pr(X[a:b]), y[a:b])
    pte = iso.transform(pr(X[b:])); yte = y[b:]
    try:
        auc = roc_auc_score(yte, pr(X[b:]))
    except ValueError:
        auc = float("nan")
    order = np.argsort(-pte)
    cov = {c_: float(yte[order[:max(20, int(len(pte) * c_))]].mean()) for c_ in (0.25, 0.10)}
    return auc, float(yte.mean()), cov, len(dd)


def run(w=5):
    df = pd.read_parquet(MATRIX).sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    d = build(df, w)
    avail = [r for r in RESEARCH if r in df.columns]
    sets = {"BASELINE (timing)": BASE, "+TOUCH-CTX": BASE + TOUCH_CTX, "+RESEARCH (all)": BASE + TOUCH_CTX + avail}
    print("\n" + "=" * 84)
    print(f"FADE feature A/B ({w}m, n_events={len(d)}, temporal 96/2/2)")
    print(f"{'feature set':<20}{'n_feats':<9}{'OOS AUC':<10}{'win@25%':<10}{'win@10%':<10}vs baseline")
    print("-" * 84)
    base_auc = None
    for nm, feats in sets.items():
        auc, base_win, cov, n = _fit_eval(d, feats)
        if base_auc is None:
            base_auc = auc
        delta = auc - base_auc
        tag = "(baseline)" if nm.startswith("BASELINE") else (f"AUC {delta:+.3f} {'LIFT' if delta > 0.01 else 'no lift'}")
        print(f"{nm:<20}{len(feats):<9}{auc:<10.3f}{cov[0.25]:<10.3f}{cov[0.10]:<10.3f}{tag}")
    print(f"\n(base fade-win rate ~{base_win:.2f}. A feature set 'helps' only if OOS AUC lift > ~0.01 beyond noise.)")


def selftest():
    n = 4000
    df = pd.DataFrame({k: np.abs(np.random.default_rng(0).normal(1, .3, n)) for k in KEEPERS})
    df["close"] = 60000 + np.cumsum(np.random.default_rng(1).normal(0, 18, n))
    df["high"] = df["close"] + 3; df["low"] = df["close"] - 3; df["ts_ms"] = np.arange(n) * 60000
    d = build(df, 5, stride=1)
    ok = len(d) > 20 and "overshoot_bps" in d.columns
    print(f"selftest: built {len(d)} events, touch-ctx cols present={ok}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    run(a.h)


if __name__ == "__main__":
    main()
