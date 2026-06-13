"""
model_bakeoff.py — INDEPENDENT model comparison on the BTC Up/Down "BEAT" task.
================================================================================
Trains MANY model families on the SAME leak-free beat task (P(window-close >= window-open),
per horizon) and emits one metrics report — so we can SEE which model actually earns its place
instead of guessing. It does NOT touch the live ensemble, the schema, serving, or any saved
model: it REUSES the beat head's leak-free builder (`train_beat_classifier`) and writes only to
`data/model_bakeoff_report.json` (report-only — nothing into saved_models/).

WHY reuse the beat builder: the §5bs leakage came from re-deriving features. By importing
`build_beat_features` + `beat_labels` + the `Xs,ys = X[:-1], y[1:]` alignment verbatim, every
model here is leak-free by construction and trains on the IDENTICAL task — a fair bakeoff.

Models (the "what we don't have", added here for comparison — NOT wired into the app):
  LIGHT (CPU, default): majority-baseline, logistic, random-forest, histgb (incumbent),
                        lightgbm, mlp.  ← run these first; LightGBM is the documented favourite.
  DEEP (--deep, GPU; run AFTER a train to avoid CUDA contention): LSTM, Transformer (lookback
        sequences of the beat features, via the architectures in seq_model_feasibility).
  NOT built (honest): CNN-LSTM / DeepLOB needs historical L2 order-book snapshots, which Binance
        does NOT archive — un-backfillable, so it can only come from live B1 accumulation later.
        RL is out of scope here (non-stationary reward; the docs and you both say "not first").

Metrics per model × horizon: accuracy, precision, recall, F1, AUC, Brier, log-loss, ECE
(calibration error), majority base-rate, high-confidence realized rate, the SIGNAL/NOISE
verdict, and top feature importances (tree/linear). Probabilities are isotonic-CALIBRATED
(the docs: for prediction markets calibration matters more than raw accuracy).

Usage:
  python backend/model_bakeoff.py --selftest                 # mechanical, synthetic, CPU
  python backend/model_bakeoff.py --days 30                  # LIGHT models, last 30 days
  python backend/model_bakeoff.py --days 30 --deep           # + LSTM/Transformer (GPU; post-train)
  python backend/model_bakeoff.py --start 2026-05-14 --end 2026-06-12
Gentle-citizen tip if a train is running:  set OMP_NUM_THREADS=2 first (light models stay CPU).
"""
import argparse
import json
import os
import sys

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
REPORT_PATH = os.path.join(DATA_DIR, "model_bakeoff_report.json")
NOISE_AUC = 0.55          # the documented bettable floor (SPEC §6); below this = no real discrimination
HORIZONS = (1, 3, 5, 7, 10, 15)


# ───────────────────────── metrics ────────────────────────────────────────────────────
def expected_calibration_error(y, p, bins=10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    n, ece = len(y), 0.0
    for i in range(bins):
        hi = p <= edges[i + 1] if i == bins - 1 else p < edges[i + 1]
        m = (p >= edges[i]) & hi
        c = int(m.sum())
        if c:
            ece += (c / n) * abs(float(p[m].mean()) - float(y[m].mean()))
    return ece


def metrics(y_true: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import (roc_auc_score, brier_score_loss, log_loss,
                                 precision_score, recall_score, f1_score, accuracy_score)
    pred = (p >= 0.5).astype(int)
    multi = len(np.unique(y_true)) > 1
    base = float(max(y_true.mean(), 1 - y_true.mean()))
    hi = p >= 0.6
    hireal = float(y_true[hi].mean()) if hi.sum() >= 20 else None
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, pred)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "auc": float(roc_auc_score(y_true, p)) if multi else 0.5,
        "brier": float(brier_score_loss(y_true, pc)),
        "log_loss": float(log_loss(y_true, pc, labels=[0, 1])),
        "ece": float(expected_calibration_error(y_true, pc)),
        "base_rate": base,
        "hi_conf_n": int(hi.sum()),
        "hi_conf_realized": hireal,
    }


def verdict(m: dict) -> str:
    # Tightened 2026-06-13 (§5bt): AUC >= 0.55 (real discrimination, not just base-rate tracking)
    # AND beats majority AND has a USABLE, CALIBRATED confident subset (>=20 calls at >=0.6 that
    # realize >=55%). The old gate (AUC>=0.53, "hi_conf None -> pass") stamped SIGNAL on a 0.531-AUC
    # model that committed ~1% of the time — a threshold artifact, not an edge. No vacuous passes now.
    ok = (m["auc"] >= NOISE_AUC and m["accuracy"] >= m["base_rate"] - 0.005
          and m["hi_conf_n"] >= 20 and (m["hi_conf_realized"] or 0.0) >= 0.55)
    return "SIGNAL" if ok else "NOISE"


# ───────────────────────── model registry (light, CPU) ────────────────────────────────
def make_light_models():
    """Fresh estimators each call. Lazy imports so --selftest needs only what it uses."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
    from sklearn.neural_network import MLPClassifier
    models = {
        "logistic": LogisticRegression(max_iter=200, C=1.0),
        "random_forest": RandomForestClassifier(n_estimators=200, max_depth=6,
                                                 n_jobs=int(os.environ.get("OMP_NUM_THREADS", "2")),
                                                 random_state=0),
        "histgb": HistGradientBoostingClassifier(max_iter=300, max_depth=4, learning_rate=0.05,
                                                 l2_regularization=1.0, random_state=0),
        "mlp": MLPClassifier(hidden_layer_sizes=(128, 64, 32), alpha=1e-3, max_iter=120,
                             early_stopping=True, random_state=0),
    }
    try:
        from lightgbm import LGBMClassifier
        models["lightgbm"] = LGBMClassifier(n_estimators=400, num_leaves=31, learning_rate=0.03,
                                            subsample=0.8, colsample_bytree=0.8, random_state=0,
                                            n_jobs=int(os.environ.get("OMP_NUM_THREADS", "2")),
                                            verbose=-1)
    except Exception:
        pass
    return models


def _feature_importance(name, model, feat_names):
    try:
        if hasattr(model, "feature_importances_"):
            imp = np.asarray(model.feature_importances_, dtype=float)
        elif hasattr(model, "coef_"):
            imp = np.abs(np.asarray(model.coef_, dtype=float).ravel())
        else:
            return None
        if imp.sum() <= 0:
            return None
        order = np.argsort(imp)[::-1][:5]
        return [(feat_names[i], round(float(imp[i] / imp.sum()), 3)) for i in order]
    except Exception:
        return None


# ───────────────────────── one horizon: temporal split + calibrate + score ─────────────
def _dump(pred_rows, tste, h, name, p, yte):
    """Append per-window REAL-DATA test predictions (for --dump-predictions review)."""
    if pred_rows is None or tste is None:
        return
    for i in range(len(p)):
        pred_rows.append({"timestamp_ms": int(tste[i]), "horizon": int(h), "model": name,
                          "p_up": round(float(p[i]), 4), "actual_up": int(yte[i])})


def run_horizon(Xs, ys, models, feat_names, ts=None, h=None, pred_rows=None, calibrate=True) -> dict:
    """Temporal 60/20/20 split (train past → test unseen future). Isotonic-calibrate on the
    middle slice. Score the held-out tail. Returns {model: metrics + importances + verdict}.
    When ts + pred_rows are given, also records each model's per-window prediction on the
    unseen test (real-data predictions to eyeball — not a metric, the raw calls)."""
    from sklearn.isotonic import IsotonicRegression
    n = len(ys)
    a, b = int(n * 0.6), int(n * 0.8)
    Xtr, ytr = Xs[:a], ys[:a]
    Xca, yca = Xs[a:b], ys[a:b]
    Xte, yte = Xs[b:], ys[b:]
    tste = ts[b:] if ts is not None else None
    out = {}
    # Majority baseline (the floor): constant = train base rate.
    base_p = np.full(len(yte), float(ytr.mean()))
    mb = metrics(yte, base_p)
    out["majority"] = {**mb, "verdict": verdict(mb), "top_features": None}
    _dump(pred_rows, tste, h, "majority", base_p, yte)
    for name, model in models.items():
        try:
            model.fit(Xtr, ytr)
            raw_ca = model.predict_proba(Xca)[:, 1]
            raw_te = model.predict_proba(Xte)[:, 1]
            if calibrate and len(np.unique(yca)) > 1:
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
                iso.fit(raw_ca, yca)
                p = iso.predict(raw_te)
            else:
                p = raw_te
            m = metrics(yte, p)
            out[name] = {**m, "verdict": verdict(m),
                         "top_features": _feature_importance(name, model, feat_names)}
            _dump(pred_rows, tste, h, name, p, yte)
        except Exception as e:
            out[name] = {"error": str(e)[:120]}
    return out


# ───────────────────────── deep models (optional, GPU) ────────────────────────────────
def run_horizon_deep(Xs, ys, lookback=30, epochs=12):
    """Build lookback sequences of the beat features and train LSTM + Transformer via the
    independent architectures in seq_model_feasibility. GPU-friendly; run AFTER a train."""
    try:
        import torch
        from seq_model_feasibility import _build_models, _fit_eval
    except Exception as e:
        return {"_error": f"deep models skipped ({str(e)[:80]})"}
    n, f = Xs.shape
    if n < lookback + 200:
        return {"_error": "not enough rows for sequences"}
    # sliding windows: seq ending at t predicts the beat label at t (already t+1-aligned upstream)
    idx = np.arange(lookback, n)
    seqs = np.stack([Xs[i - lookback:i] for i in idx]).astype(np.float32)  # (N, lookback, f)
    y3 = np.eye(3, dtype=np.float32)[np.where(ys[idx] == 1, 2, 0)]          # UP=2, DOWN=0 (no NEUTRAL)
    a = int(len(idx) * 0.7)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = {}
    for name, model in _build_models(f, lookback).items():
        if name == "TCN":
            continue  # TCN already in the live app; bakeoff focuses on the NOT-in-app candidates
        st = _fit_eval(model, seqs[:a], y3[:a], seqs[a:], y3[a:], epochs=epochs, device=device)
        out[name] = {"sign_acc": st["sign_acc"], "committed": st["committed"],
                     "first_loss": st["first_loss"], "last_loss": st["last_loss"]}
    return out


# ───────────────────────── orchestration ──────────────────────────────────────────────
def run(dates, deep=False, dump=False):
    from train_beat_classifier import _ohlc_for_dates, build_beat_features, beat_labels, FEATURE_NAMES
    T, O, H, L, C = _ohlc_for_dates(dates)
    if C is None or len(C) < 400:
        sys.exit("Not enough bars (need the cached aggTrades; run start.bat's backfill first).")
    X = build_beat_features(O, H, L, C, T)
    ts_all = T[1:]                       # window-open ms, aligned to Xs=X[:-1], ys=y[1:]
    print(f"\nFeature matrix {X.shape}; {len(C)} bars over {len(dates)} day(s). Features: {FEATURE_NAMES}")
    report = {"task": "beat P(close>=open)", "bars": int(len(C)), "days": len(dates),
              "features": FEATURE_NAMES, "horizons": {}}
    pred_rows = [] if dump else None
    for h in HORIZONS:
        y = beat_labels(O, C, h)
        Xs, ys = X[:-1], y[1:]          # §5bs anti-leakage alignment (verbatim from the beat head)
        m = ys >= 0
        Xv, yv, tsv = Xs[m], ys[m], ts_all[m]
        if len(yv) < 300 or len(np.unique(yv)) < 2:
            print(f"\n[{h}m] insufficient ({len(yv)})"); continue
        res = run_horizon(Xv, yv, make_light_models(), FEATURE_NAMES, ts=tsv, h=h, pred_rows=pred_rows)
        if deep:
            res["_deep"] = run_horizon_deep(Xv, yv)
        report["horizons"][str(h)] = res
        _print_horizon(h, res)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report -> {REPORT_PATH}")
    if pred_rows:
        import pandas as pd
        pp = os.path.join(DATA_DIR, "model_bakeoff_predictions.parquet")
        pd.DataFrame(pred_rows).to_parquet(pp, index=False)
        print(f"Per-window REAL-DATA predictions ({len(pred_rows):,} rows) -> {pp}")
        print("Each row = one held-out (unseen, recent) window: model's P(up) vs actual_up. Inspect with")
        print("pandas/DuckDB to see each independent model's raw calls on real data — not just metrics.")
    print("Read it tomorrow: which family clears SIGNAL at 5m/15m, and does any beat LightGBM with")
    print("LOWER ECE (better calibration)? Expectation (docs): LightGBM ~ histgb on top; DL likely")
    print("overfits at this scale; nothing escapes the 5m information ceiling — cleaner label only.")


def _print_horizon(h, res):
    print(f"\n  {h}m   {'model':<14}{'acc':>7}{'prec':>7}{'rec':>7}{'auc':>7}{'brier':>8}{'ece':>7}  verdict")
    for name, m in res.items():
        if name.startswith("_"):
            continue
        if "error" in m:
            print(f"       {name:<14} ERROR: {m['error']}"); continue
        print(f"       {name:<14}{m['accuracy']*100:>6.1f}%{m['precision']*100:>6.1f}%"
              f"{m['recall']*100:>6.1f}%{m['auc']:>7.3f}{m['brier']:>8.3f}{m['ece']:>7.3f}  {m['verdict']}")
    if isinstance(res.get("_deep"), dict) and "_error" not in res["_deep"]:
        for name, d in res["_deep"].items():
            sa = f"{d['sign_acc']*100:.1f}%" if d.get("sign_acc") is not None else "—"
            print(f"       {name+' (seq)':<14} sign_acc={sa}  loss {d['first_loss']:.2f}->{d['last_loss']:.2f}")


# ───────────────────────── self-test (synthetic, CPU, no data/network) ─────────────────
def selftest():
    np.random.seed(0)
    n, f = 4000, len(["ret_1", "ret_5", "ret_15", "rv_short", "rv_long", "variance_ratio",
                      "range_pos", "atr_norm", "mom_20", "hour_sin", "hour_cos"])
    feat = [f"f{i}" for i in range(f)]
    X = np.random.randn(n, f).astype(np.float32)
    # learnable signal: features 0 and 5 drive the beat label, plus noise.
    z = 0.9 * X[:, 0] + 0.6 * X[:, 5] + 0.5 * np.random.randn(n)
    y = (z > 0).astype(int)

    # ECE sanity: perfectly-calibrated constant has ECE ~ 0 only at the base rate; random ~ bounded.
    assert 0.0 <= expected_calibration_error(y, np.full(n, y.mean())) <= 1.0
    assert abs(expected_calibration_error(np.ones(100), np.ones(100)) - 0.0) < 1e-9

    res = run_horizon(X, y, make_light_models(), feat)
    assert "majority" in res and "logistic" in res
    for name, m in res.items():
        if "error" in m:
            raise AssertionError(f"{name} errored: {m['error']}")
        for k in ("accuracy", "precision", "recall", "f1", "auc", "brier", "ece", "base_rate"):
            assert k in m, f"{name} missing metric {k}"
            assert 0.0 <= m[k] <= 1.0 + 1e-9, f"{name}.{k} out of range: {m[k]}"
        assert m["verdict"] in ("SIGNAL", "NOISE")
    # a real learner should beat the majority floor's AUC (0.5) on this signal
    assert res["logistic"]["auc"] > 0.6, f"logistic AUC too low on a learnable signal: {res['logistic']['auc']}"
    assert res["majority"]["auc"] == 0.5
    print("  light models trained + scored; metrics in range; logistic AUC "
          f"{res['logistic']['auc']:.2f} > majority 0.50")
    print("model_bakeoff self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--deep", action="store_true", help="also LSTM/Transformer (GPU; post-train)")
    ap.add_argument("--dump-predictions", action="store_true",
                    help="write per-window real-data test predictions to model_bakeoff_predictions.parquet")
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--validate")
    ap.add_argument("--days", type=int, help="last N full days to yesterday")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit(0)
    from train_beat_classifier import resolve_dates
    dates, _ = resolve_dates(a)
    if not dates:
        ap.error("provide --selftest, --days N, --start/--end, or --validate DATE")
    run(dates, deep=a.deep, dump=a.dump_predictions)
