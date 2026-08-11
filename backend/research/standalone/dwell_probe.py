"""
dwell_probe.py — is "how much time price stays UP/DOWN vs the beat line" predictable? (offline)
================================================================================================
Price-to-beat sub-question #3. For each window-open t, the line = close[t] (price to beat). Over the
next h bars, dwell_up = fraction of bars with close > line. We test TWO separable questions, the same
direction-vs-timing split that holds everywhere else in this app:

  A) SIDE  — does dwell_up > 0.5 (price spends MORE time above)?  -> expected ~coin-flip (0.50),
     because which side it favors is the same dead direction problem.
  B) COMMITMENT — is the window strongly ONE-SIDED (max(dwell_up, 1-dwell_up) >= 0.8, i.e. price
     commits to one side instead of chopping across the line)?  -> may be predictable from vol/trend
     (a dwell-time cousin of P(hold)); direction-INVARIANT (doesn't say WHICH side).

Leak-free: features end at bar t; dwell measured over t+1..t+h; purged. Uses research_matrix_1m.parquet.
HONEST FRAME: COMMITMENT being predictable is a SELECTIVITY signal (when the window trends vs chops),
NOT a directional edge. SIDE being ~0.50 just reconfirms direction is dead.

Usage:  python backend/research/standalone/dwell_probe.py            (5m, 15m)
        python backend/research/standalone/dwell_probe.py --selftest
"""

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap

import argparse
import os
import sys

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
HORIZONS = (5, 15)
TIMING = ["rv_15m", "rv_30m", "rv_60m", "log_count", "compression_ratio", "shock_magnitude",
          "vpin_15m", "vpin"]                              # vol/timing keepers (for COMMITMENT)
DIRECTIONAL = ["perp_spot_basis_bps", "cvd_divergence"]   # add for SIDE a fair shot


def dwell_targets(close, h):
    """For each t: line=close[t]; dwell_up = mean(close[t+1..t+h] > close[t]). Returns
    (dwell_up, side_up_label, committed_label) with -1 tails. committed = one-sided >= 0.8."""
    n = len(close)
    dwell = np.full(n, np.nan)
    end = n - h
    for t in range(end):
        seg = close[t + 1:t + 1 + h]
        dwell[t] = float(np.mean(seg > close[t]))
    side = np.full(n, -1); committed = np.full(n, -1)
    valid = ~np.isnan(dwell)
    side[valid] = (dwell[valid] > 0.5).astype(int)
    committed[valid] = (np.maximum(dwell[valid], 1.0 - dwell[valid]) >= 0.8).astype(int)
    return dwell, side, committed


def _auc(X, y, frac=0.7):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    m = (y >= 0) & np.all(np.isfinite(X), axis=1)
    X, y = X[m], y[m]
    n = len(y); cut = int(n * frac)
    if n < 400 or len(np.unique(y[:cut])) < 2 or len(np.unique(y[cut:])) < 2:
        return None, None
    sc = StandardScaler().fit(X[:cut])
    lr = LogisticRegression(max_iter=300, class_weight="balanced").fit(sc.transform(X[:cut]), y[:cut])
    p = lr.predict_proba(sc.transform(X[cut:]))[:, 1]
    return float(roc_auc_score(y[cut:], p)), int(n - cut)


def run():
    import pandas as pd
    if not os.path.exists(MATRIX):
        sys.exit(f"missing {MATRIX} — build it via build_research_matrix.py first.")
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    timing = [c for c in TIMING if c in df.columns]
    direc = [c for c in DIRECTIONAL if c in df.columns]
    if "close" not in df.columns:
        sys.exit("matrix missing 'close'")
    df = df.dropna(subset=timing + ["close"]).reset_index(drop=True)
    close = df["close"].to_numpy(np.float64)
    Xt = df[timing].to_numpy(np.float64)
    Xall = df[timing + direc].to_numpy(np.float64) if direc else Xt
    print(f"matrix {df.shape} | timing {timing} | directional {direc or 'none'}\n")
    print(f"  {'h':>3} {'mean_dwell_up':>13} {'committed_rate':>14} {'SIDE_auc(dir)':>14} "
          f"{'COMMIT_auc(vol)':>16}")
    for h in HORIZONS:
        dwell, side, committed = dwell_targets(close, h)
        v = ~np.isnan(dwell)
        side_auc, _ = _auc(Xall, side)                     # directional features get their shot
        commit_auc, n = _auc(Xt, committed)                # vol/timing features
        print(f"  {h:>3}m {np.nanmean(dwell):>13.3f} {committed[v].mean():>14.3f} "
              f"{(f'{side_auc:.3f}' if side_auc else '—'):>14} "
              f"{(f'{commit_auc:.3f}' if commit_auc else '—'):>16}")
    print("\nREAD: SIDE_auc ~0.50 = which-side dwell is the same dead direction problem (expected).")
    print("COMMIT_auc >= 0.55 = whether the window TRENDS vs CHOPS is predictable from vol -> a")
    print("selectivity signal (when a price-to-beat line will be decisively held/broken vs whipsawed),")
    print("direction-invariant. It does NOT tell you WHICH side; pair with P(hold) for the side.")


def selftest():
    rng = np.random.default_rng(0)
    # dwell_targets correctness: a strictly rising series -> dwell_up=1, committed=1, side=1
    up = np.arange(50, dtype=float) + 100
    d, s, c = dwell_targets(up, 5)
    assert d[0] == 1.0 and s[0] == 1 and c[0] == 1 and np.isnan(d[-1])
    down = 200 - np.arange(50, dtype=float)
    d2, s2, c2 = dwell_targets(down, 5)
    assert d2[0] == 0.0 and s2[0] == 0 and c2[0] == 1            # one-sided DOWN -> committed, side=0
    # a flat oscillation AROUND the line (close[0]=line=100, then alternates above/below) ->
    # dwell ~0.5, NOT committed
    osc = np.concatenate([[100.0], np.array([101.0, 99.0] * 15)])
    d3, s3, c3 = dwell_targets(osc, 6)
    assert abs(d3[0] - 0.5) < 1e-9 and c3[0] == 0, f"oscillating should be ~0.5/uncommitted, got {d3[0]}"
    # _auc: planted vol->commitment signal is learned; random side ~0.5
    N = 3000
    vol = rng.uniform(0, 1, (N, 3))
    commit = (vol[:, 0] > np.quantile(vol[:, 0], 0.6)) ^ (rng.random(N) < 0.2)
    a, n = _auc(vol, commit.astype(int))
    assert a and a > 0.6, f"planted commitment signal should be learned, got {a}"
    ar, _ = _auc(rng.normal(0, 1, (N, 3)), rng.integers(0, 2, N))
    assert ar and abs(ar - 0.5) < 0.08, f"random must be ~0.5, got {ar}"
    print("dwell_probe self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
