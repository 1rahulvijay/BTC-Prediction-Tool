"""
train_path_forecaster.py - the PATH head: an ENSEMBLE Layer-2 model (parallel to, NOT merged
into, the frozen direction ensemble).
===========================================================================================
Predicts, ONCE near window open from the parity-proven vol keepers, the intra-window PATH a
Polymarket early-exit trader needs. Each target is an ENSEMBLE of 3 boosting libraries
(CatBoost + LightGBM + HistGBM), averaged -- a parallel ensemble layer:

  * predicted HIGH / LOW band  -> ensemble QUANTILE regression (P25/P50/P75) + CONFORMAL (~50% cov)
  * P(touch >= $50/$100)        -> exact dollar labels + ensemble + isotonic calibration
  * ROUND-TRIP P(touch BOTH +/-$50)  -> the honest "up AND down" (AUC ~0.74-0.81)
  * touch +$50 & -$30 (asymmetric)   -> AUC ~0.72-0.79
  * |net move| magnitude (size, not sign)  -> skill ~0.18

Parity: features == live_keepers.KEEPER_NAMES (rv_15m/rv_30m/rv_60m/compression_ratio/
shock_magnitude). Leak-free: targets are the NEXT window's forward high/low/close.

Saves data/saved_models/path_forecaster.pkl; loaded read-only by price_to_beat.py.

Usage:
  python backend/train_path_forecaster.py            # train from research_matrix_1m.parquet
  python backend/train_path_forecaster.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX = os.path.join(ROOT, "data", "research_matrix_1m.parquet")
OUT = os.path.join(ROOT, "data", "saved_models", "path_forecaster.pkl")

FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
HORIZONS = (5, 15)
QUANTILES = (0.25, 0.5, 0.75)
TOUCH_USD = (50.0, 100.0)
ROUNDTRIP_USD = 50.0
ASYM_HI_USD, ASYM_LO_USD = 50.0, 30.0
EARLY_USD = 50.0   # "early touch" = a +/-$50 excursion within the FIRST HALF of the window (fade-setup)
ENSEMBLE = ("catboost", "lightgbm", "histgbm")
HEAD_VERSION = "2026-06-30-path-v3-usd-early"


# ----- ensemble member factories (3 boosting libraries) -----
def _q_models(alpha):
    from catboost import CatBoostRegressor
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingRegressor
    return [CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05,
                              loss_function=f"Quantile:alpha={alpha}", random_seed=0,
                              verbose=0, allow_writing_files=False),
            lgb.LGBMRegressor(objective="quantile", alpha=alpha, n_estimators=300, max_depth=4,
                              learning_rate=0.05, verbose=-1, n_jobs=2),
            HistGradientBoostingRegressor(loss="quantile", quantile=alpha, max_iter=300,
                                          learning_rate=0.05, max_depth=4, random_state=0)]


def _clf_models():
    from catboost import CatBoostClassifier
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingClassifier
    return [CatBoostClassifier(iterations=250, depth=4, learning_rate=0.05, random_seed=0,
                               verbose=0, allow_writing_files=False),
            lgb.LGBMClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, verbose=-1, n_jobs=2),
            HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_depth=4, random_state=0)]


def _reg_models():
    from catboost import CatBoostRegressor
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingRegressor
    return [CatBoostRegressor(iterations=250, depth=4, learning_rate=0.05, random_seed=0,
                              verbose=0, allow_writing_files=False),
            lgb.LGBMRegressor(n_estimators=250, max_depth=4, learning_rate=0.05, verbose=-1, n_jobs=2),
            HistGradientBoostingRegressor(max_iter=250, learning_rate=0.05, max_depth=4, random_state=0)]


def _fit_all(models, X, y):
    for m in models:
        m.fit(X, y)
    return models


def ens_pred(models, X):
    return np.mean([m.predict(X) for m in models], axis=0)


def ens_proba(models, X):
    return np.mean([m.predict_proba(X)[:, 1] for m in models], axis=0)


def _future_hl(df, h):
    return df["high"].rolling(h).max().shift(-h), df["low"].rolling(h).min().shift(-h)


def _fit_iso(p, y):
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip"); iso.fit(p, y); return iso


def train():
    from sklearn.metrics import roc_auc_score
    import joblib
    df = pd.read_parquet(MATRIX)
    required = set(FEATURES + ["ts_ms", "close", "high", "low"])
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"research matrix missing path-forecaster columns: {missing}")
    # Parquet row order is not a training contract. Make the temporal split explicit and
    # deterministic before constructing forward labels.
    df = (df.sort_values("ts_ms")
            .drop_duplicates(subset=["ts_ms"], keep="last")
            .reset_index(drop=True))
    if "future_high_5m" in df.columns:
        fhi5, _ = _future_hl(df, 5)
        m = df["future_high_5m"].notna() & fhi5.notna()
        print(f"[parity] future_high_5m vs matrix median|diff|={ (fhi5[m]-df['future_high_5m'][m]).abs().median():.4f}")
    bundle = {"version": HEAD_VERSION, "features": FEATURES, "ensemble": list(ENSEMBLE),
              "quantiles": list(QUANTILES), "threshold_units": "usd",
              "touch_usd": list(TOUCH_USD), "roundtrip_usd": ROUNDTRIP_USD,
              "asym_usd": [ASYM_HI_USD, ASYM_LO_USD], "early_usd": EARLY_USD,
              "horizons": {}, "trained": time.time(), "n": int(len(df))}
    for h in HORIZONS:
        fhi, flo = _future_hl(df, h)
        fc = df["close"].shift(-h)
        c = df["close"]
        up = (fhi / c - 1.0) * 1e4; dn = (flo / c - 1.0) * 1e4; net = (fc / c - 1.0) * 1e4
        up_usd = fhi - c
        down_usd = c - flo
        # First-half extremes -> the EARLY-TOUCH (fade-setup) target: a +/-$50 excursion within the
        # first floor(h/2) minutes, so a fade has room to play out (probe AUC 0.75/0.83).
        ehi, elo = _future_hl(df, max(1, h // 2))
        eu_usd = ehi - c
        ed_usd = c - elo
        d = pd.concat([df[FEATURES], up.rename("up"), dn.rename("dn"), net.rename("net"),
                       up_usd.rename("up_usd"), down_usd.rename("down_usd"),
                       eu_usd.rename("eu_usd"), ed_usd.rename("ed_usd")],
                      axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        X = d[FEATURES].values; yu, yd, ynet = d["up"].values, d["dn"].values, d["net"].values
        yup_usd, ydown_usd = d["up_usd"].values, d["down_usd"].values
        yeu_usd, yed_usd = d["eu_usd"].values, d["ed_usd"].values
        n = len(d); a, b = int(n * 0.70), int(n * 0.85)
        Xtr, Xcal, Xte = X[:a], X[a:b], X[b:]
        hz = {"qhi": {}, "qlo": {}, "touch": {}, "conformal": {}, "metrics": {}}
        for q in QUANTILES:
            hz["qhi"][q] = _fit_all(_q_models(q), Xtr, yu[:a])
            hz["qlo"][q] = _fit_all(_q_models(q), Xtr, yd[:a])
        # conformal on the ensemble-averaged [0.25,0.75] band
        e_up = np.maximum(ens_pred(hz["qhi"][0.25], Xcal) - yu[a:b], yu[a:b] - ens_pred(hz["qhi"][0.75], Xcal))
        e_dn = np.maximum(ens_pred(hz["qlo"][0.25], Xcal) - yd[a:b], yd[a:b] - ens_pred(hz["qlo"][0.75], Xcal))
        hz["conformal"] = {"up": float(np.quantile(e_up, 0.5)), "dn": float(np.quantile(e_dn, 0.5))}

        def _clf_target(yt, key):
            models = _fit_all(_clf_models(), Xtr, yt[:a])
            iso = _fit_iso(ens_proba(models, Xcal), yt[a:b])
            try:
                auc = roc_auc_score(yt[b:], ens_proba(models, Xte))
            except ValueError:
                auc = float("nan")
            return {"models": models, "iso": iso, "auc": float(auc), "base": float(yt[b:].mean())}

        for dollars in TOUCH_USD:
            hz["touch"][dollars] = _clf_target(
                ((yup_usd >= dollars) | (ydown_usd >= dollars)).astype(int), f"touch_usd_{dollars:g}")
        hz["roundtrip"] = _clf_target(
            ((yup_usd >= ROUNDTRIP_USD) & (ydown_usd >= ROUNDTRIP_USD)).astype(int), "roundtrip_usd")
        hz["touch_asym"] = _clf_target(
            ((yup_usd >= ASYM_HI_USD) & (ydown_usd >= ASYM_LO_USD)).astype(int), "asym_usd")
        # EARLY touch: a +/-$50 extreme in the FIRST HALF -> "a fade is coming soon" (compose-live engine)
        hz["touch_early"] = _clf_target(
            ((yeu_usd >= EARLY_USD) | (yed_usd >= EARLY_USD)).astype(int), "touch_early")
        # |net move| magnitude (size, not sign)
        nm_models = _fit_all(_reg_models(), Xtr, np.abs(ynet[:a]))
        pred_nm = ens_pred(nm_models, Xte); ytr_nm = np.abs(ynet[b:])
        mse_nm = np.mean((ytr_nm - pred_nm) ** 2)
        mse_base = np.mean((ytr_nm - np.abs(ynet[:a]).mean()) ** 2)
        hz["net_mag"] = {"models": nm_models, "skill": float(1 - mse_nm / (mse_base + 1e-30)),
                         "mae": float(np.mean(np.abs(ytr_nm - pred_nm)))}
        # coverage check
        cov_high = float(np.mean((yu[b:] >= ens_pred(hz["qhi"][0.25], Xte) - hz["conformal"]["up"]) &
                                 (yu[b:] <= ens_pred(hz["qhi"][0.75], Xte) + hz["conformal"]["up"])))
        cov_low = float(np.mean((yd[b:] >= ens_pred(hz["qlo"][0.25], Xte) - hz["conformal"]["dn"]) &
                                (yd[b:] <= ens_pred(hz["qlo"][0.75], Xte) + hz["conformal"]["dn"])))
        hz["metrics"] = {"band_coverage": float((cov_high + cov_low) / 2.0),
                         "high_band_coverage": cov_high, "low_band_coverage": cov_low,
                         "touch_auc": {l: hz["touch"][l]["auc"] for l in TOUCH_USD},
                         "roundtrip_auc": hz["roundtrip"]["auc"], "asym_auc": hz["touch_asym"]["auc"],
                         "net_mag_skill": hz["net_mag"]["skill"]}
        bundle["horizons"][h] = hz
        print(f"[{h}m] band_cov=high:{cov_high:.2f}/low:{cov_low:.2f} "
              f"touch_auc={[round(hz['touch'][l]['auc'],3) for l in TOUCH_USD]} "
              f"roundtrip={hz['roundtrip']['auc']:.3f} asym={hz['touch_asym']['auc']:.3f} "
              f"net_mag_skill={hz['net_mag']['skill']:+.3f}", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = f"{OUT}.tmp.{os.getpid()}"
    try:
        joblib.dump(bundle, tmp)
        os.replace(tmp, OUT)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print(f"saved -> {OUT} ({os.path.getsize(OUT)//1024} KB)  ensemble={ENSEMBLE}")
    return bundle


def selftest():
    rng = np.random.default_rng(0); n = 4000
    vol = np.abs(rng.normal(1, 0.3, n))
    df = pd.DataFrame({f: vol * (1 + i * .1) for i, f in enumerate(FEATURES)})
    df["close"] = 60000 + np.cumsum(rng.normal(0, 5, n))
    df["high"] = df["close"] + vol * 40; df["low"] = df["close"] - vol * 40
    fhi, _ = _future_hl(df, 5)
    ok = (fhi.notna().sum() > 1000 and TOUCH_USD == (50.0, 100.0)
          and "usd" in HEAD_VERSION and EARLY_USD == 50.0)
    print(f"selftest: ensemble factories={len(_clf_models())} libs, forward-high ok={ok}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX} -- run build_research_matrix.py first"); sys.exit(2)
    train()


if __name__ == "__main__":
    main()
