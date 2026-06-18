#!/usr/bin/env python
"""
anchor_multihead_bakeoff.py — multi-head model bakeoff on the Binance anchor-beat dataset.
==========================================================================================
Reads data/research/binance_updown_features.parquet (+ manifest) and runs, PER HORIZON, a
round-grouped purged walk-forward bakeoff for every head:

  classification heads:  label_current_side_wins (P(Hold)), label_line_cross_before_expiry,
                         label_big_round_move_10bps/_20bps, label_up_win
  regression heads:      expiry_return_bps, max_up_bps, max_down_bps, round_range_bps, log_quote_volume
  quantile bands:        max_up_bps / max_down_bps / round_range_bps (q10/q50/q90 coverage + pinball)
  baselines:             majority base-rate, and analytic_p_hold (the barrier) for P(Hold)

ALL metrics stream to CSV (per fold + a summary):
  data/research/bakeoff_classification.csv
  data/research/bakeoff_regression.csv
  data/research/bakeoff_quantile.csv
  data/research/bakeoff_summary.csv

Walk-forward is GROUPED BY round_id and ordered by round_start (no round leaks train->test, time-ordered).
HONEST: this tests BTC fair-value heads only; tradeable EV still needs the Polymarket recorder's ask.

Usage:  python backend/research/anchor_multihead_bakeoff.py [--folds 4] [--max-train 200000]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RES = os.path.join(ROOT, "data", "research")
FEATURES = os.path.join(RES, "binance_updown_features.parquet")
MANIFEST = os.path.join(RES, "binance_updown_feature_manifest.json")

CLS_TARGETS = ["label_current_side_wins", "label_line_cross_before_expiry",
               "label_big_round_move_10bps", "label_big_round_move_20bps", "label_up_win"]
REG_TARGETS = ["expiry_return_bps", "max_up_bps", "max_down_bps", "round_range_bps", "round_quote_volume"]
QUANT_TARGETS = ["max_up_bps", "max_down_bps", "round_range_bps"]


def _csv_append(path, header, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


# ---- model factories (graceful optional deps) -------------------------------------------
def classifiers(n_jobs=2):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    m = {
        "logistic": Pipeline([("sc", StandardScaler()), ("clf", LogisticRegression(max_iter=1000, class_weight="balanced"))]),
        "histgb": HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.05),
    }
    try:
        from lightgbm import LGBMClassifier
        m["lightgbm"] = LGBMClassifier(n_estimators=300, max_depth=5, learning_rate=0.05, n_jobs=n_jobs, verbose=-1)
    except Exception:
        pass
    try:
        from xgboost import XGBClassifier
        m["xgboost"] = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, n_jobs=n_jobs,
                                     tree_method="hist", eval_metric="logloss", verbosity=0)
    except Exception:
        pass
    return m


def regressors(n_jobs=2):
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import HistGradientBoostingRegressor
    m = {"ridge": Ridge(alpha=1.0), "histgb": HistGradientBoostingRegressor(max_iter=250, max_depth=4, learning_rate=0.05)}
    try:
        from lightgbm import LGBMRegressor
        m["lightgbm"] = LGBMRegressor(n_estimators=300, max_depth=5, learning_rate=0.05, n_jobs=n_jobs, verbose=-1)
    except Exception:
        pass
    return m


# ---- metrics ----------------------------------------------------------------------------
def cls_metrics(y, p):
    from sklearn.metrics import roc_auc_score, log_loss
    y = np.asarray(y, float); p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    out = {"n": len(y), "base": float(y.mean()), "brier": float(np.mean((p - y) ** 2))}
    out["auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan")
    out["logloss"] = float(log_loss(y, p, labels=[0, 1])) if len(np.unique(y)) > 1 else float("nan")
    for thr in (0.90, 0.93, 0.95):
        m = p >= thr
        out[f"real@{thr}"] = float(y[m].mean()) if m.sum() else float("nan")
        out[f"cov@{thr}"] = float(m.mean())
    return out


def reg_metrics(y, pred):
    y = np.asarray(y, float); pred = np.asarray(pred, float)
    err = pred - y
    return {"n": len(y), "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2)))}


def pinball(y, q, alpha):
    d = y - q
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


# ---- walk-forward folds (grouped by round, time-ordered) --------------------------------
def wf_folds(df, n_splits):
    rounds = df[["round_id", "round_start"]].drop_duplicates().sort_values("round_start")
    blocks = np.array_split(rounds["round_id"].values, n_splits + 1)
    for k in range(n_splits):
        tr_ids = np.concatenate(blocks[: k + 1]); te_ids = blocks[k + 1]
        yield k + 1, set(tr_ids.tolist()), set(te_ids.tolist())


def run_horizon(hdf, feats, folds, max_train):
    h = int(hdf["horizon_min"].iloc[0])
    print(f"\n=== horizon {h}m: {len(hdf):,} snapshots, {hdf['round_id'].nunique():,} rounds ===")
    # CLASSIFICATION
    for tgt in CLS_TARGETS:
        if tgt not in hdf.columns:
            continue
        sub = hdf[hdf[tgt].notna()]
        if sub[tgt].nunique() < 2:
            continue
        rows = []
        for fold, tr_ids, te_ids in wf_folds(sub, folds):
            tr = sub[sub["round_id"].isin(tr_ids)]; te = sub[sub["round_id"].isin(te_ids)]
            if len(tr) < 500 or len(te) < 100 or tr[tgt].nunique() < 2:
                continue
            if max_train and len(tr) > max_train:
                tr = tr.sample(max_train, random_state=0)
            Xtr, ytr = tr[feats].values, tr[tgt].values.astype(int)
            Xte, yte = te[feats].values, te[tgt].values.astype(int)
            # all predictors, then score WITHIN seconds-left buckets (pooled AUC is misleading:
            # late-round snapshots make the winner trivially predictable = P(Hold), not a forecast).
            preds = {"baseline_rate": np.full(len(yte), float(ytr.mean()))}
            if tgt == "label_current_side_wins" and "analytic_p_hold" in te.columns:
                preds["analytic_barrier"] = te["analytic_p_hold"].values
            for name, model in classifiers().items():
                try:
                    model.fit(Xtr, ytr); preds[name] = model.predict_proba(Xte)[:, 1]
                except Exception as e:
                    print(f"  [skip] {tgt}/{name} fold{fold}: {str(e)[:60]}")
            sl = te["seconds_left"].values
            buckets = [("all", np.ones(len(sl), bool)), ("le60", sl <= 60), ("61_120", (sl > 60) & (sl <= 120)),
                       ("121_180", (sl > 120) & (sl <= 180)), ("gt180", sl > 180)]
            for name, p in preds.items():
                for bname, mask in buckets:
                    if mask.sum() < 50:
                        continue
                    mm = cls_metrics(yte[mask], p[mask])
                    rows.append([h, tgt, name, bname, fold, mm["n"], round(mm["base"], 4), _r(mm["auc"]),
                                 round(mm["brier"], 4), _r(mm["logloss"]), _r(mm["real@0.9"]), _r(mm["cov@0.9"]),
                                 _r(mm["real@0.93"]), _r(mm["cov@0.93"]), _r(mm["real@0.95"]), _r(mm["cov@0.95"])])
        if rows:
            _csv_append(os.path.join(RES, "bakeoff_classification.csv"),
                        ["horizon", "target", "model", "sl_bucket", "fold", "n", "base_rate", "auc", "brier",
                         "logloss", "real@0.90", "cov@0.90", "real@0.93", "cov@0.93", "real@0.95", "cov@0.95"], rows)
            print(f"  [cls] {tgt}: wrote {len(rows)} rows")
    # REGRESSION (point)
    for tgt in REG_TARGETS:
        if tgt not in hdf.columns:
            continue
        sub = hdf[hdf[tgt].notna()].copy()
        if tgt == "round_quote_volume":
            sub[tgt] = np.log1p(sub[tgt].clip(lower=0))
        rows = []
        for fold, tr_ids, te_ids in wf_folds(sub, folds):
            tr = sub[sub["round_id"].isin(tr_ids)]; te = sub[sub["round_id"].isin(te_ids)]
            if len(tr) < 500 or len(te) < 100:
                continue
            if max_train and len(tr) > max_train:
                tr = tr.sample(max_train, random_state=0)
            Xtr, ytr = tr[feats].values, tr[tgt].values; Xte, yte = te[feats].values, te[tgt].values
            rows.append([h, tgt, "baseline_mean", fold, len(yte), round(reg_metrics(yte, np.full(len(yte), ytr.mean()))["mae"], 3),
                         round(reg_metrics(yte, np.full(len(yte), ytr.mean()))["rmse"], 3)])
            for name, model in regressors().items():
                try:
                    model.fit(Xtr, ytr); pred = model.predict(Xte); rm = reg_metrics(yte, pred)
                    rows.append([h, tgt, name, fold, rm["n"], round(rm["mae"], 3), round(rm["rmse"], 3)])
                except Exception as e:
                    print(f"  [skip] {tgt}/{name} fold{fold}: {str(e)[:60]}")
        if rows:
            _csv_append(os.path.join(RES, "bakeoff_regression.csv"),
                        ["horizon", "target", "model", "fold", "n", "mae", "rmse"], rows)
            print(f"  [reg] {tgt}: wrote {len(rows)} rows")
    # QUANTILE BANDS
    try:
        from lightgbm import LGBMRegressor
        for tgt in QUANT_TARGETS:
            if tgt not in hdf.columns:
                continue
            sub = hdf[hdf[tgt].notna()]
            rows = []
            for fold, tr_ids, te_ids in wf_folds(sub, folds):
                tr = sub[sub["round_id"].isin(tr_ids)]; te = sub[sub["round_id"].isin(te_ids)]
                if len(tr) < 1000 or len(te) < 100:
                    continue
                # CQR: split train rounds -> fit (80%) + conformal calib (20%)
                trr = tr[["round_id", "round_start"]].drop_duplicates().sort_values("round_start")["round_id"].values
                cut = int(len(trr) * 0.8)
                fit = tr[tr["round_id"].isin(set(trr[:cut]))]; cal = tr[tr["round_id"].isin(set(trr[cut:]))]
                if len(cal) < 100:
                    continue
                if max_train and len(fit) > max_train:
                    fit = fit.sample(max_train, random_state=0)
                Xf, yf = fit[feats].values, fit[tgt].values
                Xc, yc = cal[feats].values, cal[tgt].values
                Xte, yte = te[feats].values, te[tgt].values
                pr = {}
                for a in (0.1, 0.5, 0.9):
                    m = LGBMRegressor(objective="quantile", alpha=a, n_estimators=250, max_depth=5,
                                      learning_rate=0.05, n_jobs=2, verbose=-1).fit(Xf, yf)
                    pr[a] = (m.predict(Xc), m.predict(Xte))
                qlo_c, qlo_t = pr[0.1]; qhi_c, qhi_t = pr[0.9]; qmid_t = pr[0.5][1]
                cov_raw = float(np.mean((yte >= qlo_t) & (yte <= qhi_t)))
                # split-conformal correction for an 80% interval
                E = np.maximum(qlo_c - yc, yc - qhi_c)
                Q = float(np.quantile(E, min(1.0, 0.8 * (1 + 1.0 / len(yc)))))
                cov_cqr = float(np.mean((yte >= qlo_t - Q) & (yte <= qhi_t + Q)))
                rows.append([h, tgt, fold, len(yte), round(cov_raw, 4), round(cov_cqr, 4), round(Q, 2),
                             round(pinball(yte, qlo_t, 0.1), 3), round(pinball(yte, qmid_t, 0.5), 3),
                             round(pinball(yte, qhi_t, 0.9), 3)])
            if rows:
                _csv_append(os.path.join(RES, "bakeoff_quantile.csv"),
                            ["horizon", "target", "fold", "n", "cov80_raw", "cov80_cqr", "cqr_Q",
                             "pinball_q10", "pinball_q50", "pinball_q90"], rows)
                print(f"  [quant] {tgt}: wrote {len(rows)} rows")
    except Exception as e:
        print(f"  [quant] skipped (no lightgbm): {str(e)[:60]}")


def _r(x):
    return "" if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


def summarize():
    out = []
    cp = os.path.join(RES, "bakeoff_classification.csv")
    if os.path.exists(cp):
        d = pd.read_csv(cp)
        for c in ["auc", "brier", "real@0.93", "cov@0.93"]:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        g = d.groupby(["horizon", "target", "model", "sl_bucket"]).agg(folds=("fold", "nunique"),
                auc=("auc", "mean"), brier=("brier", "mean"), real_at_93=("real@0.93", "mean"),
                cov_at_93=("cov@0.93", "mean")).reset_index()
        for _, r in g.iterrows():
            out.append(["classification", r["horizon"], r["target"], r["model"], r["sl_bucket"], r["folds"],
                        round(r["auc"], 4) if pd.notna(r["auc"]) else "", round(r["brier"], 4),
                        round(r["real_at_93"], 4) if pd.notna(r["real_at_93"]) else "",
                        round(r["cov_at_93"], 4) if pd.notna(r["cov_at_93"]) else ""])
    rp = os.path.join(RES, "bakeoff_regression.csv")
    if os.path.exists(rp):
        d = pd.read_csv(rp)
        g = d.groupby(["horizon", "target", "model"]).agg(folds=("fold", "nunique"), mae=("mae", "mean"),
                rmse=("rmse", "mean")).reset_index()
        for _, r in g.iterrows():
            out.append(["regression", r["horizon"], r["target"], r["model"], "", r["folds"], "",
                        round(r["mae"], 3), round(r["rmse"], 3), ""])
    sp = os.path.join(RES, "bakeoff_summary.csv")
    with open(sp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "horizon", "target", "model", "sl_bucket", "folds", "auc_or_blank", "brier_or_mae",
                    "real@0.93_or_rmse", "cov@0.93"])
        w.writerows(out)
    print(f"\n[summary] wrote {sp} ({len(out)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--max-train", type=int, default=200000)
    a = ap.parse_args()
    if not os.path.exists(FEATURES):
        raise SystemExit(f"[bakeoff] {FEATURES} not found — run build_binance_updown_feature_dataset.py first.")
    t0 = time.time()
    man = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
    df = pd.read_parquet(FEATURES)
    feats = [c for c in man.get("feature_cols", []) if c in df.columns] or \
            [c for c in df.columns if c not in ("timestamp", "round_id", "round_start") and df[c].dtype != object
             and not c.startswith("label_")]
    print(f"[bakeoff] {len(df):,} rows, {len(feats)} features, horizons={sorted(df['horizon_min'].unique())}")
    # fresh CSVs
    for f in ("bakeoff_classification.csv", "bakeoff_regression.csv", "bakeoff_quantile.csv"):
        p = os.path.join(RES, f)
        if os.path.exists(p):
            os.remove(p)
    for h in sorted(df["horizon_min"].unique()):
        run_horizon(df[df["horizon_min"] == h].copy(), feats, a.folds, a.max_train)
    summarize()
    print(f"[bakeoff] done in {time.time()-t0:.0f}s. CSVs in {RES}")


if __name__ == "__main__":
    main()
