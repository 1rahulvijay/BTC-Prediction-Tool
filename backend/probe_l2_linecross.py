"""
probe_l2_linecross.py - does live L2 microstructure predict the NEXT-FEW-SECONDS direction on BTC?
===================================================================================================
The quant literature's ONLY robust short-horizon edge lives at 0.1-3s in the order book (OFI / book
imbalance / microprice), NOT at the 5-15min direction horizon (that's a coin-flip, proven ~13 ways).
This probe tests the literature claim on YOUR OWN recorded L2 and, if it holds, feeds the line-cross /
P(Hold) decision (the edge gate) -- it is NOT a 5m direction predictor.

HONEST PRIOR: BTC's edge is thin even here. arXiv 2602.00776 found a BTC 3s L1+OFI model did NOT survive
costs (taker p=0.75, maker p=0.11); the survivors were illiquid small-caps. So expect marginal AUC that
decays fast with horizon. This probe tells us the truth on our data, leak-free.

Parity (manual rule #3): features are the EXACT per-snapshot columns microstructure_recorder.py computes
live (ofi/obi_20/obi_near/microprice/bid_usd/ask_usd/spread_bps), so train == serve by construction.
Reads data/microstructure.duckdb READ-ONLY (the recorder's own DB; separate from analytics.duckdb). If the
recorder holds the write lock, it exits cleanly -- the recorder is restart-safe, so stop it briefly and
rerun, or run during a recorder restart.

Leak-free: features at snapshot t; label = sign(mid[t+N s] - mid[t]), strictly future, matched by ts_ms.

Usage:
  python backend/probe_l2_linecross.py
  python backend/probe_l2_linecross.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
except Exception as e:  # pragma: no cover
    print(f"sklearn required: {e}")
    sys.exit(1)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "microstructure.duckdb")
HORIZONS_S = (5, 15, 30, 60)     # SHORT, in SECONDS (where the literature edge lives)
MIN_ROWS = 3600                  # ~1h at 1s cadence to even attempt; days is what you want
EPS = 1e-9


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-snapshot features, all known at t -> identical to the recorder's live columns (parity)."""
    out = pd.DataFrame(index=df.index)
    mid = df["mid"].astype(float)
    out["ofi"] = df["ofi"].astype(float)                                  # Cont order-flow imbalance
    out["obi_20"] = df["obi_20"].astype(float)                           # top-20 book imbalance
    out["obi_near"] = df["obi_near"].astype(float)                       # near-mid book imbalance
    out["microprice_dev_bps"] = (df["microprice"].astype(float) - mid) / mid * 1e4  # >0 == upward pressure
    bid = df["bid_usd"].astype(float); ask = df["ask_usd"].astype(float)
    out["depth_asym"] = (bid - ask) / (bid + ask + EPS)                  # depth-side asymmetry
    out["spread_bps"] = df["spread_bps"].astype(float)                  # control (not directional)
    return out


def forward_dir(df: pd.DataFrame, n_sec: int):
    """y = sign(mid at ~t+n_sec - mid at t), matched by ts_ms (handles 1s cadence + gaps). Leak-free."""
    base = df[["ts_ms", "mid"]].sort_values("ts_ms").reset_index(drop=True)
    fut = base.copy()
    fut["ts_ms"] = fut["ts_ms"] - n_sec * 1000          # shift so merge_asof finds the t+n_sec snapshot
    fut = fut.rename(columns={"mid": "mid_fut"}).sort_values("ts_ms").reset_index(drop=True)
    merged = pd.merge_asof(base, fut, on="ts_ms", direction="nearest", tolerance=2000)
    ret = merged["mid_fut"].to_numpy() - merged["mid"].to_numpy()
    return ret


def auc_table(feats: pd.DataFrame, y: np.ndarray) -> dict:
    """PURE core. Univariate AUC per feature + a combined temporal-70/30 logistic AUC."""
    rows = {}
    for c in feats.columns:
        s = feats[c].to_numpy(float)
        m = np.isfinite(s) & np.isfinite(y)
        rows[c] = (float(roc_auc_score(y[m], s[m]))
                   if m.sum() >= 50 and len(np.unique(y[m])) == 2 else float("nan"))
    # combined logistic, temporal split
    X = feats.to_numpy(float)
    n = len(X); split = int(n * 0.7)
    mtr = np.isfinite(X[:split]).all(1) & np.isfinite(y[:split])
    mte = np.isfinite(X[split:]).all(1) & np.isfinite(y[split:])
    combined = float("nan")
    if mtr.sum() >= 200 and mte.sum() >= 200 and len(np.unique(y[:split][mtr])) == 2:
        Xtr = X[:split][mtr]; Xte = X[split:][mte]
        mu = Xtr.mean(0); sd = Xtr.std(0) + EPS
        clf = LogisticRegression(max_iter=200).fit((Xtr - mu) / sd, y[:split][mtr])
        p = clf.predict_proba((Xte - mu) / sd)[:, 1]
        if len(np.unique(y[split:][mte])) == 2:
            combined = float(roc_auc_score(y[split:][mte], p))
    rows["__combined__"] = combined
    return rows


def evaluate(df: pd.DataFrame) -> dict:
    feats_all = build_features(df)
    res = {"n": int(len(df)), "horizons": {}}
    for n_sec in HORIZONS_S:
        ret = forward_dir(df, n_sec)
        valid = np.isfinite(ret) & (ret != 0.0)
        y = (ret > 0).astype(float)
        res["horizons"][n_sec] = auc_table(feats_all[valid].reset_index(drop=True), y[valid])
        res["horizons"][n_sec]["_n"] = int(valid.sum())
    return res


def _f(x):
    return f"{x:.3f}" if (isinstance(x, float) and np.isfinite(x)) else "  -  "


def _load():
    import duckdb
    if not os.path.exists(DB_PATH):
        print(f"No L2 DB yet: {DB_PATH}\n  Start the recorder (start_microstructure_recorder.bat) and let it"
              f" accrue (target: days of 1s snapshots).")
        return None
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        print(f"microstructure.duckdb is locked by the running recorder ({str(e)[:80]}).\n"
              f"  The recorder is restart-safe: stop it briefly and rerun, or run during a restart.")
        return None
    try:
        df = conn.execute("SELECT * FROM l2_snapshots ORDER BY ts_ms").df()
    finally:
        conn.close()
    return df


def main():
    argparse.ArgumentParser().parse_args()
    df = _load()
    if df is None:
        return
    span_h = (df["ts_ms"].max() - df["ts_ms"].min()) / 3600_000 if len(df) else 0
    print("=" * 86)
    print(f"L2 LINE-CROSS PROBE  (n={len(df):,} 1s snapshots, span {span_h:.1f}h, leak-free)")
    print("=" * 86)
    if len(df) < MIN_ROWS:
        print(f"INSUFFICIENT: {len(df):,} rows (<{MIN_ROWS}). Let the recorder accrue more (days ideal).")
        return
    if span_h < 24:
        print(f"NOTE: only {span_h:.1f}h of data -- AUCs are noisy; treat as a smoke test until you have days.")
    res = evaluate(df)
    feat_cols = ["ofi", "obi_20", "obi_near", "microprice_dev_bps", "depth_asym", "spread_bps"]
    print()
    print(f"  {'horizon':>8}{'n':>9}  " + "".join(f"{c[:11]:>12}" for c in feat_cols) + f"{'COMBINED':>11}  verdict")
    for n_sec, r in res["horizons"].items():
        cells = "".join(f"{_f(r.get(c, float('nan'))):>12}" for c in feat_cols)
        comb = r.get("__combined__", float("nan"))
        verdict = "SIGNAL" if (np.isfinite(comb) and comb >= 0.55) else "~coin-flip"
        print(f"  {str(n_sec)+'s':>8}{r['_n']:>9}  {cells}{_f(comb):>11}  {verdict}")
    print()
    print("Read: AUC>0.55 == predictive of the NEXT-N-second direction. Expect the edge (if any) at 5-15s,")
    print("decaying toward 0.50 by 60s (literature). A SIGNAL here feeds line-cross / P(Hold) in a round's")
    print("final seconds -- NOT a 5m direction call. If even 5s is ~0.50, BTC is efficient at this scale too")
    print("(consistent with arXiv 2602.00776's BTC after-cost null). Next gate if SIGNAL: cost-survival.")


def selftest():
    rng = np.random.default_rng(3)
    n = 4000
    # (a) auc_table: planted ofi predicts y; spread is noise
    sig = rng.normal(0, 1, n)
    y = (sig > 0).astype(float)
    feats = pd.DataFrame({
        "ofi": sig + rng.normal(0, 0.4, n),          # informative
        "obi_20": rng.normal(0, 1, n),               # noise
        "obi_near": rng.normal(0, 1, n),
        "microprice_dev_bps": rng.normal(0, 1, n),
        "depth_asym": rng.normal(0, 1, n),
        "spread_bps": rng.normal(0, 1, n),           # control
    })
    t = auc_table(feats, y)
    assert t["ofi"] > 0.70, f"planted ofi not detected: {t['ofi']}"
    assert abs(t["spread_bps"] - 0.5) < 0.06, f"noise not ~0.5: {t['spread_bps']}"
    assert np.isfinite(t["__combined__"]) and t["__combined__"] > 0.65, t["__combined__"]
    # (b) forward_dir: a strictly increasing mid -> every future move is UP
    df = pd.DataFrame({"ts_ms": np.arange(500) * 1000, "mid": 60000 + np.arange(500) * 1.0})
    ret = forward_dir(df, 5)
    fin = ret[np.isfinite(ret)]
    assert (fin > 0).mean() > 0.99, f"forward_dir sign wrong: {(fin > 0).mean()}"
    print("probe_l2_linecross self-test: ofi detected, noise ~0.5, forward_dir leak-free sign OK. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
