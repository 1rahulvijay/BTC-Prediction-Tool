"""
head_probability_buckets.py — probability-bucket verification for every specialist head (Final Plan P3).
========================================================================================================
Raw AUC is not enough. The champion validator should trust a head's probability REGION only where that
region has historically been good. This scorecard computes, per head, leak-free OOF buckets:

    base rate · top 1/5/10/20% event rate (precision) + lift · avg favorable move · avg adverse move
    + a 10-bin calibration table (mean predicted vs realized, ECE) + a monotonicity check.

Heads scored from the 1m research matrix (labels computable there):
  • bigmove  — future_abs_move_5m > 75th pct        (saved: bigmove_keeper_model.pkl)
  • bigdrop  — future low <= -10 bps below close     (saved: bigdrop_keeper_model.pkl)
  • signed_quantile — realized 5m move inside the 80% band (coverage, not a classifier)

Each head uses its OWN trainer's exact ensemble + FEATURES (imported) so OOF is honest, not in-sample.
P(Hold) and direction are validated by their own scorecards (phold_tier_scorecard.py, sign_truth_scorecard.py)
— pointed to here rather than recomputed (different feature space / snapshot table).

Outputs: docs/active/HEAD_PROBABILITY_BUCKETS_<date>.md (+ data/head_probability_buckets.parquet).
Usage:  python backend/head_probability_buckets.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 can't encode ≤ / ·
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
SM = os.path.join(DATA_DIR, "saved_models")
OUT_MD = os.path.join(ROOT, "docs", "active", f"HEAD_PROBABILITY_BUCKETS_{date.today().isoformat()}.md")
OUT_PARQUET = os.path.join(DATA_DIR, "head_probability_buckets.parquet")

if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _oof_scores(X, y, ensemble_factory, n_splits=5):
    """Leak-free out-of-fold probabilities via TimeSeriesSplit — refits the head's own ensemble."""
    oof = np.full(len(y), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=n_splits).split(X):
        clf = ensemble_factory()
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]
    seen = ~np.isnan(oof)
    return oof, seen


def _top_buckets(y, p, fav, adv, fracs=(0.01, 0.05, 0.10, 0.20)):
    """For each top-frac of score: event rate (precision), lift vs base, avg favorable/adverse move."""
    base = float(y.mean())
    order = np.argsort(p)[::-1]
    rows = []
    for f in fracs:
        k = max(1, int(len(p) * f))
        idx = order[:k]
        er = float(y[idx].mean())
        rows.append({
            "bucket": f"top {int(f*100)}%", "n": int(k),
            "event_rate": round(er, 4), "lift": round(er / base, 2) if base > 0 else None,
            "avg_favorable_move": round(float(np.nanmean(fav[idx])), 2) if fav is not None else None,
            "avg_adverse_move": round(float(np.nanmean(adv[idx])), 2) if adv is not None else None,
        })
    return base, rows


def _calibration(y, p, bins=10):
    """Decile calibration table + ECE + monotonicity of realized event rate across score deciles."""
    qs = np.quantile(p, np.linspace(0, 1, bins + 1))
    qs[0], qs[-1] = -np.inf, np.inf
    table, ece, prev, mono = [], 0.0, -1.0, True
    for i in range(bins):
        m = (p > qs[i]) & (p <= qs[i + 1])
        if m.sum() == 0:
            continue
        pred, real = float(p[m].mean()), float(y[m].mean())
        table.append({"decile": i + 1, "n": int(m.sum()),
                      "mean_pred": round(pred, 4), "realized": round(real, 4)})
        ece += (m.sum() / len(p)) * abs(pred - real)
        if real < prev - 1e-9:
            mono = False
        prev = real
    return table, round(ece, 4), mono


def _score_classifier_head(name, df, features, label, fav_col, adv_col, ensemble_factory, saved_auc=None):
    sub = df.dropna(subset=features + [label]).copy()
    X = sub[features].values
    y = sub[label].values.astype(int)
    fav = sub[fav_col].abs().values if fav_col else None
    adv = sub[adv_col].values if adv_col else None
    oof, seen = _oof_scores(X, y, ensemble_factory)
    y, oof = y[seen], oof[seen]
    fav = fav[seen] if fav is not None else None
    adv = adv[seen] if adv is not None else None
    auc = float(roc_auc_score(y, oof))
    # Honest OUT-OF-SAMPLE calibrated ECE: fit isotonic on the first 80% of the OOF and evaluate
    # calibration on the held-out last 20% — the SAME shape serving uses. (In-sample iso would
    # report a misleading ~0; the raw rank score reports a misleading ~0.18. The OOS number is
    # the truth.) AUC and the top-N buckets are rank-invariant, so only calibration changes.
    raw_ece = _calibration(y, oof)[1]
    cut = int(len(oof) * 0.8)
    try:
        iso = IsotonicRegression(out_of_bounds="clip").fit(oof[:cut], y[:cut])
        calib, ece, mono = _calibration(y[cut:], iso.predict(oof[cut:]))
    except Exception:
        calib, ece, mono = _calibration(y, oof)
    base, buckets = _top_buckets(y, oof, fav, adv)
    return {"head": name, "n": int(len(y)), "base_rate": round(base, 4), "oof_auc": round(auc, 3),
            "saved_auc": saved_auc, "ece": ece, "raw_ece": raw_ece, "monotonic": mono,
            "buckets": buckets, "calibration": calib}


def _score_quantile_coverage(df):
    """signed_quantile: realized 5m move inside the calibrated 80% band → coverage by the saved head."""
    path = os.path.join(SM, "signed_quantile_model.pkl")
    if not os.path.exists(path):
        return None
    bundle = _verified_load(path)
    feats = bundle["features"]
    models = bundle.get("models", {})
    h = 5 if 5 in models else (next(iter(models)) if models else None)
    if h is None:
        return None
    m = models[h]
    sub = df.dropna(subset=feats + ["future_close_5m", "close"]).copy()
    Xv = sub[feats].values
    cqr = float(m.get("cqr", 0.0))
    lo = m["q10"].predict(Xv) - cqr
    hi = m["q90"].predict(Xv) + cqr
    realized = (sub["future_close_5m"].values / sub["close"].values - 1.0) * 1e4
    inside = (realized >= lo) & (realized <= hi)
    width = float(np.mean(hi - lo))
    return {"head": "signed_quantile (80% band)", "n": int(len(sub)),
            "coverage": round(float(inside.mean()), 4), "target": 0.80,
            "avg_band_width_bps": round(width, 1)}


def _md(results, qcov):
    L = [f"# Head Probability Buckets — {date.today().isoformat()}", "",
         "Leak-free OOF (TimeSeriesSplit-5) bucket quality for each specialist head. The champion "
         "validator should trust a head's probability region only where its bucket event-rate + "
         "calibration here are good. Raw AUC alone is insufficient.", ""]
    for r in results:
        if r is None:
            continue
        L.append(f"## {r['head']}")
        L.append(f"n={r['n']:,} · base rate **{r['base_rate']*100:.1f}%** · OOF AUC **{r['oof_auc']}**"
                 + (f" (saved {r['saved_auc']})" if r.get("saved_auc") else "")
                 + f" · **ECE {r['ece']}** (isotonic OOS-calibrated; raw-rank {r.get('raw_ece', '—')})"
                 + f" · monotonic deciles: **{'YES' if r['monotonic'] else 'NO'}**")
        L.append("")
        L.append("| bucket | n | event rate | lift | avg favorable | avg adverse |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for b in r["buckets"]:
            L.append(f"| {b['bucket']} | {b['n']:,} | {b['event_rate']*100:.1f}% | {b['lift']}× "
                     f"| {b['avg_favorable_move']} | {b['avg_adverse_move']} |")
        L.append("")
        L.append("Calibration (decile mean-pred → realized): "
                 + " · ".join(f"d{c['decile']}:{c['mean_pred']:.2f}→{c['realized']:.2f}"
                              for c in r["calibration"]))
        L.append("")
    if qcov:
        L.append(f"## {qcov['head']}")
        L.append(f"n={qcov['n']:,} · realized coverage **{qcov['coverage']*100:.1f}%** "
                 f"(target {qcov['target']*100:.0f}%) · avg band width {qcov['avg_band_width_bps']} bps")
        L.append("")
    L.append("## P(Hold) and direction")
    L.append("- **P(Hold)** is validated on its own snapshot holdout — run `phold_tier_scorecard.py` "
             "(P≥0.93 → 95.1% realized, P≥0.95 → 96.0%). Different feature space (intra-window snapshots), "
             "not recomputed here.")
    L.append("- **Directional big-up / big-down confirmation** is bucketed above, but remains confirmation-only. "
             "Top-score precision is better than base rate, yet not strong enough to trade alone; see "
             "`sign_truth_scorecard.py` for ordinary direction truth.")
    return "\n".join(L)


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)

    results, flat = [], []
    # bigmove
    try:
        import train_bigmove_keeper as bm
        _b = _verified_load(os.path.join(SM, "bigmove_keeper_model.pkl"))
        _thr = _b.get("move_threshold_usd_by_horizon") or {}
        t5 = float(_thr.get(5) or _thr.get("5") or 30.0)   # the threshold the head ACTUALLY trained on
        sv = _b.get("auc")
        df_bm = df.copy()
        delta = df_bm["close"].shift(-5) - df_bm["close"]
        df_bm["_bm_abs"] = delta.abs()
        df_bm["_bm_label"] = (df_bm["_bm_abs"] >= t5).astype(int)
        r = _score_classifier_head(f"bigmove (5m abs close move >= ${t5:.0f})", df_bm,
                                    bm.FEATURES, "_bm_label", "_bm_abs", None, bm._ensemble, sv)
        results.append(r)
    except Exception as e:
        print(f"bigmove buckets skipped: {e}")
    # bigdrop
    try:
        import train_bigdrop_keeper as bd
        _b = _verified_load(os.path.join(SM, "bigdrop_keeper_model.pkl"))
        _thr = _b.get("drop_threshold_usd_by_horizon") or {}
        t5 = float(_thr.get(5) or _thr.get("5") or 30.0)
        df_bd = df.copy()
        lows = df_bd["low"].to_numpy()
        future_low = np.full(len(df_bd), np.nan)
        for i in range(max(0, len(df_bd) - 5)):
            future_low[i] = np.min(lows[i + 1:i + 6])
        drop_usd = future_low - df_bd["close"].to_numpy()
        df_bd["_bd_label"] = (drop_usd <= -t5).astype(int)
        df_bd["_bd_adverse"] = drop_usd
        sv = _b.get("auc")
        r = _score_classifier_head(f"bigdrop (5m low <= -${t5:.0f})", df_bd,
                                    bd.FEATURES, "_bd_label", "future_abs_move_5m", "_bd_adverse",
                                    bd._ensemble, sv)
        results.append(r)
    except Exception as e:
        print(f"bigdrop buckets skipped: {e}")
    # directional confirmation heads
    try:
        import train_directional_keeper as dh
        _thr = _verified_load(os.path.join(SM, "directional_keeper_model.pkl")).get("move_threshold_usd_by_horizon") or {}
        t5 = float(_thr.get(5) or _thr.get("5") or 30.0)
        df_dh = df.copy()
        delta = df_dh["close"].shift(-5) - df_dh["close"]
        df_dh["_delta_usd"] = delta
        df_dh["_big_up_label"] = (delta >= t5).astype(int)
        df_dh["_big_down_label"] = (delta <= -t5).astype(int)
        bundle = _verified_load(os.path.join(SM, "directional_keeper_model.pkl"))
        h5 = (bundle.get("models") or {}).get(5) or (bundle.get("models") or {}).get("5") or {}
        sv_up = (h5.get("big_up") or {}).get("auc")
        sv_down = (h5.get("big_down") or {}).get("auc")
        results.append(_score_classifier_head(f"big_up confirmation (5m close >= +${t5:.0f})", df_dh,
                                             dh.FEATURES, "_big_up_label",
                                             "future_abs_move_5m", "_delta_usd", dh._ensemble, sv_up))
        results.append(_score_classifier_head(f"big_down confirmation (5m close <= -${t5:.0f})", df_dh,
                                             dh.FEATURES, "_big_down_label",
                                             "future_abs_move_5m", "_delta_usd", dh._ensemble, sv_down))
    except Exception as e:
        print(f"directional buckets skipped: {e}")
    # activity/range proxy
    try:
        import train_activity_keeper as ah
        _b = _verified_load(os.path.join(SM, "activity_keeper_model.pkl"))
        _thr = _b.get("range_threshold_usd_by_horizon") or {}
        t5 = float(_thr.get(5) or _thr.get("5") or 30.0)
        df_ah = df.copy()
        highs, lows = df_ah["high"].to_numpy(), df_ah["low"].to_numpy()
        future_range = np.full(len(df_ah), np.nan)
        for i in range(max(0, len(df_ah) - 5)):
            future_range[i] = np.max(highs[i + 1:i + 6]) - np.min(lows[i + 1:i + 6])
        df_ah["_range_usd"] = future_range
        df_ah["_activity_label"] = (future_range >= t5).astype(int)
        sv = _b.get("auc")
        results.append(_score_classifier_head(f"activity_range (5m range >= ${t5:.0f})", df_ah,
                                             ah.FEATURES, "_activity_label",
                                             "_range_usd", None, ah._ensemble, sv))
    except Exception as e:
        print(f"activity buckets skipped: {e}")
    # signed_quantile coverage
    qcov = None
    try:
        qcov = _score_quantile_coverage(df)
    except Exception as e:
        print(f"signed_quantile coverage skipped: {e}")

    # flatten for parquet
    for r in results:
        for b in r["buckets"]:
            flat.append({"head": r["head"], "base_rate": r["base_rate"], "oof_auc": r["oof_auc"],
                         "ece": r["ece"], "monotonic": r["monotonic"], **b})
    if flat:
        pd.DataFrame(flat).to_parquet(OUT_PARQUET, index=False)

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(_md(results, qcov))

    for r in results:
        print(f"{r['head']:28s} base={r['base_rate']*100:4.1f}%  OOF_AUC={r['oof_auc']}  "
              f"top5%={[b['event_rate'] for b in r['buckets'] if b['bucket']=='top 5%'][0]*100:.1f}%  "
              f"ECE={r['ece']}  monotonic={r['monotonic']}")
    if qcov:
        print(f"{qcov['head']:28s} coverage={qcov['coverage']*100:.1f}% (target 80%)")
    print(f"\nWrote {OUT_MD}")
    if flat:
        print(f"Wrote {OUT_PARQUET}")


if __name__ == "__main__":
    main()


def _verified_load(path):
    """Hash-check against the sidecar manifest BEFORE deserializing.

    joblib.load executes arbitrary code while unpickling, so validating after loading has
    already lost. Artifacts written before this migration carry no manifest; they still load
    while BTC_STRICT_ARTIFACT_IDENTITY is off, and each one is counted as remaining debt."""
    import sys as _sys
    from pathlib import Path as _Path

    _backend = str(_Path(__file__).resolve().parent)
    if _backend not in _sys.path:
        _sys.path.insert(0, _backend)
    from verified_io import verified_load as _vl

    return _vl(path)
