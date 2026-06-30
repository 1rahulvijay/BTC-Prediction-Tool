"""
path_plan_verifier.py — grade the Layer-2 path plan with the EXACT serving function (read-only).
=================================================================================================
The path head has per-target training AUCs, but before betting on its CHOP/TREND + touch + round-trip
calls you want the *composed plan* graded out-of-sample. This replays the production serving function
`price_to_beat._predict_path_plan` over the 360-day research matrix (the same code that runs live) and
grades each frozen plan against the realized window high/low:

  • P(move ≥ $50)   — predicted mean vs realized touch rate (calibration) + Brier
  • round-trip      — predicted P(touch both ±$50) vs realized + Brier + AUC
  • CHOP/TREND style — of CHOP plans, how many round-tripped; of TREND, how many did not (the trade rule)
  • high/low band   — coverage of the realized extreme inside the predicted band (target ≈ the band's nominal)
  • net-drift       — |net move| MAE in dollars

It does NOT touch the live tracker (offline, deterministic — the serving logic is a pure function of the
keepers, so this == what live would have produced). For ONGOING live recording, wire grading into the
tracker's _resolve (separate, gated step).

Usage:  python backend/path_plan_verifier.py [--oos 0.2]    # grade on the last 20% (out-of-sample tail)
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA, "research_matrix_1m.parquet")
sys.path.insert(0, HERE)


def _brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _auc(p, y):
    y = np.asarray(y); p = np.asarray(p)
    if len(np.unique(y)) < 2:
        return float("nan")
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos", type=float, default=0.2, help="fraction held out as the out-of-sample tail")
    ap.add_argument("--sample", type=int, default=40000, help="max rows to score per horizon (speed)")
    a = ap.parse_args()

    # Import the REAL serving function for parity. price_to_beat has no DB side-effects at import.
    try:
        from price_to_beat import _predict_path_plan, _load_path_forecaster
    except Exception as e:
        print(f"cannot import serving function: {e}"); return
    bundle = _load_path_forecaster()
    if not bundle:
        print("no path_forecaster.pkl loaded (train it first, or version/units mismatch)."); return
    if bundle.get("threshold_units") != "usd":
        print(f"path bundle is not usd-barriers (threshold_units={bundle.get('threshold_units')}) — stale artifact."); return
    feats = bundle.get("features") or []
    print(f"path bundle: version={bundle.get('version')} features={feats}")

    if not os.path.exists(MATRIX):
        print(f"no {MATRIX}"); return
    df = pd.read_parquet(MATRIX).reset_index(drop=True)
    missing = [f for f in feats if f not in df.columns]
    if missing:
        print(f"matrix missing path features: {missing}"); return
    n = len(df)
    cut = int(n * (1 - a.oos))  # grade only the held-out tail (the model never trained on it)
    print(f"matrix rows={n:,} · grading the last {int(a.oos*100)}% (rows {cut:,}..{n:,})\n")

    high = df["high"].to_numpy(float); low = df["low"].to_numpy(float); close = df["close"].to_numpy(float)

    for h in (5, 15):
        # realized window extremes over the NEXT h 1m bars (what the path targets are built from)
        recs = []
        idxs = range(cut, n - h)
        if a.sample and (n - h - cut) > a.sample:
            idxs = np.linspace(cut, n - h - 1, a.sample).astype(int)
        for i in idxs:
            c = close[i]
            if not np.isfinite(c) or c <= 0:
                continue
            keepers = {f: float(df[f].iat[i]) for f in feats}
            if not all(np.isfinite(v) for v in keepers.values()):
                continue
            plan = _predict_path_plan(bundle, h, keepers, c)
            if not plan:
                continue
            wh = float(np.max(high[i + 1:i + 1 + h])); wl = float(np.min(low[i + 1:i + 1 + h]))
            up_usd = wh - c; dn_usd = c - wl
            net_usd = abs(close[i + h] - c)
            recs.append({
                "p_move50": plan["p_move_50"], "p_rt": plan.get("p_roundtrip"),
                "style": plan.get("style"), "net_pred": plan.get("net_move_usd"),
                "hi_band": plan["high_band"], "lo_band": plan["low_band"],
                "act_move50": int(up_usd >= 50 or dn_usd >= 50),
                "act_rt": int(up_usd >= 50 and dn_usd >= 50),
                "act_high": c + up_usd, "act_low": c - dn_usd, "act_net": net_usd,
            })
        if not recs:
            print(f"== {h}m: no scorable rows ==\n"); continue
        r = pd.DataFrame(recs)
        print(f"== {h}m  (n={len(r):,}) ==")
        # touch / move50 calibration
        print(f"  P(move≥$50): predicted {r['p_move50'].mean():.3f} vs realized {r['act_move50'].mean():.3f} "
              f"· Brier {_brier(r['p_move50'], r['act_move50']):.3f} · AUC {_auc(r['p_move50'], r['act_move50']):.3f}")
        # round-trip
        rr = r[r["p_rt"].notna()]
        if len(rr):
            print(f"  round-trip:  predicted {rr['p_rt'].mean():.3f} vs realized {rr['act_rt'].mean():.3f} "
                  f"· Brier {_brier(rr['p_rt'], rr['act_rt']):.3f} · AUC {_auc(rr['p_rt'], rr['act_rt']):.3f}")
        # style → the actual trade rule: CHOP should round-trip, TREND should not
        st = r[r["style"].notna()]
        if len(st):
            print("  style → realized round-trip rate (CHOP should be HIGH, one_sided/quiet LOW):")
            for s in ("two_sided", "mixed", "one_sided", "quiet"):
                g = st[st["style"] == s]
                if len(g):
                    print(f"     {s:10} n={len(g):6,}  round-trip {g['act_rt'].mean():.3f}  move50 {g['act_move50'].mean():.3f}")
        # band coverage
        hi_cov = float(np.mean([(b[0] <= ah <= b[1]) for b, ah in zip(r["hi_band"], r["act_high"])]))
        lo_cov = float(np.mean([(b[0] <= al <= b[1]) for b, al in zip(r["lo_band"], r["act_low"])]))
        print(f"  band coverage: high {hi_cov:.2f} · low {lo_cov:.2f}  (predicted band should contain the realized extreme)")
        # net-drift MAE
        nd = r[r["net_pred"].notna()]
        if len(nd):
            print(f"  net-drift |move| MAE: ${np.mean(np.abs(nd['net_pred'] - nd['act_net'])):.1f} "
                  f"(predicted mean ${nd['net_pred'].mean():.0f} vs realized ${nd['act_net'].mean():.0f})")
        print()

    print("READ: style is the trade rule — CHOP (two_sided) should round-trip materially more than TREND "
          "(one_sided/quiet). If that separation holds out-of-sample, the CHOP/TREND call is trustworthy for "
          "fade-vs-ride. Band coverage near nominal + low Brier = calibrated touch/round-trip odds.")


if __name__ == "__main__":
    main()
