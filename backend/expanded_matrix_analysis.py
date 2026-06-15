"""
expanded_matrix_analysis.py — THE definitive ~145-feature x all-models x all-targets sweep.
=============================================================================================
Operator ask: build a 140-145 feature matrix and run every model on 5m & 15m for the price-to-beat
targets, with complete metrics + feature importances, so we can decide what to WIRE into the app.

WHY engineered features (not the app's raw 136): ~78/136 live features are dead-zero in backfill
(live-only microstructure). So we ENGINEER ~145 MEANINGFUL leak-free features from the available data
(OHLCV + flow + cross-venue): multi-horizon returns, rolling vol/mean, EMA ratios, RSI, MACD,
Bollinger, ATR, candle geometry, distance-from-extremes, stochastic/Williams, Parkinson vol, VWAP,
volume/flow dynamics, VPIN/CVD/basis dynamics + lags, cross-venue ratios, interactions, time-of-day.
All backward-looking (shift/rolling/ewm) -> leak-free. Targets from FORWARD close.

HONEST PRIOR (proven 9 ways): dir_beat stays ~0.50 (information ceiling — more features can't fix it);
big_move ~0.62-0.72 (the one real edge). Payoff: DEFINITIVE confirmation + the feature importances
that tell you what to wire for the TIMING/selectivity gate.

Saves data/expanded_matrix.parquet (reusable for wiring). Reuses model_bakeoff (14 models, run_horizon).
Usage:  python backend/expanded_matrix_analysis.py            # 5m + 15m
        python backend/expanded_matrix_analysis.py --selftest
"""
import argparse
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "expanded_matrix.parquet")
HORIZONS = (5, 15)
TARGETS = ("dir_beat", "big_move", "dwell_side")
_BANNED = ("future_", "ret_5m", "_label", "ts_ms", "timestamp")
BASE = ["volume", "trade_count", "taker_buy", "taker_sell", "rv_15m", "rv_30m", "rv_60m", "rv_term",
        "log_count", "log_vol", "count_accel_5m", "vol_accel", "vpin_15m", "vpin_30m", "vpin_50m",
        "compression_ratio", "range_15m", "shock_magnitude", "micro_range_15m", "cvd_change",
        "cvd_1m", "cvd_5m", "delta", "vpin", "large_trade_delta", "large_trade_imbalance",
        "funding_velocity", "cvd_spot", "cvd_perp", "cvd_divergence", "perp_spot_basis_bps",
        "vol_spot", "vol_perp"]


def build_expanded_features(df):
    """~145 leak-free engineered features (dict-accumulated). All backward-looking (no future)."""
    import pandas as pd
    F = {}
    has = lambda c: c in df.columns                              # noqa: E731
    close, high, low, openp = df["close"], df["high"], df["low"], df["open"]
    vol, tc, tb, ts = df["volume"], df["trade_count"], df["taker_buy"], df["taker_sell"]
    logc = np.log(close.clip(lower=1.0)); r1 = logc.diff()
    for c in BASE:
        if has(c):
            F[c] = df[c]
    for k in (1, 2, 3, 5, 10, 15, 20, 30):
        F[f"ret_{k}b"] = logc.diff(k)
    for w in (5, 10, 20, 30, 60, 90):
        F[f"rvol_{w}"] = r1.rolling(w).std()
    for w in (5, 10, 20, 30):
        F[f"rmean_{w}"] = r1.rolling(w).mean()
    for w in (5, 10, 20, 60, 120):
        F[f"ema_ratio_{w}"] = close / close.ewm(span=w).mean() - 1.0
    for w in (7, 14):
        d = close.diff(); upg = d.clip(lower=0).rolling(w).mean(); dng = (-d.clip(upper=0)).rolling(w).mean()
        F[f"rsi_{w}"] = 100 - 100 / (1 + upg / (dng + 1e-9))
    e12, e26 = close.ewm(span=12).mean(), close.ewm(span=26).mean()
    macd = e12 - e26; sigl = macd.ewm(span=9).mean()
    F["macd"], F["macd_sig"], F["macd_hist"] = macd / close, sigl / close, (macd - sigl) / close
    sma20, std20 = close.rolling(20).mean(), close.rolling(20).std()
    F["boll_pos"] = (close - sma20) / (std20 + 1e-9); F["boll_width"] = std20 / (sma20 + 1e-9)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    F["atr_norm"] = atr / close; F["range_atr"] = (high - low) / (atr + 1e-9)
    F["hl_range"] = (high - low) / close
    F["range_pos"] = (close - low) / ((high - low) + 1e-9)
    F["body"] = (close - openp) / close
    F["upper_wick"] = (high - np.maximum(close, openp)) / close
    F["lower_wick"] = (np.minimum(close, openp) - low) / close
    for w in (15, 30, 60, 120):
        F[f"dist_hi_{w}"] = close / close.rolling(w).max() - 1.0
        F[f"dist_lo_{w}"] = close / close.rolling(w).min() - 1.0
    mn14, mx14 = close.rolling(14).min(), close.rolling(14).max()
    F["stoch_14"] = (close - mn14) / ((mx14 - mn14) + 1e-9)
    F["williams_14"] = (mx14 - close) / ((mx14 - mn14) + 1e-9)
    for k in (5, 10):
        F[f"roc_{k}"] = close / close.shift(k) - 1.0
    F["parkinson_15"] = np.sqrt((np.log(high / low.clip(lower=1.0)) ** 2).rolling(15).mean())
    F["ret_accel"] = r1 - r1.shift(5)
    vwap20 = (close * vol).rolling(20).sum() / (vol.rolling(20).sum() + 1e-9)
    F["vwap_dist"] = close / vwap20 - 1.0
    F["rvol_ratio_sl"] = r1.rolling(5).std() / (r1.rolling(60).std() + 1e-9)
    if has("rv_15m") and has("rv_60m"):
        F["rv_ratio_sl"] = df["rv_15m"] / (df["rv_60m"] + 1e-9)
    for w in (5, 15, 60):
        F[f"vol_ratio_{w}"] = vol / (vol.rolling(w).mean() + 1e-9)
    F["vol_z30"] = (vol - vol.rolling(30).mean()) / (vol.rolling(30).std() + 1e-9)
    F["tc_z30"] = (tc - tc.rolling(30).mean()) / (tc.rolling(30).std() + 1e-9)
    F["tc_ratio15"] = tc / (tc.rolling(15).mean() + 1e-9)
    imb = (tb - ts) / ((tb + ts) + 1e-9)
    F["taker_imb"] = imb
    for w in (5, 15):
        F[f"taker_imb_{w}"] = imb.rolling(w).mean()
    if has("delta"):
        cvd = df["delta"]
        for w in (5, 15, 30):
            F[f"cvd_roll_{w}"] = cvd.rolling(w).sum()
        F["cvd_slope"] = cvd.rolling(5).mean() - cvd.rolling(15).mean()
        F["cvd_z30"] = (cvd - cvd.rolling(30).mean()) / (cvd.rolling(30).std() + 1e-9)
    if has("cvd_change"):
        F["cvd_change_roll15"] = df["cvd_change"].rolling(15).sum()
    if has("vpin_15m"):
        vp = df["vpin_15m"]
        for k in (1, 3, 5):
            F[f"vpin_lag{k}"] = vp.shift(k)
        F["vpin_slope"] = vp.diff(3); F["vpin_accel"] = vp.diff().diff()
        F["vpin_z30"] = (vp - vp.rolling(30).mean()) / (vp.rolling(30).std() + 1e-9)
    if has("compression_ratio"):
        cr = df["compression_ratio"]
        for k in (1, 3, 5):
            F[f"compr_lag{k}"] = cr.shift(k)
        F["compr_chg"] = cr.diff(5)
    if has("shock_magnitude"):
        sh = df["shock_magnitude"]
        for k in (1, 3):
            F[f"shock_lag{k}"] = sh.shift(k)
        F["shock_max15"] = sh.rolling(15).max()
    if has("perp_spot_basis_bps"):
        b = df["perp_spot_basis_bps"]
        for k in (1, 3, 5):
            F[f"basis_lag{k}"] = b.shift(k)
        F["basis_chg"] = b.diff(3); F["basis_mean15"] = b.rolling(15).mean()
        F["basis_std15"] = b.rolling(15).std()
        F["basis_z30"] = (b - b.rolling(30).mean()) / (b.rolling(30).std() + 1e-9)
    if has("cvd_divergence"):
        cd = df["cvd_divergence"]
        for k in (1, 3, 5):
            F[f"cvddiv_lag{k}"] = cd.shift(k)
        F["cvddiv_chg"] = cd.diff(3); F["cvddiv_mean15"] = cd.rolling(15).mean()
    if has("cvd_spot") and has("cvd_perp"):
        F["cvd_sp_ratio"] = df["cvd_perp"] / (df["cvd_spot"].abs() + 1e-9)
    if has("vol_spot") and has("vol_perp"):
        F["vol_sp_ratio"] = df["vol_perp"] / (df["vol_spot"] + 1e-9)
    if has("funding_velocity"):
        fv = df["funding_velocity"]
        for k in (1, 3):
            F[f"funding_lag{k}"] = fv.shift(k)
        F["funding_chg"] = fv.diff(3)
    if has("large_trade_delta"):
        ltd = df["large_trade_delta"]
        F["ltd_roll15"] = ltd.rolling(15).sum(); F["ltd_lag1"] = ltd.shift(1)
    if has("vpin_15m") and has("compression_ratio"):
        F["vpin_x_compr"] = df["vpin_15m"] * df["compression_ratio"]
    if has("rv_15m") and has("perp_spot_basis_bps"):
        F["rv_x_basis"] = df["rv_15m"] * df["perp_spot_basis_bps"]
    if has("shock_magnitude") and has("vpin_15m"):
        F["shock_x_vpin"] = df["shock_magnitude"] * df["vpin_15m"]
    F["vol_x_range"] = F["vol_ratio_15"] * F["hl_range"]
    if "ts_ms" in df.columns:
        minute = (df["ts_ms"] // 60000)
        hour = (minute // 60) % 24; dow = ((minute // 1440) + 4) % 7
        F["hour_sin"] = np.sin(2 * np.pi * hour / 24); F["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        F["min_of_hr"] = (minute % 60) / 60.0
        F["asia"] = ((hour >= 0) & (hour < 8)).astype(float)
        F["eu"] = ((hour >= 7) & (hour < 15)).astype(float)
        F["us"] = ((hour >= 13) & (hour < 21)).astype(float)
        F["weekend"] = (dow >= 5).astype(float)
    return pd.DataFrame(F, index=df.index)


def run():
    import pandas as pd
    from model_bakeoff import make_light_models, run_horizon
    from final_analysis import build_targets, magnitude
    if not os.path.exists(MATRIX):
        sys.exit(f"missing {MATRIX}")
    raw = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    feat_df = build_expanded_features(raw)
    names = list(feat_df.columns)
    bad = [c for c in names if any(b in c for b in _BANNED)]
    assert not bad, f"LEAKAGE: {bad}"
    full = pd.concat([feat_df, raw["close"]], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    X = full[names].to_numpy(np.float64)
    close = full["close"].to_numpy(np.float64)
    feat_df.assign(close=raw["close"]).to_parquet(OUT)
    n_models = len(make_light_models())
    print(f"EXPANDED matrix: {X.shape[0]:,} rows x {len(names)} leak-free features -> saved {OUT}")
    print(f"{n_models} models | targets {TARGETS} | horizons {HORIZONS}\n")
    for h in HORIZONS:
        targets, move_bps = build_targets(close, h)
        print(f"\n{'='*80}\n  HORIZON {h}m  ({X.shape[0]:,} rows, {len(names)} features)\n{'='*80}")
        for tname in TARGETS:
            y = targets[tname]; mask = y >= 0
            Xv, yv = X[mask], y[mask]
            if len(yv) < 400 or len(np.unique(yv)) < 2:
                print(f"\n  [{tname}] insufficient"); continue
            res = run_horizon(Xv, yv, make_light_models(), names, calibrate=True)
            rows = sorted([(k, v["auc"]) for k, v in res.items()
                           if isinstance(v, dict) and "auc" in v], key=lambda r: -r[1])
            best = rows[0]; base = res.get("majority", {})
            sig = "SIGNAL" if best[1] >= 0.55 else "all NOISE"
            print(f"\n  [{tname}] base_rate={base.get('base_rate', 0):.2f} -> {sig} "
                  f"(best {best[0]} {best[1]:.3f})")
            for i in range(0, len(rows), 2):
                a = rows[i]; b = rows[i + 1] if i + 1 < len(rows) else ("", None)
                bs = f"{b[1]:.3f}" if b[1] is not None else ""
                print(f"      {a[0]:<15}{a[1]:>7.3f}    {b[0]:<15}{bs:>7}")
            if tname == "big_move":
                cnt = Counter()
                for k, v in res.items():
                    for fn, imp in (v.get("top_features") or []):
                        cnt[fn] += imp
                print(f"      -> TOP TIMING FEATURES (wire these): "
                      f"{', '.join(n for n, _ in cnt.most_common(12))}")
        mg = magnitude(X, move_bps)
        if mg:
            edge = "beats flat" if mg["pinball_model"] < mg["pinball_base"] else "~flat"
            print(f"\n  [MAGNITUDE] pinball {mg['pinball_model']:.3f} vs flat {mg['pinball_base']:.3f} "
                  f"({edge}); 80%-band coverage {mg['coverage80']*100:.1f}%; "
                  f"exp_drop {mg['exp_drop_bps']:+.1f} / exp_high {mg['exp_high_bps']:+.1f} bps")
    print("\nWIRE GUIDE: dir_beat ~0.50 -> DO NOT wire a direction model (proven). big_move SIGNAL ->")
    print("the TOP TIMING FEATURES are the selectivity gate to wire (P(big_move)); compose with P(hold)")
    print("for the side. Magnitude -> wire the calibrated band (conformal-widen to ~80%).")


def selftest():
    import pandas as pd
    rng = np.random.default_rng(0)
    n = 800
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame({"ts_ms": np.arange(n) * 60000, "open": close, "high": close + 1,
                       "low": close - 1, "close": close})
    for c in BASE:                                   # give the synthetic ALL base columns
        df[c] = rng.uniform(0.1, 5, n) if c not in df.columns else df[c]
    f = build_expanded_features(df)
    assert 138 <= f.shape[1] <= 150, f"expected ~145 features, got {f.shape[1]}"
    assert not [c for c in f.columns if any(b in c for b in _BANNED)], "leakage column present"
    # leak-free: changing FUTURE close must NOT alter PAST features
    df2 = df.copy(); df2.loc[600:, "close"] = df2.loc[600:, "close"] + 500
    f2 = build_expanded_features(df2)
    a = np.nan_to_num(f.iloc[:560].to_numpy()); b = np.nan_to_num(f2.iloc[:560].to_numpy())
    assert np.allclose(a, b), "LEAKAGE: changing FUTURE close altered PAST features"
    print(f"expanded_matrix_analysis self-test: ALL PASS ({f.shape[1]} features, leak-free)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
