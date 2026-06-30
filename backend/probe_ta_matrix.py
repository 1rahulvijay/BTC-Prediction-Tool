"""
probe_ta_matrix.py - the "predict everything" day-trader TA matrix, measured HONESTLY.
=====================================================================================
Build a large classic-TA feature matrix (moving averages + distances, RSI/MACD/Stoch,
Bollinger, ATR, support/resistance + distance, volume, prev high/low, candle structure,
time, optional fractional-diff) on 5m and 15m BTC bars, then predict EVERY target a day
trader cares about: direction (up/down), next return, next close/high/low/volume, a
moving average, and big-move.

CEILING-MONITOR DISCIPLINE (so the verdict can never be a single-split false positive):
  * WALK-FORWARD: every metric is the mean over 6 expanding out-of-sample folds, not one split.
  * SHUFFLE-NULL: each target is also fit on a shuffled label; the real score must clear the
    shuffle null (mean + 3*std), or it's an artifact.
  * COST-AWARE: for a *tradeable* target (direction), "edge" requires walk-forward AUC >= 0.55
    -- a ~2-5c Polymarket spread eats anything below ~0.55. A robust 0.52-0.55 is labelled
    "real but SUB-COST (at ceiling)", NOT an edge.
  * regression skill = 1 - MSE_model/MSE_persistence  (>0 beats "tomorrow==today").

Leak-free: features use only bars up to the CURRENT completed bar t; every target is strictly
bar t+1. Temporal (no shuffle) folds -- random splits leak on time series.

Read-only; depends only on data/research/binance_updown_rounds.parquet + sklearn.

Usage:
  python backend/probe_ta_matrix.py                 # 5m + 15m full report (walk-forward)
  python backend/probe_ta_matrix.py --h 5           # one horizon
  python backend/probe_ta_matrix.py --fracdiff      # add fractional-diff(log-price) features
  python backend/probe_ta_matrix.py --selftest      # validate the harness on synthetic data
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROUNDS = os.path.join(ROOT, "data", "research", "binance_updown_rounds.parquet")

N_FOLDS = 6          # expanding walk-forward folds (out-of-sample)
COST_AUC = 0.55      # tradeable-direction bar: below this, the Polymarket spread eats the edge
SHUFFLE_REPS = 3     # label-shuffle repeats for the null


# --------------------------------------------------------------------------- data
def load_ohlcv(horizon: int) -> pd.DataFrame:
    df = pd.read_parquet(ROUNDS)
    df = df[df["horizon_min"] == horizon].copy()
    df["ts"] = pd.to_datetime(df["round_start"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    out = pd.DataFrame({
        "ts": df["ts"], "open": df["anchor_price"].astype(float),
        "high": df["round_high"].astype(float), "low": df["round_low"].astype(float),
        "close": df["expiry_close"].astype(float), "volume": df["round_volume"].astype(float),
    })
    return out.dropna().reset_index(drop=True)


# --------------------------------------------------------------------------- features
def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def _ffd_weights(d: float, thres: float = 1e-4, max_size: int = 100) -> np.ndarray:
    """Fixed-width fractional-diff weights (Lopez de Prado, AFML ch.5)."""
    w = [1.0]
    for k in range(1, max_size):
        w_ = -w[-1] * (d - k + 1) / k
        if abs(w_) < thres:
            break
        w.append(w_)
    return np.array(w[::-1])


def frac_diff(series: pd.Series, d: float = 0.4, thres: float = 1e-4) -> pd.Series:
    """Stationary-but-memory-preserving transform (attacks non-stationarity of price)."""
    w = _ffd_weights(d, thres)
    width = len(w) - 1
    s = series.values
    out = np.full(len(s), np.nan)
    for i in range(width, len(s)):
        out[i] = np.dot(w, s[i - width:i + 1])
    return pd.Series(out, index=series.index)


def build_features(df: pd.DataFrame, fracdiff: bool = False) -> pd.DataFrame:
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    f = pd.DataFrame(index=df.index)
    for k in (1, 2, 3, 6, 12, 24):
        f[f"ret_{k}"] = c.pct_change(k)
    f["logret_1"] = np.log(c / c.shift(1))
    smas = {}
    for n in (5, 10, 20, 50, 100, 200):
        smas[n] = c.rolling(n).mean()
        f[f"dist_sma_{n}"] = c / smas[n] - 1.0
    for n in (8, 21, 50):
        f[f"dist_ema_{n}"] = c / c.ewm(span=n, adjust=False).mean() - 1.0
    f["sma_5_20_cross"] = smas[5] / smas[20] - 1.0
    f["sma_20_50_cross"] = smas[20] / smas[50] - 1.0
    f["sma_50_200_cross"] = smas[50] / smas[200] - 1.0
    f["rsi_14"] = _rsi(c, 14)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    f["macd"] = macd / c
    f["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    f["stoch_k"] = (c - ll) / (hh - ll + 1e-12)
    f["williams_r"] = (hh - c) / (hh - ll + 1e-12)
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    f["atr_pct_14"] = tr.rolling(14).mean() / c
    f["realized_vol_20"] = c.pct_change().rolling(20).std()
    f["range_pct"] = (h - l) / c
    f["range_compression"] = (h - l) / ((h - l).rolling(20).mean() + 1e-12)
    sd20 = c.rolling(20).std()
    f["bb_pct"] = (c - smas[20]) / (2 * sd20 + 1e-12)
    f["bb_width"] = (4 * sd20) / (smas[20] + 1e-12)
    for n in (20, 50):
        rh, rl = h.rolling(n).max(), l.rolling(n).min()
        f[f"dist_to_res_{n}"] = rh / c - 1.0
        f[f"dist_to_sup_{n}"] = c / rl - 1.0
        f[f"pos_in_range_{n}"] = (c - rl) / (rh - rl + 1e-12)
    vsma = v.rolling(20).mean()
    f["vol_ratio_20"] = v / (vsma + 1e-12)
    f["vol_z_20"] = (v - vsma) / (v.rolling(20).std() + 1e-12)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    f["obv_slope_10"] = obv.diff(10) / (vsma * 10 + 1e-12)
    f["body"] = (c - o) / o
    f["upper_wick"] = (h - np.maximum(o, c)) / c
    f["lower_wick"] = (np.minimum(o, c) - l) / c
    f["gap"] = o / c.shift(1) - 1.0
    f["dist_prev_high"] = h.shift(1) / c - 1.0
    f["dist_prev_low"] = c / l.shift(1) - 1.0
    f["hour"] = df["ts"].dt.hour
    f["dow"] = df["ts"].dt.dayofweek
    if fracdiff:
        lp = np.log(c)
        for d in (0.3, 0.5, 0.7):
            f[f"fracdiff_{d}"] = frac_diff(lp, d)
    return f


def build_targets(df: pd.DataFrame) -> dict:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    sma20 = c.rolling(20).mean()
    absret = c.pct_change().abs()
    thr = absret.rolling(50).median() * 1.5
    # (name, kind, tradeable, y, naive_baseline)
    return {
        "direction_up_down": ("clf", True, (c.shift(-1) > c).astype(float), None),
        "next_return":       ("reg", True, c.pct_change().shift(-1), pd.Series(0.0, index=df.index)),
        "next_close":        ("reg", False, c.shift(-1), c),
        "next_high":         ("reg", False, h.shift(-1), h),
        "next_low":          ("reg", False, l.shift(-1), l),
        "next_volume":       ("reg", False, v.shift(-1), v),
        "next_sma20 (a MA)": ("reg", False, sma20.shift(-1), sma20),
        "big_move":          ("clf", False, (absret.shift(-1) > thr).astype(float), None),
    }


# --------------------------------------------------------------------------- models + walk-forward
def default_clf():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_depth=4, random_state=0)


def default_reg():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_depth=4, random_state=0)


def _auc(y, p):
    from sklearn.metrics import roc_auc_score
    try:
        return roc_auc_score(y, p)
    except ValueError:
        return float("nan")


def wf_clf(Xc, y, factory=default_clf, folds=N_FOLDS):
    """Expanding walk-forward; returns per-fold AUC/acc aggregates."""
    n = len(Xc)
    aucs, accs = [], []
    for k in range(10 - folds, 10):
        cut, te = int(n * k / 10.0), int(n * (k + 1) / 10.0)
        if te - cut < 50 or cut < 200:
            continue
        m = factory()
        m.fit(Xc.iloc[:cut], y.iloc[:cut])
        p = m.predict_proba(Xc.iloc[cut:te])[:, 1]
        aucs.append(_auc(y.iloc[cut:te], p))
        accs.append(float(((p >= 0.5).astype(int) == y.iloc[cut:te].astype(int)).mean()))
    a = np.array([x for x in aucs if x == x])
    return {"auc": float(a.mean()) if len(a) else float("nan"),
            "auc_std": float(a.std()) if len(a) else float("nan"),
            "above_half": int((a > 0.5).sum()), "n_folds": len(a),
            "acc": float(np.mean(accs)) if accs else float("nan")}


def shuffle_null_clf(Xc, y, factory=default_clf, reps=SHUFFLE_REPS):
    rng = np.random.default_rng(0)
    n = len(Xc); cut = int(n * 0.8)
    nulls = []
    for _ in range(reps):
        ysh = y.values.copy(); rng.shuffle(ysh)
        m = factory(); m.fit(Xc.iloc[:cut], ysh[:cut])
        nulls.append(_auc(ysh[cut:], m.predict_proba(Xc.iloc[cut:])[:, 1]))
    nn = np.array([x for x in nulls if x == x])
    return float(nn.mean()) if len(nn) else 0.5, float(nn.std()) if len(nn) else 0.0


def wf_reg(Xc, y, base, factory=default_reg, folds=N_FOLDS):
    n = len(Xc); skills, r2s = [], []
    for k in range(10 - folds, 10):
        cut, te = int(n * k / 10.0), int(n * (k + 1) / 10.0)
        if te - cut < 50 or cut < 200:
            continue
        m = factory(); m.fit(Xc.iloc[:cut], y.iloc[:cut])
        pred = m.predict(Xc.iloc[cut:te])
        yte = y.iloc[cut:te].values
        mse_m = np.mean((yte - pred) ** 2)
        mse_b = np.mean((yte - base.iloc[cut:te].values) ** 2)
        skills.append(1 - mse_m / (mse_b + 1e-30))
        r2s.append(1 - mse_m / (np.var(yte) + 1e-30))
    return {"skill": float(np.mean(skills)) if skills else float("nan"),
            "r2": float(np.mean(r2s)) if r2s else float("nan")}


def _verdict_clf(tradeable, agg, null_mean, null_std):
    auc, robust = agg["auc"], agg["above_half"] >= max(1, agg["n_folds"] - 1)
    clears_null = auc > null_mean + 3 * null_std
    if not (robust and clears_null and auc > 0.515):
        return "coin-flip / ceiling"
    if tradeable:
        return "TRADEABLE EDGE" if auc >= COST_AUC else "real but SUB-COST (at ceiling)"
    return "REAL (selectivity/timing)"


def evaluate(df: pd.DataFrame, fracdiff: bool = False) -> list:
    X = build_features(df, fracdiff=fracdiff)
    rows = []
    for name, (kind, tradeable, y, base) in build_targets(df).items():
        cols = [X, y.rename("__y__")] + ([base.rename("__b__")] if base is not None else [])
        d = pd.concat(cols, axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) < 800:
            continue
        Xc = d[X.columns]; yy = d["__y__"]
        if kind == "clf":
            agg = wf_clf(Xc, yy)
            nm, ns = shuffle_null_clf(Xc, yy)
            verdict = _verdict_clf(tradeable, agg, nm, ns)
            rows.append((name, f"AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f}",
                         f"null {nm:.3f}", f"{agg['above_half']}/{agg['n_folds']} folds>0.5",
                         f"acc {agg['acc']:.3f}", verdict))
        else:
            agg = wf_reg(Xc, yy, d["__b__"])
            verdict = ("REAL EDGE" if agg["skill"] > 0.02
                       else "trivial / priced-in (no lift over naive)")
            rows.append((name, f"skill {agg['skill']:+.3f}", "vs persistence",
                         f"R^2 {agg['r2']:+.3f}", "", verdict))
    return rows


def _print(title, rows):
    print("\n" + "=" * 104)
    print(title)
    print(f"{'target':<20}{'walk-fwd metric':<22}{'null/base':<14}{'robustness':<20}{'detail':<12}verdict")
    print("-" * 104)
    for r in rows:
        print(f"{r[0]:<20}{r[1]:<22}{r[2]:<14}{r[3]:<20}{r[4]:<12}{r[5]}")


# --------------------------------------------------------------------------- selftest
def selftest():
    rng = np.random.default_rng(0)
    n = 5000
    x = np.cumsum(rng.normal(0, 1, n)) + 5000
    df = pd.DataFrame({"ts": pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"),
                       "open": x, "high": x + 0.5, "low": x - 0.5, "close": x,
                       "volume": np.abs(rng.normal(100, 20, n))})
    X = build_features(df).replace([np.inf, -np.inf], np.nan)
    # noise label -> walk-forward AUC ~0.5 and must NOT clear the shuffle null
    yn = pd.Series((rng.normal(0, 1, n) > 0).astype(float))
    d = pd.concat([X, yn.rename("y")], axis=1).dropna()
    agg = wf_clf(d[X.columns], d["y"]); nm, ns = shuffle_null_clf(d[X.columns], d["y"])
    v = _verdict_clf(True, agg, nm, ns)
    # learnable reg label (next rsi) -> positive skill
    ys = X["rsi_14"].shift(-1)
    d2 = pd.concat([X, ys.rename("y"), X["rsi_14"].rename("b")], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    r = wf_reg(d2[X.columns], d2["y"], d2["b"])
    print(f"selftest: noise-direction walk-fwd AUC={agg['auc']:.3f} verdict='{v}' (expect coin-flip)")
    print(f"          learnable-reg skill={r['skill']:+.3f} (expect > 0)")
    ok = ("ceiling" in v) and (r["skill"] > 0.0)
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=None)
    ap.add_argument("--fracdiff", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(ROUNDS):
        print(f"missing OHLCV source: {ROUNDS}"); sys.exit(2)
    for hz in ([a.h] if a.h else [5, 15]):
        df = load_ohlcv(hz)
        rows = evaluate(df, fracdiff=a.fracdiff)
        tag = " +fracdiff" if a.fracdiff else ""
        _print(f"BTC {hz}m{tag}  -  walk-forward {N_FOLDS}-fold, shuffle-null, cost-aware  (n={len(df)})", rows)
    print(f"\nVERDICT KEY: direction needs walk-forward AUC>={COST_AUC} to be TRADEABLE (Polymarket spread); "
          "0.515-0.55 robust = real but SUB-COST = the ceiling. Regression 'skill'>0 beats 'tomorrow==today'.")


if __name__ == "__main__":
    main()
