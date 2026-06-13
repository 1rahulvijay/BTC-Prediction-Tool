"""
build_path_labels.py — the PATH-SHAPE head (A1-ext), offline/historical.
========================================================================
Per-horizon multiclass classifier of HOW a window travels (first-passage shape):
  CHOP · UP_DIRECT · UP_THEN_DOWN · DOWN_DIRECT · DOWN_THEN_UP
Turns the heuristic `_path_outlook` (CROSS/HOLD/CHOP rule) into a LEARNED call, and makes
"WAIT → fade the fake-out" (UP_THEN_DOWN / DOWN_THEN_UP) a first-class predicted signal.

Offline: reconstructs intra-window TICK paths from SPOT aggTrades (the shape needs ticks, not
OHLC); features at the window OPEN reuse the beat head's lean backfillable set. Band theta is
vol-scaled from PRE-window vol (no lookahead). HGB multiclass + TEMPORAL split.

NOISE GATE (refuses to save a horizon that fails): test accuracy must beat the majority-class
base rate by >= 3 points on the unseen future split — else the "shape" is unpredictable noise.

Usage:  python backend/build_path_labels.py --start S --end E   |   --validate DATE
"""
import argparse
import os

import numpy as np

from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange
from train_beat_classifier import ticks_to_ohlc, build_beat_features, FEATURE_NAMES, resolve_dates

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "saved_models", "path_model.pkl")
HORIZONS = (3, 5, 7, 10, 15)
CLASSES = ["CHOP", "UP_DIRECT", "UP_THEN_DOWN", "DOWN_DIRECT", "DOWN_THEN_UP"]
_CIX = {c: i for i, c in enumerate(CLASSES)}


def classify_path(prices, open_price, theta):
    """First price to cross open±theta decides the shape; both-crossed = the fade shapes."""
    hi = lo = None
    for i, p in enumerate(prices):
        if hi is None and p >= open_price + theta:
            hi = i
        if lo is None and p <= open_price - theta:
            lo = i
        if hi is not None and lo is not None:
            break
    if hi is None and lo is None:
        return "CHOP"
    if hi is not None and (lo is None or hi < lo):
        return "UP_THEN_DOWN" if lo is not None else "UP_DIRECT"
    return "DOWN_THEN_UP" if hi is not None else "DOWN_DIRECT"


def day_path_samples(ts, price, horizons=HORIZONS):
    """-> {h: (X[features@open], y[class index])} for one day's ticks."""
    order = np.argsort(ts, kind="stable"); ts, price = ts[order], price[order]
    tmin, o, hh, ll, c = ticks_to_ohlc(ts, price)
    if len(c) < 40:
        return {}
    X = build_beat_features(o, hh, ll, c, tmin)
    rv = X[:, FEATURE_NAMES.index("rv_short")]          # pre-window vol (log-ret std) at open
    out = {}
    for h in horizons:
        feats, labels = [], []
        # ANTI-LEAKAGE: features through close[t-1] are what's known at the window open (o[t]);
        # X[t] would include close[t] = inside the window. Use X[t-1] for the window at bar t.
        for t in range(1, len(c) - h):
            w0 = tmin[t]; w1 = w0 + h * 60_000
            lo_i = int(np.searchsorted(ts, w0, "left")); hi_i = int(np.searchsorted(ts, w1, "left"))
            if hi_i - lo_i < 3:
                continue
            theta = o[t] * max(0.0008, 0.6 * float(rv[t - 1]))   # pre-window vol, known at open
            cls = classify_path(price[lo_i:hi_i], o[t], theta)
            feats.append(X[t - 1]); labels.append(_CIX[cls])
        if feats:
            out[h] = (np.array(feats), np.array(labels))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int)
    args = ap.parse_args()
    from sklearn.ensemble import HistGradientBoostingClassifier
    import joblib
    dates, save = resolve_dates(args)
    if not dates:
        ap.error("provide --start/--end, --validate DATE, or --days N")

    agg = {h: ([], []) for h in HORIZONS}
    for d in dates:
        try:
            ts, price, _q, _m = load_aggtrades(download_day(d))
        except Exception as e:
            print(f"[{d}] skip ({str(e)[:60]})"); continue
        ds = day_path_samples(ts, price)
        for h, (Xh, yh) in ds.items():
            agg[h][0].append(Xh); agg[h][1].append(yh)
        print(f"[{d}] path samples: " + ", ".join(f"{h}m={sum(len(a) for a in agg[h][1])}" for h in HORIZONS), flush=True)

    models, passed = {}, []
    print(f"\n{'h':>3} {'n':>8} {'acc':>6} {'base':>6}  verdict")
    for h in HORIZONS:
        if not agg[h][0]:
            continue
        X = np.concatenate(agg[h][0]); y = np.concatenate(agg[h][1])
        if len(y) < 500 or len(np.unique(y)) < 2:
            print(f"{h:>3} {len(y):>8}  (insufficient)"); continue
        a = int(len(y) * 0.8)
        Xtr, ytr, Xte, yte = X[:a], y[:a], X[a:], y[a:]      # temporal
        clf = HistGradientBoostingClassifier(max_iter=300, max_depth=4, learning_rate=0.05,
                                             l2_regularization=1.0, random_state=0)
        clf.fit(Xtr, ytr)
        acc = (clf.predict(Xte) == yte).mean()
        base = np.bincount(yte).max() / len(yte)
        ok = acc >= base + 0.03
        print(f"{h:>3} {len(y):>8} {acc*100:>5.1f}% {base*100:>5.1f}%  {'SIGNAL' if ok else '** NOISE **'}")
        if ok:
            models[h] = {"clf": clf, "features": FEATURE_NAMES, "classes": CLASSES}
            passed.append(h)

    print(f"\n{len(passed)}/{len(HORIZONS)} horizons cleared the gate: {passed}")
    if save and models:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        joblib.dump({"models": models, "classes": CLASSES, "features": FEATURE_NAMES,
                     "horizons": passed}, OUT_PATH)
        print(f"Saved {OUT_PATH}")
    elif save:
        print("No horizon beat base-rate — NOT saved (shape is noise at this data scale).")
    else:
        print("[validate] not saved.")


if __name__ == "__main__":
    main()
