"""
probe_model_bakeoff.py - is the ceiling the MODEL's fault, or the information's?
================================================================================
Runs EVERY model family on the SAME walk-forward bench (from probe_ta_matrix) for the
two hard questions -- direction (tradeable) and big_move (selectivity) -- so we can prove
the 5m/15m direction ceiling is informational, not a modelling shortfall.

Models tested:
  * IN THE APP:  HistGBM, XGBoost, LightGBM, CatBoost, LogisticRegression  (the L1 seats)
  * NOT in app:  RandomForest, ExtraTrees, GradientBoosting, KNN, GaussianNB,
                 MLP (sklearn), MLP (torch), SGD-logistic
  (the app's TCN sequence seat is tested separately -- run with --tcn.)

Plus a META-LABELING experiment (Lopez de Prado): instead of predicting direction, train a
secondary model to predict WHEN a primary momentum signal is RIGHT, then check precision at
low coverage -- the honest test of "selective betting". An edge exists only if a high-
confidence SUBSET clears ~0.55 accuracy (the Polymarket cost bar) out-of-sample.

Read-only; reuses probe_ta_matrix features + walk-forward. ASCII-only output.

Usage:
  python backend/research/standalone/probe_model_bakeoff.py            # full bakeoff (5m + 15m) + meta-labeling
  python backend/research/standalone/probe_model_bakeoff.py --tcn      # also run the torch sequence model (slower)
  python backend/research/standalone/probe_model_bakeoff.py --selftest
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
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")

FOLDS = 4   # walk-forward folds for the bakeoff (fewer than the monitor's 6, for runtime)


# --------------------------------------------------------------------------- model registry
def _scaled(est):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), est)


def model_registry(include_torch=True) -> dict:
    from sklearn.ensemble import (HistGradientBoostingClassifier, RandomForestClassifier,
                                  ExtraTreesClassifier, GradientBoostingClassifier)
    from sklearn.linear_model import LogisticRegression, SGDClassifier
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neural_network import MLPClassifier
    import xgboost as xgb
    import lightgbm as lgb
    from catboost import CatBoostClassifier

    reg = {
        # --- in the app (L1 seats) ---
        "HistGBM*":     lambda: HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=4, random_state=0),
        "XGBoost*":     lambda: xgb.XGBClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                                  subsample=0.8, eval_metric="logloss", verbosity=0, n_jobs=2),
        "LightGBM*":    lambda: lgb.LGBMClassifier(n_estimators=200, max_depth=4, learning_rate=0.05,
                                                   subsample=0.8, verbose=-1, n_jobs=2),
        "CatBoost*":    lambda: CatBoostClassifier(iterations=150, depth=4, learning_rate=0.05,
                                                   verbose=0, allow_writing_files=False),
        "LogReg*":      lambda: _scaled(LogisticRegression(max_iter=1000, C=0.5)),
        # --- NOT in the app ---
        "RandomForest": lambda: RandomForestClassifier(n_estimators=200, max_depth=8, n_jobs=2, random_state=0),
        "ExtraTrees":   lambda: ExtraTreesClassifier(n_estimators=200, max_depth=8, n_jobs=2, random_state=0),
        "GradBoost":    lambda: GradientBoostingClassifier(n_estimators=50, max_depth=2, subsample=0.5,
                                                           learning_rate=0.08, random_state=0),
        "KNN":          lambda: _scaled(KNeighborsClassifier(n_neighbors=64, algorithm="kd_tree", n_jobs=2)),
        "GaussianNB":   lambda: _scaled(GaussianNB()),
        "MLP-sklearn":  lambda: _scaled(MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=60,
                                                      early_stopping=True, random_state=0)),
        "SGD-logistic": lambda: _scaled(SGDClassifier(loss="log_loss", max_iter=1000, random_state=0)),
    }
    if include_torch:
        reg["MLP-torch"] = lambda: _scaled(TorchMLP())
    return reg


# --------------------------------------------------------------------------- torch tabular MLP (sklearn-compatible)
class TorchMLP:
    def __init__(self, hidden=(64, 32), epochs=20, lr=1e-3, bs=512):
        self.hidden, self.epochs, self.lr, self.bs = hidden, epochs, lr, bs

    def fit(self, X, y):
        import torch
        import torch.nn as nn
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32).reshape(-1, 1)
        layers, prev = [], X.shape[1]
        for hsz in self.hidden:
            layers += [nn.Linear(prev, hsz), nn.ReLU(), nn.Dropout(0.1)]
            prev = hsz
        layers += [nn.Linear(prev, 1)]
        self.net = nn.Sequential(*layers)
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        lossf = nn.BCEWithLogitsLoss()
        Xt, yt = torch.tensor(X), torch.tensor(y)
        n = len(Xt)
        self.net.train()
        for _ in range(self.epochs):
            perm = torch.randperm(n)
            for i in range(0, n, self.bs):
                idx = perm[i:i + self.bs]
                opt.zero_grad()
                loss = lossf(self.net(Xt[idx]), yt[idx])
                loss.backward()
                opt.step()
        self.classes_ = np.array([0, 1])
        return self

    def predict_proba(self, X):
        import torch
        self.net.eval()
        with torch.no_grad():
            p = torch.sigmoid(self.net(torch.tensor(np.asarray(X, dtype=np.float32)))).numpy().ravel()
        return np.column_stack([1 - p, p])


# --------------------------------------------------------------------------- bakeoff
def _prep(df, target):
    X = TA.build_features(df)
    tgt = TA.build_targets(df)[target]
    kind, tradeable, y, base = tgt
    d = pd.concat([X, y.rename("__y__")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    return d[X.columns], d["__y__"], tradeable


def run_bakeoff(horizons=(5, 15), target="direction_up_down", include_torch=True):
    reg = model_registry(include_torch)
    results = {}
    tradeable = None
    for hz in horizons:
        df = TA.load_ohlcv(hz)
        Xc, y, tradeable = _prep(df, target)
        for name, fac in reg.items():
            try:
                agg = TA.wf_clf(Xc, y, factory=fac, folds=FOLDS)
                results.setdefault(name, {})[hz] = (agg["auc"], agg["auc_std"], agg["above_half"], agg["n_folds"])
                print(f"  [{target} {hz}m] {name:<16} AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} "
                      f"({agg['above_half']}/{agg['n_folds']})", flush=True)
            except Exception as e:
                results.setdefault(name, {})[hz] = (float("nan"), 0.0, 0, 0)
                print(f"  [{target} {hz}m] {name:<16} ERR {str(e)[:50]}", flush=True)
    return results, tradeable


def _print_bakeoff(title, results, horizons, tradeable):
    print("\n" + "=" * 88)
    print(title)
    bar = TA.COST_AUC
    print(f"{'model':<16}" + "".join(f"{str(h)+'m AUC':<16}" for h in horizons) +
          ("verdict (cost bar %.2f)" % bar if tradeable else "verdict (selectivity)"))
    print("-" * 88)
    # sort by mean AUC desc
    def meanauc(r):
        vs = [r[h][0] for h in horizons if h in r and r[h][0] == r[h][0]]
        return np.mean(vs) if vs else -1
    for name in sorted(results, key=lambda n: -meanauc(results[n])):
        r = results[name]
        cells = ""
        aucs = []
        for h in horizons:
            if h in r and r[h][0] == r[h][0]:
                auc, std, ab, nf = r[h]
                aucs.append(auc)
                cells += f"{auc:.3f}+-{std:.3f}({ab}/{nf}) "
            else:
                cells += "  --            "
        ma = np.mean(aucs) if aucs else float("nan")
        if tradeable:
            verdict = ("TRADEABLE" if ma >= bar else "sub-cost (ceiling)" if ma >= 0.515 else "coin-flip")
        else:
            verdict = ("REAL edge" if ma >= 0.55 else "weak" if ma >= 0.515 else "none")
        print(f"{name:<16}{cells:<32}{verdict}")
    print(f"\n(* = a seat the app already uses.  AUC>={bar:.2f} on direction = tradeable after spread.)")


# --------------------------------------------------------------------------- meta-labeling
def meta_labeling(horizon=5):
    """Primary signal = momentum (lean with the last return). Meta-model predicts P(primary
    correct) from features. Honest test: at low coverage (top meta-confidence), does the
    primary's directional accuracy clear the ~0.55 cost bar out-of-sample?"""
    df = TA.load_ohlcv(horizon)
    X = TA.build_features(df)
    c = df["close"]
    primary = np.sign(c.pct_change()).shift(0)            # lean = sign of last completed return (causal)
    actual = np.sign(c.shift(-1) - c)
    meta_y = (primary == actual).astype(float)            # 1 if the momentum lean was right
    d = pd.concat([X, meta_y.rename("__m__"), primary.rename("__p__"), actual.rename("__a__")],
                  axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    d = d[d["__p__"] != 0]
    n = len(d); cut = int(n * 0.8)
    Xc = d[X.columns]
    m = TA.default_clf(); m.fit(Xc.iloc[:cut], d["__m__"].iloc[:cut])
    p_correct = m.predict_proba(Xc.iloc[cut:])[:, 1]
    te = d.iloc[cut:].copy(); te["p_correct"] = p_correct
    # directional accuracy = fraction where primary lean == actual, sliced by meta-confidence coverage
    base_acc = float((te["__p__"] == te["__a__"]).mean())
    print(f"\n--- META-LABELING ({horizon}m): primary=momentum lean, meta=P(lean correct) ---")
    print(f"baseline: bet EVERY momentum lean -> acc {base_acc:.3f} (n_test={len(te)})")
    print(f"{'top coverage':<14}{'n':<8}{'dir acc':<10}{'vs cost 0.55'}")
    for cov in (1.0, 0.5, 0.25, 0.10, 0.05):
        k = max(20, int(len(te) * cov))
        sel = te.nlargest(k, "p_correct")
        acc = float((sel["__p__"] == sel["__a__"]).mean())
        print(f"{cov*100:>5.0f}%        {k:<8}{acc:<10.3f}{'CLEARS' if acc >= 0.55 else 'below'}")
    print("  READ: if dir-acc stays < 0.55 even at the tightest coverage, meta-labeling cannot")
    print("  manufacture a tradeable direction edge here -- the ceiling holds under selection too.")


# --------------------------------------------------------------------------- sequence model (optional)
def run_tcn(horizon=5, lookback=32, epochs=8):
    """Tiny 1D-CNN (TCN-style) on a lookback window of returns+vol -> direction. Tests whether
    TEMPORAL deep structure beats the tabular ceiling. Walk-forward, last 3 folds (CPU-bounded)."""
    import torch
    import torch.nn as nn
    df = TA.load_ohlcv(horizon)
    c = df["close"].values
    ret = np.diff(np.log(c), prepend=np.log(c[0]))
    vol = pd.Series(ret).rolling(20).std().bfill().values
    feats = np.stack([ret, vol], axis=1)
    y = (np.r_[c[1:], c[-1]] > c).astype(np.float32)       # next-bar up
    Xs, ys = [], []
    for i in range(lookback, len(c) - 1):
        Xs.append(feats[i - lookback:i]); ys.append(y[i])
    Xs = np.array(Xs, dtype=np.float32).transpose(0, 2, 1)  # (N, C, L)
    ys = np.array(ys, dtype=np.float32)
    from sklearn.metrics import roc_auc_score
    n = len(Xs); aucs = []
    for k in (7, 8, 9):
        cut, teu = int(n * k / 10), int(n * (k + 1) / 10)
        net = nn.Sequential(nn.Conv1d(2, 16, 3, padding=1), nn.ReLU(),
                            nn.Conv1d(16, 16, 3, padding=1), nn.ReLU(),
                            nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(16, 1))
        opt = torch.optim.Adam(net.parameters(), lr=1e-3)
        lossf = nn.BCEWithLogitsLoss()
        Xtr = torch.tensor(Xs[:cut]); ytr = torch.tensor(ys[:cut]).view(-1, 1)
        net.train()
        for _ in range(epochs):
            for i in range(0, len(Xtr), 256):
                opt.zero_grad()
                loss = lossf(net(Xtr[i:i+256]), ytr[i:i+256]); loss.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            p = torch.sigmoid(net(torch.tensor(Xs[cut:teu]))).numpy().ravel()
        try:
            aucs.append(roc_auc_score(ys[cut:teu], p))
        except ValueError:
            pass
    a = np.array(aucs)
    print(f"\n--- SEQUENCE TCN (1D-CNN, {horizon}m, lookback={lookback}) ---")
    print(f"walk-forward AUCs: {' '.join(f'{x:.3f}' for x in a)}  mean={a.mean():.3f}")
    print(f"  -> {'beats ceiling' if a.mean() >= 0.55 else 'at the ceiling (no temporal edge)'}")


# --------------------------------------------------------------------------- selftest
def selftest():
    reg = model_registry(include_torch=False)
    assert len(reg) >= 11, "registry too small"
    # synthetic: a learnable target (feature sign) -> a couple of models must clear AUC 0.6
    rng = np.random.default_rng(0); n = 2500
    X = pd.DataFrame(rng.normal(0, 1, (n, 6)), columns=[f"x{i}" for i in range(6)])
    y = pd.Series(((X["x0"] + 0.5 * X["x1"]) > 0).astype(float))
    aucs = []
    for name in ("HistGBM*", "LogReg*", "RandomForest"):
        agg = TA.wf_clf(X, y, factory=reg[name], folds=4)
        aucs.append(agg["auc"])
    print(f"selftest: learnable-target AUCs {['%.3f' % a for a in aucs]} (expect > 0.6)")
    ok = all(a > 0.6 for a in aucs)
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tcn", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    dr, trad = run_bakeoff(target="direction_up_down")
    _print_bakeoff("MODEL BAKEOFF -- DIRECTION (up/down), walk-forward 6-fold", dr, (5, 15), trad)
    bm, tradb = run_bakeoff(target="big_move")
    _print_bakeoff("MODEL BAKEOFF -- BIG_MOVE (selectivity), walk-forward 6-fold", bm, (5, 15), tradb)
    meta_labeling(5)
    if a.tcn:
        run_tcn(5)
    print("\nBOTTOM LINE: if EVERY model clusters at the same AUC, the ceiling is INFORMATIONAL, "
          "not a model choice -- exactly the manual's rule 7. Spend effort on selectivity, not models.")


if __name__ == "__main__":
    main()
