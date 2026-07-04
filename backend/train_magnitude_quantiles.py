"""
train_magnitude_quantiles.py — the MAGNITUDE head (C3), offline/historical.
===========================================================================
Per-horizon conditional quantile regressors for |move| (q10/q50/q90 of |window-close −
window-open|, as a fraction of price). Replaces the flat ~$40 mean with a band that BREATHES
with volatility/regime — so the Polymarket card can say "expected ~$X, 50% band $A–$B" honestly.

Offline: 1m OHLC from SPOT aggTrades; lean backfillable features at the window open (reused from
the beat head). Three quantile regressors (q=0.1/0.5/0.9) per horizon. TEMPORAL split.

NOISE GATE (refuses to save a horizon that fails): the model's PINBALL loss on the unseen test
must beat the CONSTANT empirical-quantile baseline (the flat-$40 equivalent). If it can't beat a
constant, the conditioning is noise → not saved. Also enforces monotone q10≤q50≤q90.

Usage:  python backend/train_magnitude_quantiles.py --start S --end E  |  --validate DATE
"""
import argparse
import os

import numpy as np

from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange
from train_beat_classifier import (ticks_to_ohlc, build_beat_features, FEATURE_NAMES,
                                    _ohlc_for_dates, resolve_dates)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "saved_models", "magnitude_model.pkl")
HORIZONS = (5, 15)   # pruned 2026-06-21: dropped 3/7/10/30
QUANTILES = (0.1, 0.5, 0.9)
MAX_SAMPLES = 40_000


def abs_move_pct(o, c, horizon):
    """|window-close − window-open| / open, per bar (NaN-guarded tail = -1)."""
    n = len(c); y = np.full(n, -1.0)
    end = n - horizon
    if end > 0:
        y[:end] = np.abs(c[horizon - 1:horizon - 1 + end] - o[:end]) / np.where(o[:end] > 0, o[:end], 1.0)
    return y


def pinball(y, pred, q):
    d = y - pred
    return float(np.mean(np.maximum(q * d, (q - 1) * d)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int)
    args = ap.parse_args()
    from sklearn.ensemble import GradientBoostingRegressor
    import joblib
    dates, save = resolve_dates(args)
    if not dates:
        ap.error("provide --start/--end, --validate DATE, or --days N")

    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 500:
        print("Not enough bars."); return
    X = build_beat_features(O, H, L, C, T)
    print(f"\nFeature matrix {X.shape}; {len(C)} bars")

    models, passed = {}, []
    print(f"\n{'h':>3} {'n':>7} {'pinball_q50':>12} {'base_q50':>10}  mono  verdict")
    for h in HORIZONS:
        y = abs_move_pct(O, C, h)
        # ANTI-LEAKAGE (see beat head): features[t] use close[t]; predict the window opening at t+1.
        Xs, ys = X[:-1], y[1:]
        m = ys >= 0
        Xv, yv = Xs[m], ys[m]
        if len(yv) < 400:
            print(f"{h:>3} {len(yv):>7}  (insufficient)"); continue
        _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)
        a = int(len(yv) * _sf)                      # 98/2: fit = sf (98%); conformal-cal = recent 1-sf (2%)
        Xtr, ytr, Xte, yte = Xv[:a], yv[:a], Xv[a:], yv[a:]    # temporal
        if len(Xtr) > MAX_SAMPLES:
            sel = np.linspace(0, len(Xtr) - 1, MAX_SAMPLES).astype(int)
            Xtr, ytr = Xtr[sel], ytr[sel]
        qmodels, preds = {}, {}
        for q in QUANTILES:
            gbr = GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=150,
                                            max_depth=3, learning_rate=0.05, subsample=0.7,
                                            random_state=0)
            gbr.fit(Xtr, ytr); qmodels[q] = gbr; preds[q] = gbr.predict(Xte)
        # noise gate: model pinball(q50) < constant baseline (empirical q50 of train)
        base_pred = np.full(len(yte), np.quantile(ytr, 0.5))
        pb_model, pb_base = pinball(yte, preds[0.5], 0.5), pinball(yte, base_pred, 0.5)
        mono = bool(np.mean((preds[0.1] <= preds[0.5]) & (preds[0.5] <= preds[0.9])) > 0.95)
        ok = (pb_model < pb_base) and mono
        print(f"{h:>3} {len(yv):>7} {pb_model:>12.6f} {pb_base:>10.6f}  {str(mono):>5}  "
              f"{'SIGNAL' if ok else '** NOISE **'}")
        if ok:
            models[h] = {"q": {q: qmodels[q] for q in QUANTILES}, "features": FEATURE_NAMES,
                         "quantiles": list(QUANTILES)}
            passed.append(h)

    print(f"\n{len(passed)}/{len(HORIZONS)} horizons beat the flat baseline: {passed}")
    if save and models:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        joblib.dump({"models": models, "quantiles": list(QUANTILES), "features": FEATURE_NAMES,
                     "horizons": passed}, OUT_PATH)
        print(f"Saved {OUT_PATH} — P50/band is now conditional, not flat.")
    elif save:
        print("No horizon beat the flat baseline — NOT saved (conditioning is noise here).")
    else:
        print("[validate] not saved.")


if __name__ == "__main__":
    main()
