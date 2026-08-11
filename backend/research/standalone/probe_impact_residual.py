"""
probe_impact_residual.py - standalone leak-free probe for market-impact / turbulence features.
=============================================================================================
Tests whether the social-media "market impact / square-root law / turbulence energy" ideas add
anything on BTC -- the DISCIPLINED way: probe the feature OFFLINE before building any head
(operating-manual rule #4). The screenshots say "build a Market Impact Head, then use it"; that
is backwards and how you ship leakage. We measure first; we build only what clears a gate.

Reads data/research_matrix_1m.parquet (a FILE -> safe while the app holds the DuckDB lock).
Leak-free: every candidate feature uses ONLY data known at close[t]; labels are the matrix's
forward columns (ret_5m / future_abs_move_5m). A feature NEVER reads an outcome bar.

Judged TWO ways:
  1) DIRECTION (sign of ret_5m)  -> expected ~0.50 (the proven ceiling; this reproduces WHY the
     live win-rate panel sits at ~48%).
  2) TIMING |move| (future_abs_move_5m top-quartile) -> the only place an edge has ever lived
     (realized_vol/range_compression AUC 0.57-0.64).
A feature KEEPS only if move_AUC >= 0.55 AND it lifts test-AUC over the incumbent rv_15m by
>= +0.005. A KEEP is a SELECTIVITY candidate only; trading it still needs the separate
cost-survival test (direction is a coin-flip, so timing alone is not a tradeable side).

Usage:
  python backend/research/standalone/probe_impact_residual.py            # full matrix
  python backend/research/standalone/probe_impact_residual.py --days 30  # recent window only
  python backend/research/standalone/probe_impact_residual.py --selftest # validate the logic on synthetic data
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

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
except Exception as e:  # pragma: no cover
    print(f"sklearn required: {e}")
    sys.exit(1)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
EPS = 1e-9


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """ALL features known at close[t] (leak-free). Returns a feature frame aligned to df."""
    out = pd.DataFrame(index=df.index)
    close = df["close"].astype(float)
    vol = df["volume"].astype(float) if "volume" in df else pd.Series(np.nan, index=df.index)

    # signed taker flow at bar t (known at close[t])
    if "taker_buy" in df and "taker_sell" in df:
        signed_flow = df["taker_buy"].astype(float) - df["taker_sell"].astype(float)
    elif "cvd_change" in df:
        signed_flow = df["cvd_change"].astype(float)
    else:
        signed_flow = df.get("delta", pd.Series(0.0, index=df.index)).astype(float)

    ret_1m = close.pct_change()                                   # realized in bar t (known at close[t])
    rv15 = df.get("rv_15m", pd.Series(np.nan, index=df.index)).astype(float)
    rv60 = df.get("rv_60m", pd.Series(np.nan, index=df.index)).astype(float)

    # --- market impact (square-root law): expected move from flow, vs realized ---
    flow_share = (signed_flow.abs() / (vol.abs() + EPS)).clip(upper=1.0)
    expected_impact = rv15 * np.sqrt(flow_share) * np.sign(signed_flow)   # k folded into rv units
    impact_residual = ret_1m - expected_impact                   # signed: realized minus flow-explained
    out["impact_residual_signed"] = impact_residual              # for DIRECTION
    out["impact_residual_abs"] = impact_residual.abs()           # for |move| (abnormal impact magnitude)

    # --- turbulence energy = realized variance * volume ---
    out["energy_1m"] = (ret_1m ** 2) * vol

    # --- shock front = recent vol spike (short vs long realized vol) ---
    out["shock_front"] = rv15 / (rv60 + EPS)

    # --- reversal pressure = short-vs-long momentum conflict ---
    mom_s = close - close.shift(5)
    mom_l = close - close.shift(30)
    out["reversal_pressure_signed"] = mom_s - mom_l                              # signed, for DIRECTION
    out["reversal_pressure_abs"] = (np.sign(mom_s) != np.sign(mom_l)).astype(float)  # conflict flag, for |move|

    # incumbent carrier (the bar to beat)
    out["rv_15m"] = rv15
    return out


def _auc(y: np.ndarray, score: np.ndarray) -> float:
    y = np.asarray(y, float); score = np.asarray(score, float)
    m = np.isfinite(score) & np.isfinite(y)
    if m.sum() < 50 or len(np.unique(y[m])) < 2:
        return float("nan")
    try:
        return float(roc_auc_score(y[m], score[m]))
    except Exception:
        return float("nan")


def _incremental_auc(base_cols, feat_col, X: pd.DataFrame, y_move: np.ndarray):
    """Temporal 70/30: does adding feat_col to the incumbent lift test-AUC on |move|? (leak-free in time)."""
    n = len(X); split = int(n * 0.7)

    def fit_auc(cols):
        Xtr = X.iloc[:split][cols].to_numpy(float); Xte = X.iloc[split:][cols].to_numpy(float)
        ytr = y_move[:split]; yte = y_move[split:]
        mtr = np.isfinite(Xtr).all(1) & np.isfinite(ytr)
        mte = np.isfinite(Xte).all(1) & np.isfinite(yte)
        if mtr.sum() < 200 or mte.sum() < 200 or len(np.unique(ytr[mtr])) < 2:
            return float("nan")
        mu = Xtr[mtr].mean(0); sd = Xtr[mtr].std(0) + EPS
        clf = LogisticRegression(max_iter=200)
        clf.fit((Xtr[mtr] - mu) / sd, ytr[mtr])
        p = clf.predict_proba((Xte[mte] - mu) / sd)[:, 1]
        return _auc(yte[mte], p)

    return fit_auc(base_cols), fit_auc(base_cols + [feat_col])


def evaluate(df: pd.DataFrame) -> dict:
    """PURE, unit-testable core. df = research-matrix slice. Returns result rows."""
    feats = build_features(df)

    # forward labels (already leak-free in the matrix)
    if "future_abs_move_5m" in df:
        amove = df["future_abs_move_5m"].astype(float)
    elif "ret_5m" in df:
        amove = df["ret_5m"].abs().astype(float)
    else:
        amove = ((df["future_close_5m"].astype(float) - df["close"].astype(float))
                 / df["close"].astype(float)).abs()
    if "ret_5m" in df:
        ret5 = df["ret_5m"].astype(float)
    else:
        ret5 = ((df["future_close_5m"].astype(float) - df["close"].astype(float))
                / df["close"].astype(float))

    valid = np.isfinite(amove.to_numpy()) & np.isfinite(ret5.to_numpy())
    feats = feats[valid].reset_index(drop=True)
    amove = amove[valid].reset_index(drop=True).to_numpy()
    ret5 = ret5[valid].reset_index(drop=True).to_numpy()

    thr = float(np.nanquantile(amove, 0.75))
    y_move = (amove >= thr).astype(float)            # P(big move) = top-quartile |move|
    dir_mask = ret5 != 0
    y_dir = (ret5 > 0).astype(float)

    candidates = [
        ("impact_residual",   "impact_residual_signed",   "impact_residual_abs"),
        ("energy_1m",         None,                       "energy_1m"),
        ("shock_front",       None,                       "shock_front"),
        ("reversal_pressure", "reversal_pressure_signed", "reversal_pressure_abs"),
    ]
    rv = feats["rv_15m"].to_numpy(float)
    rows = []
    for name, sc_dir, sc_move in candidates:
        move_auc = _auc(y_move, feats[sc_move].to_numpy(float)) if sc_move else float("nan")
        dir_auc = (_auc(y_dir[dir_mask], feats[sc_dir].to_numpy(float)[dir_mask])
                   if sc_dir else float("nan"))
        b, a = _incremental_auc(["rv_15m"], sc_move, feats, y_move) if sc_move else (float("nan"),) * 2
        lift = (a - b) if (np.isfinite(a) and np.isfinite(b)) else float("nan")
        keep = bool(np.isfinite(move_auc) and move_auc >= 0.55 and np.isfinite(lift) and lift >= 0.005)
        rows.append({"name": name, "move_auc": move_auc, "dir_auc": dir_auc,
                     "base": b, "aug": a, "lift": lift,
                     "verdict": "KEEP" if keep else "reject/redundant"})

    return {"n": int(len(feats)), "move_thr": thr,
            "rv15_move_auc": _auc(y_move, rv),
            "rv15_dir_auc": _auc(y_dir[dir_mask], rv[dir_mask]),
            "rows": rows}


def _f(x, nd=3):
    return f"{x:.{nd}f}" if np.isfinite(x) else "  -  "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="use only the last N days (0=all)")
    args = ap.parse_args()
    if not os.path.exists(MATRIX):
        print(f"ERROR: matrix not found: {MATRIX}")
        return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    if args.days and "ts_ms" in df:
        cut = df["ts_ms"].max() - args.days * 86400_000
        df = df[df["ts_ms"] >= cut].reset_index(drop=True)
    res = evaluate(df)

    print("=" * 80)
    print(f"MARKET-IMPACT / TURBULENCE FEATURE PROBE  (n={res['n']:,} 1m bars, leak-free)")
    print("=" * 80)
    print(f"Incumbent rv_15m:  DIRECTION-AUC={_f(res['rv15_dir_auc'])} (~0.50 == the ceiling)   "
          f"|move|-AUC={_f(res['rv15_move_auc'])}")
    print(f"|move| label = future_abs_move_5m >= top-quartile ({res['move_thr']:.5f})")
    print()
    print(f"  {'feature':<20}{'dir_AUC':>9}{'move_AUC':>10}{'rv15->+feat(test)':>20}{'lift':>9}  verdict")
    for r in res["rows"]:
        prog = f"{_f(r['base'])}->{_f(r['aug'])}"
        print(f"  {r['name']:<20}{_f(r['dir_auc']):>9}{_f(r['move_auc']):>10}"
              f"{prog:>20}{(('%+.3f' % r['lift']) if np.isfinite(r['lift']) else '  -  '):>9}  {r['verdict']}")
    print()
    print("dir_AUC ~0.50 across the board == the same coin-flip the live panel shows. These features")
    print("can only ever help SELECTIVITY (|move| timing), never the side.")
    print("KEEP gate: move_AUC >= 0.55 AND lift over rv_15m >= +0.005. A KEEP still needs the")
    print("cost-survival test before it is tradeable.")


def selftest():
    rng = np.random.default_rng(0)
    n = 6000
    close = 60000 + np.cumsum(rng.normal(0, 5, n))
    rv = np.abs(rng.normal(0, 1, n)) + 0.5
    fut_abs = np.abs(rv * 100 + rng.normal(0, 10, n))    # |move| driven by rv -> incumbent timing works
    ret5 = rng.normal(0, 1, n)                            # direction RANDOM -> AUC ~0.50 (the ceiling)
    df = pd.DataFrame({
        "ts_ms": np.arange(n) * 60000, "close": close,
        "volume": np.abs(rng.normal(100, 20, n)),
        "taker_buy": np.abs(rng.normal(50, 10, n)), "taker_sell": np.abs(rng.normal(50, 10, n)),
        "rv_15m": rv, "rv_60m": np.abs(rng.normal(0, 1, n)) + 0.5,
        "future_abs_move_5m": fut_abs, "ret_5m": ret5,
    })
    res = evaluate(df)
    assert abs(res["rv15_dir_auc"] - 0.5) < 0.06, f"dir AUC not ~0.5: {res['rv15_dir_auc']}"
    assert res["rv15_move_auc"] > 0.70, f"rv timing should work: {res['rv15_move_auc']}"
    assert len(res["rows"]) == 4
    # planted direction is pure noise -> no candidate should fake a directional edge
    for r in res["rows"]:
        if np.isfinite(r["dir_auc"]):
            assert abs(r["dir_auc"] - 0.5) < 0.08, f"{r['name']} fake dir edge: {r['dir_auc']}"
    print("probe_impact_residual self-test: ceiling reproduced (dir~0.50), rv timing works, "
          "no fake direction edge. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
