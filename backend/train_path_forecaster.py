"""
train_path_forecaster.py - the PATH head: an ENSEMBLE Layer-2 model (parallel to, NOT merged
into, the frozen direction ensemble).
===========================================================================================
Predicts, ONCE near window open from the parity-proven vol keepers, the intra-window PATH a
Polymarket early-exit trader needs. Each target is an ENSEMBLE of 3 boosting libraries
(CatBoost + LightGBM + HistGBM), averaged -- a parallel ensemble layer:

  * predicted HIGH / LOW band  -> ensemble QUANTILE regression (P25/P50/P75) + CONFORMAL (~50% cov)
  * P(touch >= $50/$100 now)    -> price-normalized historical labels + ensemble + isotonic calibration
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

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "path_forecaster.pkl",
)

FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
HORIZONS = (5, 15)
QUANTILES = (0.25, 0.5, 0.75)
TOUCH_USD = (50.0, 100.0)
ROUNDTRIP_USD = 50.0
ASYM_HI_USD, ASYM_LO_USD = 50.0, 30.0
EARLY_USD = 50.0   # "early touch" = a +/-$50 excursion within the FIRST HALF of the window (fade-setup)
ENSEMBLE = ("catboost", "lightgbm", "histgbm")
# v4 (2026-07-03): classifier LABELS are built in BPS of each row's own price (the $50/$100/$30
# nominals are converted at the LATEST training price and applied relatively). Rationale: fixed-$
# labels drift with BTC's price level (2x base-rate swing per quarter measured at 400d; 7.5x over a
# 1200-1500d window spanning $15.5k..$115k). Bundle keys / serve code / UI semantics are UNCHANGED
# (touch heads still keyed 50.0/100.0; "P(moves>=$50)" now means the $50-equivalent-at-train event,
# applied consistently across all history). Quantile/band heads were already bps-relative.
HEAD_VERSION = "2026-07-03-path-v4.1-bpslabels-prodrefit"


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
    # BPS conversion of the nominal $ thresholds at the latest training price. Labels below compare
    # each row's RELATIVE excursion (bps of its own close) to these -- price-level-proof labels.
    px_ref = float(df["close"].iloc[-1])
    _to_bps = lambda usd: usd / px_ref * 1e4
    touch_bps = {usd: _to_bps(usd) for usd in TOUCH_USD}
    rt_bps = _to_bps(ROUNDTRIP_USD)
    asym_hi_bps, asym_lo_bps = _to_bps(ASYM_HI_USD), _to_bps(ASYM_LO_USD)
    early_bps = _to_bps(EARLY_USD)
    bundle = {"version": HEAD_VERSION, "features": FEATURES, "ensemble": list(ENSEMBLE),
              "quantiles": list(QUANTILES), "threshold_units": "usd",
              "touch_usd": list(TOUCH_USD), "roundtrip_usd": ROUNDTRIP_USD,
              "asym_usd": [ASYM_HI_USD, ASYM_LO_USD], "early_usd": EARLY_USD,
              "label_basis": "bps", "px_ref": px_ref,
              "touch_bps": {k: round(v, 3) for k, v in touch_bps.items()},
              "horizons": {}, "trained": time.time(), "n": int(len(df))}
    for h in HORIZONS:
        fhi, flo = _future_hl(df, h)
        fc = df["close"].shift(-h)
        c = df["close"]
        up = (fhi / c - 1.0) * 1e4; dn = (flo / c - 1.0) * 1e4; net = (fc / c - 1.0) * 1e4
        dn_pos = (1.0 - flo / c) * 1e4            # downward excursion in POSITIVE bps of the row's price
        # First-half extremes -> the EARLY-TOUCH (fade-setup) target: a +/-$50-equivalent excursion
        # within the first floor(h/2) minutes, so a fade has room to play out (probe AUC 0.75/0.83).
        ehi, elo = _future_hl(df, max(1, h // 2))
        eu_bps = (ehi / c - 1.0) * 1e4
        ed_bps = (1.0 - elo / c) * 1e4
        d = pd.concat([df[FEATURES], up.rename("up"), dn.rename("dn"), net.rename("net"),
                       dn_pos.rename("dn_pos"), eu_bps.rename("eu_bps"), ed_bps.rename("ed_bps")],
                      axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        X = d[FEATURES].values; yu, yd, ynet = d["up"].values, d["dn"].values, d["net"].values
        yup_bps, ydown_bps = d["up"].values, d["dn_pos"].values
        yeu_bps, yed_bps = d["eu_bps"].values, d["ed_bps"].values
        # 98/2 split via BTC_TRAIN_SPLIT_FRAC (sf): fit=2*sf-1, conformal-cal=1-sf, test=1-sf
        # -> fit+cal = sf = 98% TRAINING, test = 2%. Matches the direction ensemble + keeper heads.
        _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)
        n = len(d); a, b = int(n * (2 * _sf - 1)), int(n * _sf)
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

        for dollars in TOUCH_USD:   # keys stay in nominal $ (serve/UI contract); labels compare in bps
            t = touch_bps[dollars]
            hz["touch"][dollars] = _clf_target(
                ((yup_bps >= t) | (ydown_bps >= t)).astype(int), f"touch_usd_{dollars:g}")
        hz["roundtrip"] = _clf_target(
            ((yup_bps >= rt_bps) & (ydown_bps >= rt_bps)).astype(int), "roundtrip_usd")
        hz["touch_asym"] = _clf_target(
            ((yup_bps >= asym_hi_bps) & (ydown_bps >= asym_lo_bps)).astype(int), "asym_usd")
        # EARLY touch: a $50-equivalent extreme in the FIRST HALF -> "a fade is coming soon"
        hz["touch_early"] = _clf_target(
            ((yeu_bps >= early_bps) | (yed_bps >= early_bps)).astype(int), "touch_early")
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
        print(f"[{h}m] band_cov=high:{cov_high:.2f}/low:{cov_low:.2f} "
              f"touch_auc={[round(hz['touch'][l]['auc'],3) for l in TOUCH_USD]} "
              f"roundtrip={hz['roundtrip']['auc']:.3f} asym={hz['touch_asym']['auc']:.3f} "
              f"net_mag_skill={hz['net_mag']['skill']:+.3f}", flush=True)
        # ── VALIDATED PRODUCTION REFIT (rotation, 2026-07-03) ──────────────────────────────
        # Candidate metrics above (fit 96% / cal 2% / TEST 2%) are the honest record kept in the
        # bundle. If they clear the predeclared gate, the SERVED models refit through the old cal
        # span (first 98%) with conformal + isotonic on the FRESHEST 2% (the old test slice, which
        # the refit models never saw). Gate miss -> serve the measured candidate. BTC_HEAD_REFIT_ALL=0
        # disables. Post-refit honesty = the live path-plan scorecard (permanent shadow layer).
        _gate = (hz["metrics"]["touch_auc"].get(50.0, 0.0) >= 0.65
                 and 0.35 <= hz["metrics"]["band_coverage"] <= 0.65)
        if _gate and os.environ.get("BTC_HEAD_REFIT_ALL", "1") != "0":
            Xf, Xc = X[:b], X[b:]
            for q in QUANTILES:
                hz["qhi"][q] = _fit_all(_q_models(q), Xf, yu[:b])
                hz["qlo"][q] = _fit_all(_q_models(q), Xf, yd[:b])
            e_up2 = np.maximum(ens_pred(hz["qhi"][0.25], Xc) - yu[b:], yu[b:] - ens_pred(hz["qhi"][0.75], Xc))
            e_dn2 = np.maximum(ens_pred(hz["qlo"][0.25], Xc) - yd[b:], yd[b:] - ens_pred(hz["qlo"][0.75], Xc))
            hz["conformal"] = {"up": float(np.quantile(e_up2, 0.5)), "dn": float(np.quantile(e_dn2, 0.5))}

            def _refit_clf(entry, yt):
                models = _fit_all(_clf_models(), Xf, yt[:b])
                entry.update({"models": models, "iso": _fit_iso(ens_proba(models, Xc), yt[b:])})

            for dollars in TOUCH_USD:
                t = touch_bps[dollars]
                _refit_clf(hz["touch"][dollars], ((yup_bps >= t) | (ydown_bps >= t)).astype(int))
            _refit_clf(hz["roundtrip"], ((yup_bps >= rt_bps) & (ydown_bps >= rt_bps)).astype(int))
            _refit_clf(hz["touch_asym"], ((yup_bps >= asym_hi_bps) & (ydown_bps >= asym_lo_bps)).astype(int))
            _refit_clf(hz["touch_early"], ((yeu_bps >= early_bps) | (yed_bps >= early_bps)).astype(int))
            hz["net_mag"]["models"] = _fit_all(_reg_models(), Xf, np.abs(ynet[:b]))
            hz["refit_on_all"] = True
            print(f"[{h}m] production refit: models through {b:,}/{n:,} rows; "
                  f"conformal/iso on the freshest {n-b:,}", flush=True)
        else:
            hz["refit_on_all"] = False
            if not _gate:
                print(f"[{h}m] refit gate FAILED -- serving the measured candidate unchanged", flush=True)
        bundle["horizons"][h] = hz
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = f"{OUT}.tmp.{os.getpid()}"
    try:
        joblib.dump(bundle, tmp)
        write_integrity_manifest(tmp)
        os.replace(tmp, OUT)
        write_integrity_manifest(OUT)
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
    probe = pd.DataFrame({"high": [10.0, 20.0, 30.0, 5.0],
                          "low": [10.0, 9.0, 8.0, 7.0]})
    probe_hi, probe_lo = _future_hl(probe, 2)
    aligned = (probe_hi.iloc[0] == 30.0 and probe_lo.iloc[0] == 8.0)
    ok = (fhi.notna().sum() > 1000 and TOUCH_USD == (50.0, 100.0)
          and "bpslabels" in HEAD_VERSION and EARLY_USD == 50.0 and aligned)
    print(f"selftest: ensemble factories={len(_clf_models())} libs, "
          f"future-window-aligned={aligned}, contract-ok={ok}")
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
