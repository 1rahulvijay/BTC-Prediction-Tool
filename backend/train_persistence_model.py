"""
train_persistence_model.py — A1 / T3 persistence model (offline, frozen-model-safe).
====================================================================================
Trains a CALIBRATED P(hold) classifier on persistence_dataset.parquet:
"price is `distance` past the line with `seconds_left` left — will it HOLD to close?"

This is a SEPARATE head — it does NOT touch the frozen v6 ensemble, the feature schema,
or any serving code. It only reads the parquet and writes
data/saved_models/persistence_model.pkl. Wiring P(hold) into the live card is a later,
separate step (needs a restart).

Anti-leakage discipline:
  * NEVER use `close` / `actual_direction` / `anchor` (outcome or window-specific) as features.
  * Snapshots within one window share the outcome -> a random split LEAKS. Split TEMPORALLY
    by `window_start_ms` (train=oldest, calib=middle, test=newest) so test windows are
    fully unseen AND it measures generalization to the future.
  * Persistence is up/down SYMMETRIC -> use |distance| (sign carries no extra signal).

Headline metric: at a high P(hold) threshold, the REALIZED hold rate on the held-out test
set (the honest T3-tier precision), with coverage.

Usage:  python backend/train_persistence_model.py
"""
from __future__ import annotations

import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
IN_PATH = os.path.join(DATA_DIR, "persistence_dataset.parquet")
OUT_PATH = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "persistence_model.pkl",
)
HEAD_VERSION = "2026-07-03-keeper-dual-perhorizon-iso-prodrefit"   # train_heads.py retrains when this changes

FEATURES = ["abs_distance_pct", "seconds_left", "vol_60s_pct", "horizon", "dist_vol_ratio"]
# Volatility KEEPERS — validated to LIFT P(hold) (+0.0135 AUC overall, +0.027 on the late
# T3 subset; phold_keeper_test.py). Trained as a SECOND model saved alongside the base one;
# the live serve path keeps using the base model until the keepers are plumbed into
# price_to_beat (no breakage). Joined from research_matrix_1m.parquet at the current minute.
KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "vpin", "compression_ratio", "shock_magnitude"]


def _add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["abs_distance_pct"] = df["distance_pct"].abs()
    # how many trailing-60s-vol units the price is ahead — the core persistence predictor
    df["dist_vol_ratio"] = df["abs_distance_pct"] / (df["vol_60s_pct"] + 1e-6)
    return df


def _ece(p, y, bins=10):
    """Expected Calibration Error (same binning as calibration_monitor.py) — lower is better."""
    p = np.asarray(p, float); y = np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, bins + 1); e = 0.0
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum():
            e += (m.sum() / len(p)) * abs(float(p[m].mean()) - float(y[m].mean()))
    return e


def _fit_iso_by_horizon(clf, ca: pd.DataFrame, feat_cols, min_rows=2000):
    """Per-horizon isotonic on the (temporally-separate) CALIB slice. The global isotonic over-/under-
    states some horizons (1m drifts, ECE 0.0545); a per-horizon mapping fixes that. Horizons with too
    few calib rows are OMITTED -> the serve path falls back to the global isotonic for them. Leak-free:
    ca is the held-out calibration window, never the train rows."""
    raw = clf.predict_proba(ca[feat_cols].values)[:, 1]
    yca = ca["label"].values; hca = ca["horizon"].values
    out = {}
    for h in np.unique(hca):
        m = hca == h
        if int(m.sum()) < min_rows or len(np.unique(yca[m])) < 2:
            continue
        iso_h = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso_h.fit(raw[m], yca[m])
        out[int(h)] = iso_h
    return out


def _join_keepers(df: pd.DataFrame) -> pd.DataFrame | None:
    """Join the volatility keepers at each row's CURRENT minute. Returns None if the
    research matrix is absent (keeper model is then skipped — base model unaffected)."""
    mpath = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
    if not os.path.exists(mpath):
        return None
    m = pd.read_parquet(mpath, columns=["ts_ms"] + KEEPERS).replace([np.inf, -np.inf], np.nan)
    d = df.copy()
    d["cur_min_ms"] = (((d["window_start_ms"] + d["seconds_elapsed"] * 1000) // 60000) * 60000).astype("int64")
    out = d.merge(m.rename(columns={"ts_ms": "cur_min_ms"}), on="cur_min_ms", how="inner")
    return out.dropna(subset=KEEPERS)


def _train_keeper_model(df: pd.DataFrame, base_clf, base_iso):
    """Train base+keeper model; return (bundle_extra dict) or {} if not enough joined data.
    Reports the honest lift vs the base model on the SAME held-out test windows."""
    kdf = _join_keepers(df)
    if kdf is None or len(kdf) < 50_000:
        print("\n[keeper] research matrix absent or too few joined rows — keeper model SKIPPED.")
        return {}
    wins = np.sort(kdf["window_start_ms"].unique())
    n = len(wins)
    _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)  # 98/2: fit+cal=sf, test=1-sf
    tr_cut, ca_cut = wins[int(n * (2 * _sf - 1))], wins[int(n * _sf)]
    tr = kdf[kdf["window_start_ms"] < tr_cut]
    ca = kdf[(kdf["window_start_ms"] >= tr_cut) & (kdf["window_start_ms"] < ca_cut)]
    te = kdf[kdf["window_start_ms"] >= ca_cut]
    KF = FEATURES + KEEPERS
    kclf = HistGradientBoostingClassifier(max_iter=300, max_leaf_nodes=31, learning_rate=0.05,
                                          l2_regularization=0.1, random_state=42,
                                          validation_fraction=0.1)
    kclf.fit(tr[KF].values, tr["label"].values)
    kiso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    kiso.fit(kclf.predict_proba(ca[KF].values)[:, 1], ca["label"].values)
    kiso_by_horizon = _fit_iso_by_horizon(kclf, ca, KF)
    yte = te["label"].values
    kp = kiso.predict(kclf.predict_proba(te[KF].values)[:, 1])
    kauc = roc_auc_score(yte, kp)
    bp = base_iso.predict(base_clf.predict_proba(te[FEATURES].values)[:, 1])
    bauc = roc_auc_score(yte, bp)
    late = te["seconds_left"].values <= 120
    kauc_l = roc_auc_score(yte[late], kp[late]) if late.sum() > 500 else float("nan")
    bauc_l = roc_auc_score(yte[late], bp[late]) if late.sum() > 500 else float("nan")
    print(f"\n[keeper] joined={len(kdf):,}  test={len(te):,}")
    print(f"[keeper] AUC  base={bauc:.4f}  base+keepers={kauc:.4f}  lift={kauc-bauc:+.4f}")
    print(f"[keeper] T3 late (<=120s): base={bauc_l:.4f}  base+keepers={kauc_l:.4f}  lift={kauc_l-bauc_l:+.4f}")
    return {"clf_keeper": kclf, "iso_keeper": kiso, "iso_keeper_by_horizon": kiso_by_horizon,
            "features_keeper": KF,
            "keeper_test_auc": float(kauc), "keeper_base_auc": float(bauc),
            "keeper_test_auc_late": float(kauc_l), "keeper_base_auc_late": float(bauc_l)}


def main():
    if not os.path.exists(IN_PATH):
        raise SystemExit(f"missing {IN_PATH} — run build_persistence_dataset.py first")
    df = _add_features(pd.read_parquet(IN_PATH))
    df = df.dropna(subset=FEATURES + ["label"])
    print(f"loaded {len(df):,} snapshots, base hold-rate {df['label'].mean()*100:.1f}%")

    # TEMPORAL, leak-free split BY WINDOW (snapshots in a window share the outcome).
    wins = np.sort(df["window_start_ms"].unique())
    n = len(wins)
    _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)  # 98/2: fit+cal=sf, test=1-sf
    tr_cut, ca_cut = wins[int(n * (2 * _sf - 1))], wins[int(n * _sf)]
    tr = df[df["window_start_ms"] < tr_cut]
    ca = df[(df["window_start_ms"] >= tr_cut) & (df["window_start_ms"] < ca_cut)]
    te = df[df["window_start_ms"] >= ca_cut]
    print(f"split (by window, temporal): train={len(tr):,}  calib={len(ca):,}  test={len(te):,}")

    Xtr, ytr = tr[FEATURES].values, tr["label"].values
    clf = HistGradientBoostingClassifier(
        max_iter=300, max_leaf_nodes=31, learning_rate=0.05,
        l2_regularization=0.1, random_state=42, validation_fraction=0.1)
    clf.fit(Xtr, ytr)

    # Isotonic calibration on the (temporally separate) calib slice — leak-free.
    p_ca = clf.predict_proba(ca[FEATURES].values)[:, 1]
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p_ca, ca["label"].values)
    # Per-horizon isotonic (fixes the 1m drift) — global iso stays as the fallback for thin horizons.
    iso_by_horizon = _fit_iso_by_horizon(clf, ca, FEATURES)

    # ----- HONEST evaluation on the unseen TEST windows -----
    p_te = iso.predict(clf.predict_proba(te[FEATURES].values)[:, 1])
    yte = te["label"].values
    auc = roc_auc_score(yte, p_te)
    print(f"\nTEST  n={len(te):,}  AUC={auc:.4f}  base={yte.mean()*100:.1f}%")

    print("\nReliability (calibrated P(hold) -> realized):")
    for lo in (0.5, 0.7, 0.8, 0.9, 0.95):
        hi = lo + 0.05 if lo < 0.95 else 1.01
        m = (p_te >= lo) & (p_te < hi)
        if m.sum() >= 50:
            print(f"  P in [{lo:.2f},{hi:.2f}): realized={yte[m].mean()*100:5.1f}%  n={m.sum():,}")

    print("\nT3-tier precision (the headline) — realized hold at each P(hold) bar, with coverage:")
    for thr in (0.85, 0.90, 0.93, 0.95):
        m = p_te >= thr
        if m.sum():
            print(f"  P(hold) >= {thr:.2f}:  realized={yte[m].mean()*100:5.1f}%  "
                  f"coverage={m.mean()*100:5.1f}%  (n={m.sum():,})")

    # Per-horizon at the 0.93 bar (the bettable horizons)
    print("\nPer-horizon @ P(hold)>=0.93:")
    for h in sorted(te["horizon"].unique()):
        sub = te[te["horizon"] == h]
        ph = iso.predict(clf.predict_proba(sub[FEATURES].values)[:, 1])
        m = ph >= 0.93
        ys = sub["label"].values
        if m.sum():
            print(f"  {int(h):>2}m: realized={ys[m].mean()*100:5.1f}%  n={m.sum():,} "
                  f"({m.mean()*100:.0f}% of {len(sub):,})")

    # Per-horizon CALIBRATION — the headline of this change: global iso vs per-horizon iso on TEST.
    print("\nPer-horizon calibration (TEST ECE: global iso -> per-horizon iso; lower=better):")
    raw_te_all = clf.predict_proba(te[FEATURES].values)[:, 1]
    yte_all = te["label"].values; hte = te["horizon"].values
    for h in sorted(te["horizon"].unique()):
        m = hte == h
        eg = _ece(iso.predict(raw_te_all[m]), yte_all[m])
        iso_h = iso_by_horizon.get(int(h))
        ep = _ece(iso_h.predict(raw_te_all[m]), yte_all[m]) if iso_h is not None else eg
        tag = "" if iso_h is not None else "  (too few calib rows -> uses global)"
        print(f"  {int(h):>2}m: ECE {eg:.4f} -> {ep:.4f}  n={int(m.sum()):,}{tag}")

    # Baseline the naive heuristic for comparison (the old flat rule)
    naive = (te["seconds_left"] <= 60) & (te["distance"].abs() >= 10)
    if naive.sum():
        print(f"\nNaive heuristic (<=60s & >=$10 ahead): {yte[naive].mean()*100:.1f}%  "
              f"coverage={naive.mean()*100:.1f}% — the model should match this AND extend "
              f"high-precision coverage to more setups.")

    # Keeper model (additive; live serve keeps using the base model until keepers are plumbed).
    keeper_extra = _train_keeper_model(df, clf, iso)

    # ── VALIDATED PRODUCTION REFIT (rotation, 2026-07-03) ────────────────────────────────────
    # The candidate above (fit = first 96% of windows, cal 96-98%, TEST 98-100%) supplies the honest
    # report printed/persisted above. If it clears the predeclared gate, the SERVED model rotates
    # forward: clf refit on the first 98% of windows (so live P(hold) has also learned the old test
    # span) and the isotonics recalibrated on the FRESHEST 2% -- which the refit clf never saw
    # (a final calibration tail is structurally required for honest isotonic; it is not waste).
    # Gate miss -> serve the measured candidate unchanged. Disable with BTC_HEAD_REFIT_ALL=0.
    refit_on_all = bool(auc >= 0.70 and os.environ.get("BTC_HEAD_REFIT_ALL", "1") != "0")
    if refit_on_all:
        fit_p = df[df["window_start_ms"] < ca_cut]
        cal_p = df[df["window_start_ms"] >= ca_cut]
        clf = HistGradientBoostingClassifier(
            max_iter=300, max_leaf_nodes=31, learning_rate=0.05,
            l2_regularization=0.1, random_state=42, validation_fraction=0.1)
        clf.fit(fit_p[FEATURES].values, fit_p["label"].values)
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(clf.predict_proba(cal_p[FEATURES].values)[:, 1], cal_p["label"].values)
        iso_by_horizon = _fit_iso_by_horizon(clf, cal_p, FEATURES)
        print(f"\n[refit] production rotation: fit={len(fit_p):,} rows (incl. old cal span), "
              f"iso on freshest {len(cal_p):,} rows; candidate TEST metrics above are the record.")
        if keeper_extra:
            kdf = _join_keepers(df)
            if kdf is not None and len(kdf) >= 50_000:
                KF = keeper_extra["features_keeper"]
                kfit = kdf[kdf["window_start_ms"] < ca_cut]
                kcal = kdf[kdf["window_start_ms"] >= ca_cut]
                kclf = HistGradientBoostingClassifier(
                    max_iter=300, max_leaf_nodes=31, learning_rate=0.05,
                    l2_regularization=0.1, random_state=42, validation_fraction=0.1)
                kclf.fit(kfit[KF].values, kfit["label"].values)
                kiso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
                kiso.fit(kclf.predict_proba(kcal[KF].values)[:, 1], kcal["label"].values)
                keeper_extra.update({"clf_keeper": kclf, "iso_keeper": kiso,
                                     "iso_keeper_by_horizon": _fit_iso_by_horizon(kclf, kcal, KF)})
                print("[refit] keeper twin rotated on the same fit/cal windows.")

    bundle = {"clf": clf, "iso": iso, "iso_by_horizon": iso_by_horizon, "features": FEATURES,
              "trained_rows": int(len(tr)), "test_auc": float(auc), "version": HEAD_VERSION,
              "refit_on_all": refit_on_all,
              "note": "P(hold|abs_distance_pct,seconds_left,vol_60s_pct,horizon,dist_vol_ratio); "
                      "per-horizon isotonic (global iso fallback for thin horizons)"}
    bundle.update(keeper_extra)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    _tmp = f"{OUT_PATH}.tmp.{os.getpid()}"
    try:
        joblib.dump(bundle, _tmp)
        write_integrity_manifest(_tmp)
        os.replace(_tmp, OUT_PATH)
        write_integrity_manifest(OUT_PATH)
    finally:
        if os.path.exists(_tmp):
            os.remove(_tmp)
    print(f"\nSaved -> {OUT_PATH}  (keeper model: {'yes' if keeper_extra else 'no'}; "
          f"production refit: {'yes' if refit_on_all else 'NO -- candidate served'})")


if __name__ == "__main__":
    main()
