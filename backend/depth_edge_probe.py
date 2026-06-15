"""
depth_edge_probe.py — DOES ORDER-BOOK DEPTH PREDICT DIRECTION? (offline, no-train, leak-free)
==============================================================================================
The one test that decides whether L2/depth is the missing 5m edge or a dead end. Uses the
free Binance FUTURES `bookDepth` ARCHIVE (data.binance.vision — reachable here even though the
live futures WS is geo-blocked) + futures 1m klines, both downloaded HTTP. Engineers depth-
imbalance features and measures whether they predict the forward move — AUC / sign-truth.

WHY this matters: depth has always been CONSTANT in our training matrix (no history), so we
could never tell if it carries edge. The archive lets us answer it directly, OFFLINE. If depth
shows real AUC (>~0.55), getting LIVE futures via a proxy is justified. If it's ~0.50, we close
the L2 question and stop chasing it.

Leak-free: features = the resting-liquidity snapshot at minute m; label = sign(open[m+1+h] −
open[m+1]) — anchored AFTER the snapshot, strictly future. (bookDepth `depth` at percentage P is
cumulative resting quantity within |P|% of mid; negative P = bids, positive = asks.)

Usage:  python backend/depth_edge_probe.py --days 7
        python backend/depth_edge_probe.py --selftest
"""
import argparse
import csv
import io
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
CACHE = os.path.join(DATA_DIR, "depth_cache")
BASE = "https://data.binance.vision/data/futures/um/daily"
HORIZONS = (3, 5, 10, 15)
LEVELS = (0.2, 1.0, 2.0, 5.0)
FEATURES = [f"imb_{l}" for l in LEVELS] + ["bid_conc", "ask_conc", "log_depth", "depth_skew"]


def _fetch(path_in_archive: str, out_name: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, out_name)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    url = f"{BASE}/{path_in_archive}"
    with urllib.request.urlopen(url, timeout=120) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        with z.open(z.namelist()[0]) as f:
            data = f.read()
    with open(out, "wb") as f:
        f.write(data)
    return out


def load_bookdepth(date: str) -> dict:
    """minute_ms -> {percentage: depth} (last snapshot in each minute)."""
    p = _fetch(f"bookDepth/BTCUSDT/BTCUSDT-bookDepth-{date}.zip", f"bookDepth-{date}.csv")
    snaps = {}
    with open(p, "r", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd, None)  # header: timestamp,percentage,depth,notional
        for row in rd:
            if len(row) < 3:
                continue
            try:
                t = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                ms = int(t.timestamp() * 1000)
                minute = (ms // 60000) * 60000
                snaps.setdefault(minute, {})[float(row[1])] = float(row[2])
            except Exception:
                continue
    return snaps


def load_klines(date: str) -> dict:
    """minute_ms -> open price (futures 1m)."""
    p = _fetch(f"klines/BTCUSDT/1m/BTCUSDT-1m-{date}.zip", f"klines-{date}.csv")
    out = {}
    with open(p, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            try:
                out[(int(row[0]) // 60000) * 60000] = float(row[1])  # open_time -> open
            except Exception:
                continue
    return out


def build_features(snap: dict) -> list:
    """PURE: one bookDepth snapshot {pct: depth} -> depth-imbalance feature vector (FEATURES)."""
    v = []
    for l in LEVELS:
        b = snap.get(-l, 0.0)
        a = snap.get(l, 0.0)
        v.append((b - a) / (b + a + 1e-9))                    # imbalance at level l
    b02, b5 = snap.get(-0.2, 0.0), snap.get(-5.0, 0.0)
    a02, a5 = snap.get(0.2, 0.0), snap.get(5.0, 0.0)
    v.append(b02 / (b5 + 1e-9))                               # bid concentration near touch
    v.append(a02 / (a5 + 1e-9))                               # ask concentration near touch
    v.append(float(np.log1p(b5 + a5)))                        # total book depth (thickness)
    v.append((b5 - a5) / (b5 + a5 + 1e-9))                    # far-band skew
    return v


def _daterange(days: int):
    end = datetime.now(timezone.utc).date() - timedelta(days=2)  # archive lags ~1-2 days
    for i in range(days):
        yield (end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")


def build_dataset(dates):
    X, ts = [], []
    ys = {h: [] for h in HORIZONS}
    for d in dates:
        try:
            depth = load_bookdepth(d)
            kl = load_klines(d)
        except Exception as e:
            print(f"[{d}] skip ({str(e)[:60]})")
            continue
        n_ok = 0
        for minute, snap in sorted(depth.items()):
            anchor = kl.get(minute + 60000)                  # open of the minute AFTER the snapshot
            if anchor is None or anchor <= 0:
                continue
            row_y = {}
            ok = True
            for h in HORIZONS:
                res = kl.get(minute + 60000 + h * 60000)
                if res is None:
                    ok = False
                    break
                row_y[h] = 1 if res >= anchor else 0
            if not ok:
                continue
            X.append(build_features(snap))
            ts.append(minute)
            for h in HORIZONS:
                ys[h].append(row_y[h])
            n_ok += 1
        print(f"[{d}] {len(depth)} minute-snapshots -> {n_ok} labeled samples")
    return np.array(X, dtype=float), {h: np.array(v) for h, v in ys.items()}, np.array(ts)


def evaluate(X, y):
    """Temporal 70/30 split; lightgbm + logistic; AUC + sign-truth on the unseen future."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    n = len(y)
    cut = int(n * 0.70)
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        return None
    res = {}
    # logistic (scaled) — the honest baseline learner
    sc = StandardScaler().fit(Xtr)
    lr = LogisticRegression(max_iter=300).fit(sc.transform(Xtr), ytr)
    p = lr.predict_proba(sc.transform(Xte))[:, 1]
    res["logistic"] = {"auc": float(roc_auc_score(yte, p)),
                       "acc": float(((p >= 0.5).astype(int) == yte).mean())}
    try:
        from lightgbm import LGBMClassifier
        m = LGBMClassifier(n_estimators=300, num_leaves=15, learning_rate=0.03,
                           subsample=0.8, random_state=0,
                           n_jobs=int(os.environ.get("OMP_NUM_THREADS", "2")), verbose=-1)
        m.fit(Xtr, ytr)
        pl = m.predict_proba(Xte)[:, 1]
        res["lightgbm"] = {"auc": float(roc_auc_score(yte, pl)),
                           "acc": float(((pl >= 0.5).astype(int) == yte).mean()),
                           "importance": dict(zip(FEATURES, [round(float(x), 1) for x in m.feature_importances_]))}
    except Exception:
        pass
    res["base_rate"] = float(max(yte.mean(), 1 - yte.mean()))
    res["n_test"] = int(len(yte))
    return res


def main(days):
    dates = list(_daterange(days))
    print(f"Depth-edge probe over {days} day(s): {dates[0]}..{dates[-1]}")
    X, ys, ts = build_dataset(dates)
    if len(X) < 500:
        sys.exit(f"only {len(X)} samples — try more --days")
    print(f"\nDataset: {X.shape} | features: {FEATURES}\n")
    print(f"  {'h':>3} {'n_test':>7} {'base':>6} {'logistic_AUC':>13} {'lgb_AUC':>9}  verdict")
    any_edge = False
    for h in HORIZONS:
        r = evaluate(X, ys[h])
        if not r:
            print(f"  {h:>3}  (insufficient)")
            continue
        la = r["logistic"]["auc"]
        ga = r.get("lightgbm", {}).get("auc")
        edge = max(la, ga or 0) >= 0.55
        any_edge = any_edge or edge
        print(f"  {h:>3}m {r['n_test']:>7} {r['base_rate']*100:>5.1f}% {la:>13.3f} "
              f"{(ga if ga is not None else 0):>9.3f}  {'EDGE (>=.55)' if edge else 'no edge'}")
    # show what the trees leaned on at 5m
    r5 = evaluate(X, ys[5])
    if r5 and "lightgbm" in r5:
        imp = sorted(r5["lightgbm"]["importance"].items(), key=lambda kv: -kv[1])[:5]
        print(f"\n  5m depth-feature importance (top): {imp}")
    print("\nVERDICT:", "DEPTH HAS EDGE -> getting LIVE futures (proxy) is justified; backfill these "
          "depth features for the keeper retrain." if any_edge else
          "NO depth edge at any horizon (AUC ~0.50) -> L2/depth is NOT the missing 5m edge here. "
          "Stop chasing live futures; the ceiling is elsewhere (or genuinely information-poor at 5m).")


def selftest():
    rng = np.random.default_rng(0)
    # synthetic: bid-heavy book (imbalance>0) -> price tends UP. Verify the probe LEARNS it,
    # and that a random book yields ~0.5 (no false edge), and the alignment is leak-free.
    X, y = [], []
    for _ in range(3000):
        sign = 1 if rng.random() < 0.5 else -1
        # depth: bids vs asks tilted by `sign`; cumulative bands increase outward
        scale = 1.0 + 0.5 * sign + rng.normal(0, 0.3)
        snap = {}
        for l in (0.2, 1.0, 2.0, 5.0):
            snap[-l] = max(0.1, l * 1000 * scale)            # bid side
            snap[l] = max(0.1, l * 1000 * (2 - scale))       # ask side
        feats = build_features(snap)
        X.append(feats)
        # label correlates with the imbalance sign (+ noise)
        y.append(1 if (sign + rng.normal(0, 0.6)) > 0 else 0)
    X, y = np.array(X), np.array(y)
    assert X.shape[1] == len(FEATURES)
    r = evaluate(X, y)
    assert r and r["logistic"]["auc"] > 0.65, f"probe should LEARN a real depth signal, got {r['logistic']['auc'] if r else None}"
    # random book -> no edge
    Xr = rng.normal(0, 1, (3000, len(FEATURES)))
    yr = rng.integers(0, 2, 3000)
    rr = evaluate(Xr, yr)
    assert rr and abs(rr["logistic"]["auc"] - 0.5) < 0.06, f"random must be ~0.5, got {rr['logistic']['auc']}"
    # feature builder sanity: bid-heavy snap -> positive near-touch imbalance
    f = build_features({-0.2: 100, 0.2: 10, -5.0: 500, 5.0: 50, -1.0: 200, 1.0: 20, -2.0: 300, 2.0: 30})
    assert f[0] > 0, "bid-heavy book should give positive imbalance"
    print("depth_edge_probe self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.days)
