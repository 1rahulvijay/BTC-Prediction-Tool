"""
build_fingerprints_historical.py — the A10 "similar setups" evidence table, offline.
====================================================================================
The historical twin of the live `setup_fingerprint` recorder. For every bucketed setup
(vol × momentum-sign × range × variance-sign) it computes the BEAT rate + sample count over
history, with Laplace shrinkage so thin cells can't lie. This is the evidence the T3 gate and
the "Similar setups: n · success%" UI read — available immediately from history, not after weeks.

Offline: 1m OHLC from SPOT aggTrades; lean features at the window open (reused from the beat head).
NO model, NO noise gate — it is empirical frequencies with shrinkage (honest by construction; thin
cells shrink toward 0.5 and are flagged low-confidence by their n).

Output: data/fingerprint_evidence.parquet
Columns: horizon, vol_b, mom_sign, range_b, vr_sign, n, beat_rate, beat_rate_shrunk

Usage:  python backend/build_fingerprints_historical.py --start S --end E  |  --validate DATE
"""
import argparse
import os

import numpy as np

from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange
from train_beat_classifier import (build_beat_features, beat_labels, FEATURE_NAMES,
                                    _ohlc_for_dates, resolve_dates)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "fingerprint_evidence.parquet")
HORIZONS = (1, 3, 5, 7, 10, 15)
SHRINK_K = 30.0   # Laplace strength: a cell with n=K is pulled halfway to 0.5


def _bucket(x, edges):
    return int(np.searchsorted(edges, x))


def fingerprints(X, y):
    """-> dict keyed (vol_b, mom_sign, range_b, vr_sign) -> [n, wins]."""
    iv = FEATURE_NAMES.index("rv_short"); im = FEATURE_NAMES.index("mom_20")
    ir = FEATURE_NAMES.index("range_pos"); ivr = FEATURE_NAMES.index("variance_ratio")
    vol_e = np.quantile(X[:, iv], [0.33, 0.66])
    rng_e = np.array([0.33, 0.66])
    cells = {}
    for i in range(len(y)):
        key = (_bucket(X[i, iv], vol_e), int(np.sign(X[i, im])),
               _bucket(X[i, ir], rng_e), int(np.sign(X[i, ivr])))
        n, w = cells.get(key, (0, 0))
        cells[key] = (n + 1, w + int(y[i]))
    return cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int)
    args = ap.parse_args()
    import pandas as pd
    dates, save = resolve_dates(args)
    if not dates:
        ap.error("provide --start/--end, --validate DATE, or --days N")

    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 400:
        print("Not enough bars."); return
    X = build_beat_features(O, H, L, C, T)

    rows = []
    for h in HORIZONS:
        y = beat_labels(O, C, h)
        Xs, ys = X[:-1], y[1:]          # ANTI-LEAKAGE (see beat head): features[t] -> window t+1
        m = ys >= 0
        if m.sum() < 200:
            continue
        cells = fingerprints(Xs[m], ys[m])
        for (vb, ms, rb, vs), (n, w) in cells.items():
            rate = w / n
            shrunk = (w + 0.5 * SHRINK_K) / (n + SHRINK_K)   # toward 0.5
            rows.append(dict(horizon=h, vol_b=vb, mom_sign=ms, range_b=rb, vr_sign=vs,
                             n=n, beat_rate=round(rate, 4), beat_rate_shrunk=round(shrunk, 4)))
    if not rows:
        print("No cells."); return
    df = pd.DataFrame(rows)
    print(f"\n{len(df)} fingerprint cells over {len(dates)} day(s). Strongest (n>=100):")
    strong = df[df.n >= 100].copy()
    strong["edge"] = (strong.beat_rate_shrunk - 0.5).abs()
    for _, r in strong.sort_values("edge", ascending=False).head(8).iterrows():
        print(f"  h={int(r.horizon):>2} vol={int(r.vol_b)} mom={int(r.mom_sign):+d} "
              f"rng={int(r.range_b)} vr={int(r.vr_sign):+d}: {r.beat_rate_shrunk*100:.1f}% (n={int(r.n)})")
    if save:
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_parquet(OUT_PATH, index=False)
        print(f"\nWrote {len(df)} cells -> {OUT_PATH}")
    else:
        print(f"\n[validate] {len(df)} cells (not written)")


if __name__ == "__main__":
    main()
