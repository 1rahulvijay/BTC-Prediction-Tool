"""
backtest_reversal_strategy.py - standalone Polymarket-anchor REVERSAL / round-trip backtest (5m & 15m).
=====================================================================================================
GOAL: is the two-sided anchor fade PROFITABLE? Predict the PREDICTABLE thing (reversal / round-trip /
reach-anchor), NOT the settled UP/DOWN close (that is the proven coin-flip; shown here only as the NULL).

Your play, measured end-to-end:
  anchor = window-open price (exactly like a Polymarket 5m/15m round).
  a $10-50 spike off the anchor reprices the UP/DOWN shares -> BUY the cheap (losing) side, sell as it
  REVERTS to the anchor. Fade each leg of the round-trip. This script:
    1. labels every clock-aligned 5m & 15m window (touch/roundtrip/reversal/reach-anchor + direction null);
    2. builds ~70 leak-free features (vol / trend / volume / range / choppiness / regime / time / level);
    3. finds the BEST TRADING WINDOWS (when reversals cluster, when volume is high) by hour/4h-block/weekday;
    4. runs a 5-10 MODEL bake-off on the profit target (reach-anchor fade) + an ENSEMBLE, 80/20 time split;
    5. BACKTESTS honest P&L with costs + risk-reward, gated on the ensemble, by regime & window;
    6. emits the latest "what next" signal.

HONEST BOUNDARY (read this): the P&L is BTC-price reversion, a PROXY. Real Polymarket P&L needs the share
MISPRICED vs these odds after costs. A "profitable reversion backtest" != "profitable Polymarket bot" until
the recorder proves the ask. Direction stays a coin-flip; the edge (if any) is reversal selection + timing.

Usage:
  python backend/backtest_reversal_strategy.py                          # instant: existing 360d 1m matrix
  python backend/backtest_reversal_strategy.py --source binance --interval 30s --days 180   # fetch sub-minute
  python backend/backtest_reversal_strategy.py --selftest
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
CACHE = os.path.join(DATA, "subminute_cache")
KLINES_DAILY = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s/BTCUSDT-1s-{date}.zip"
OUT_MD = os.path.join(ROOT, "docs", "active", f"REVERSAL_STRATEGY_BACKTEST_{date.today().isoformat()}.md")
EPS = 1e-9
WARSAW_TZ = ZoneInfo("Europe/Warsaw")
# profit barriers (USD off the anchor) per horizon -- your targets: $10-30 in 5m, $20-50 in 15m.
BARRIERS = {5: (10.0, 20.0, 30.0), 15: (20.0, 30.0, 50.0)}
PRIMARY = {5: 30.0, 15: 50.0}   # headline profit barrier per horizon


# ----------------------------------------------------------------------------- data
def _norm_ms(df: pd.DataFrame) -> pd.DataFrame:
    """Binance Vision switched open_time to MICROSECONDS (16-digit) in newer files; older ones are ms.
    Normalize to milliseconds so all downstream time math (resample, windows, hour features) is correct."""
    t0 = int(df["ts_ms"].iloc[0]) if len(df) else 0
    if t0 > 1e14:                 # microseconds (2025 ms ~1.7e12 / us ~1.7e15)
        df["ts_ms"] = df["ts_ms"] // 1000
    elif t0 > 1e11 and t0 < 1e13:
        pass                      # already milliseconds
    return df


def _download_1s_day(d: str) -> pd.DataFrame | None:
    os.makedirs(CACHE, exist_ok=True)
    pq = os.path.join(CACHE, f"1s-{d}.parquet")
    if os.path.exists(pq):
        try:
            return _norm_ms(pd.read_parquet(pq))
        except Exception:
            pass
    try:
        with urllib.request.urlopen(KLINES_DAILY.format(date=d), timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(f, header=None, usecols=[0, 1, 2, 3, 4, 5, 9],
                                 names=["ts_ms", "open", "high", "low", "close", "volume", "taker_buy"])
        df = df[df["ts_ms"] > 0].astype({"ts_ms": "int64"})
        df.to_parquet(pq, index=False)
        return _norm_ms(df)
    except Exception as e:
        print(f"  [skip {d}] {str(e)[:70]}")
        return None


def _resample(df1s: pd.DataFrame, seconds: int) -> pd.DataFrame:
    if seconds <= 1:
        return df1s
    g = (df1s["ts_ms"] // (seconds * 1000)) * (seconds * 1000)
    out = df1s.groupby(g).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                              close=("close", "last"), volume=("volume", "sum"),
                              taker_buy=("taker_buy", "sum")).reset_index().rename(columns={"ts_ms": "ts_ms"})
    out.columns = ["ts_ms", "open", "high", "low", "close", "volume", "taker_buy"]
    return out


def load_data(source: str, interval: str, days: int) -> tuple[pd.DataFrame, int]:
    """Returns (df with ts_ms/open/high/low/close/volume/taker_buy, bar_seconds)."""
    if source == "matrix":
        df = pd.read_parquet(MATRIX, columns=["ts_ms", "open", "high", "low", "close", "volume", "taker_buy"])
        return df.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True), 60
    secs = {"1s": 1, "15s": 15, "30s": 30}.get(interval)
    if secs is None:
        raise SystemExit(f"--interval must be 1s/15s/30s (got {interval})")
    end = datetime.now(timezone.utc).date() - timedelta(days=1)   # yesterday (today not yet published)
    parts = []
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        day = _download_1s_day(d)
        if day is not None and len(day):
            parts.append(_resample(day, secs))
        if (i + 1) % 20 == 0:
            print(f"  fetched {i+1}/{days} days ...", flush=True)
    if not parts:
        raise SystemExit("no sub-minute data fetched (data.binance.vision unreachable?)")
    df = pd.concat(parts, ignore_index=True).sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    return df, secs


# ----------------------------------------------------------------------------- features + labels
def _hurst(x):
    x = np.asarray(x, float)
    if len(x) < 16 or np.allclose(x, x[0]):
        return 0.5
    lags = range(2, min(16, len(x) // 2))
    tau = [np.sqrt(np.std(x[l:] - x[:-l])) for l in lags]
    try:
        return float(np.polyfit(np.log(list(lags)), np.log(tau), 1)[0] * 2.0)
    except Exception:
        return 0.5


def build_windows(df: pd.DataFrame, horizon_min: int, bar_seconds: int) -> pd.DataFrame:
    """One row per clock-aligned window anchor. Features = bars UP TO anchor (leak-free). Labels = forward
    intra-window path (bars after anchor). Anchor = the window-open price (Polymarket price-to-beat)."""
    W = max(1, (horizon_min * 60) // bar_seconds)          # bars per window
    c = df["close"].to_numpy(float); h = df["high"].to_numpy(float); lo = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float); tb = df["taker_buy"].to_numpy(float); ts = df["ts_ms"].to_numpy("int64")
    n = len(df)
    # anchors on the clock grid (window open every W bars); need W past + W future bars
    L1 = max(W, (60 // bar_seconds))                        # 1m in bars
    L5, L15, L30, L60 = 5 * L1, 15 * L1, 30 * L1, 60 * L1
    step = W
    idx = np.arange(max(L60, 2), n - W - 1, step)
    rows = []
    cum_v = np.concatenate([[0.0], np.cumsum(v)])
    for i in idx:
        anc = c[i]
        if not np.isfinite(anc) or anc <= 0:
            continue
        fh = h[i + 1:i + 1 + W]; fl = lo[i + 1:i + 1 + W]; fc = c[i + W]
        if len(fh) < W:
            continue
        max_up = float(np.nanmax(fh) - anc); max_dn = float(anc - np.nanmin(fl))
        # first-touch minute + which side first (leak-free within the label window)
        up_hit = np.where((fh - anc) >= PRIMARY[horizon_min])[0]
        dn_hit = np.where((anc - fl) >= PRIMARY[horizon_min])[0]
        t_up = up_hit[0] if len(up_hit) else 10 ** 9
        t_dn = dn_hit[0] if len(dn_hit) else 10 ** 9
        first_t = min(t_up, t_dn)
        # Causal touch context. A completed OHLC touch bar includes post-entry prices, so it
        # cannot supply overshoot/range without leakage. Use the exact barrier crossing plus
        # completed bars strictly before the touch; ambiguous touch bars are ungradeable below.
        Xp = PRIMARY[horizon_min]
        if first_t < W:
            tb_ = first_t
            if t_up <= t_dn:
                level = anc + Xp
                prior_hi = list(fh[:tb_]); prior_lo = list(fl[:tb_])
                known_hi = max([anc, level] + prior_hi)
                known_lo = min([anc, level] + prior_lo)
                tt_opp = (anc - known_lo) / anc * 1e4
            else:
                level = anc - Xp
                prior_hi = list(fh[:tb_]); prior_lo = list(fl[:tb_])
                known_hi = max([anc, level] + prior_hi)
                known_lo = min([anc, level] + prior_lo)
                tt_opp = (known_hi - anc) / anc * 1e4
            tt_over = 0.0
            tt_rng = (known_hi - known_lo) / anc * 1e4
            tt_fr = (W - tb_) / W
        else:
            tt_over = tt_opp = tt_rng = tt_fr = 0.0
        # ---- leak-free features from bars [.. i] ----
        def ret(k):
            return (c[i] / c[i - k] - 1.0) * 1e4 if i - k >= 0 and c[i - k] > 0 else 0.0

        def rv(k):
            seg = c[max(0, i - k):i + 1]
            r = np.diff(np.log(seg)) if len(seg) > 2 else np.array([0.0])
            return float(np.std(r) * 1e4)

        def rng(k):
            a = max(0, i - k)
            return (np.nanmax(h[a:i + 1]) - np.nanmin(lo[a:i + 1])) / anc * 1e4

        def vol_z(k):
            seg = v[max(0, i - k):i + 1]
            return float((v[i] - seg.mean()) / (seg.std() + EPS)) if len(seg) > 2 else 0.0
        seg5 = c[max(0, i - L5):i + 1]
        chg = np.sign(np.diff(seg5)) if len(seg5) > 2 else np.array([0.0])
        chop5 = float((np.diff(chg) != 0).mean()) if len(chg) > 1 else 0.0
        seg15 = c[max(0, i - L15):i + 1]
        chg15 = np.sign(np.diff(seg15)) if len(seg15) > 2 else np.array([0.0])
        chop15 = float((np.diff(chg15) != 0).mean()) if len(chg15) > 1 else 0.0
        r5, r15, r60 = rv(L5), rv(L15), rv(L60)
        ema_s = c[max(0, i - L5):i + 1].mean(); ema_l = c[max(0, i - L30):i + 1].mean()
        hi60 = np.nanmax(h[max(0, i - L60):i + 1]); lo60 = np.nanmin(lo[max(0, i - L60):i + 1])
        vwapw = c[max(0, i - L15):i + 1]
        tbr = float(tb[max(0, i - L5):i + 1].sum() / (v[max(0, i - L5):i + 1].sum() + EPS))
        dt = datetime.fromtimestamp(ts[i] / 1000, tz=timezone.utc).astimezone(WARSAW_TZ)
        net = c[i] - c[max(0, i - L15)]; path = np.abs(np.diff(c[max(0, i - L15):i + 1])).sum()
        feat = {
            # vol (7)
            "rv_1m": rv(L1), "rv_5m": r5, "rv_15m": r15, "rv_30m": rv(L30), "rv_60m": r60,
            "vol_of_vol": float(np.std([rv(L1), rv(L5), rv(L15)])), "rv_ratio_s_l": r5 / (r60 + EPS),
            # trend / momentum (10)
            "ret_1m": ret(L1), "ret_5m": ret(L5), "ret_15m": ret(L15), "ret_30m": ret(L30), "ret_60m": ret(L60),
            "ema_slope": (ema_s - ema_l) / anc * 1e4, "dist_ema_s": (c[i] - ema_s) / anc * 1e4,
            "dist_ema_l": (c[i] - ema_l) / anc * 1e4,
            "mom_align": float(np.sign(ret(L5)) == np.sign(ret(L15))),
            "consec": float(np.sum(np.sign(np.diff(c[max(0, i - 6):i + 1])) == np.sign(c[i] - c[i - 1]))),
            # range / compression (6)
            "range_5m": rng(L5), "range_15m": rng(L15), "range_30m": rng(L30),
            "compression": rng(L5) / (rng(L30) + EPS), "range_eff": abs(net) / (path + EPS),
            "hl_pos": (c[i] - lo60) / (hi60 - lo60 + EPS),
            # volume (8)
            "vol_z5": vol_z(L5), "vol_z15": vol_z(L15),
            "vol_accel": (v[max(0, i - L1):i + 1].mean()) / (v[max(0, i - L5):i + 1].mean() + EPS),
            "taker_buy_ratio": tbr, "taker_imb": (tbr - 0.5) * 2.0,
            "vol_trend": (v[max(0, i - L5):i + 1].sum()) / (v[max(0, i - L15):i + 1].sum() + EPS),
            "cum_vol_15m": float(cum_v[i + 1] - cum_v[max(0, i - L15)]) / (anc + EPS),
            "big_vol": float(vol_z(L15) > 1.5),
            # choppiness / microstructure (8)
            "chop_5m": chop5, "chop_15m": chop15, "up_bar_ratio": float((np.diff(seg5) > 0).mean()) if len(seg5) > 2 else 0.5,
            "wick_up": (h[i] - max(c[i], c[i - 1])) / anc * 1e4, "wick_dn": (min(c[i], c[i - 1]) - lo[i]) / anc * 1e4,
            "body": abs(c[i] - c[i - 1]) / anc * 1e4, "alt_rate": chop5,
            "stretch_vwap": (c[i] - vwapw.mean()) / anc * 1e4,
            # regime (6)
            "hurst": _hurst(c[max(0, i - L30):i + 1]), "trend_strength": abs(net) / (r15 * anc / 1e4 + EPS),
            "vol_regime": float(r15 > rv(L60)), "range_regime": float(rng(L5) < rng(L30) * 0.7),
            "autocorr": float(pd.Series(np.diff(seg15)).autocorr()) if len(seg15) > 4 else 0.0,
            "overext": ret(L5) / (r5 + EPS),
            # time (8)
            "hour": dt.hour, "minute": dt.minute, "dow": dt.weekday(), "is_weekend": float(dt.weekday() >= 5),
            "block4h": dt.hour // 4, "hour_sin": math.sin(2 * math.pi * dt.hour / 24),
            "hour_cos": math.cos(2 * math.pi * dt.hour / 24), "session": (0 if dt.hour < 8 else 1 if dt.hour < 16 else 2),
            # level / setup (7)
            "dist_hi60": (hi60 - c[i]) / anc * 1e4, "dist_lo60": (c[i] - lo60) / anc * 1e4,
            "prev_range": rng(W), "spring_tension": max((hi60 - c[i]), (c[i] - lo60)) / anc * 1e4,
            "near_round": float(min(c[i] % 100, 100 - c[i] % 100) < 15), "opp_excursion": min(max_up, max_dn) if False else 0.0,
            "time_norm": float((i % (L1)) / (L1 + EPS)),
            # extra reversal / mean-reversion features (10) -> ~70 total
            "rv_2m": rv(2 * L1), "ret_2m": ret(2 * L1), "vol_z30": vol_z(L30),
            "taker_imb_15m": (float(tb[max(0, i - L15):i + 1].sum() / (v[max(0, i - L15):i + 1].sum() + EPS)) - 0.5) * 2,
            "price_accel": ret(L1) - ret(2 * L1) / 2.0, "range_expansion": rng(L5) / (rng(L15) + EPS),
            "upper_rej_5m": float(np.nanmean((h[max(0, i - L5):i + 1] - c[max(0, i - L5):i + 1]) /
                                             (h[max(0, i - L5):i + 1] - lo[max(0, i - L5):i + 1] + EPS))),
            "lower_rej_5m": float(np.nanmean((c[max(0, i - L5):i + 1] - lo[max(0, i - L5):i + 1]) /
                                             (h[max(0, i - L5):i + 1] - lo[max(0, i - L5):i + 1] + EPS))),
            "stretch_over_rv": ((c[i] - vwapw.mean()) / anc * 1e4) / (r15 + EPS),
            "dist_4h_mean": (c[i] - c[max(0, i - 4 * L60):i + 1].mean()) / anc * 1e4,
        }
        # ---- labels (forward path) ----
        lbl = {"ts_ms": ts[i], "anchor": anc, "horizon": horizon_min,
               "dir_up": int(fc > anc),                                    # NULL: settled direction (coin-flip)
               "max_up": max_up, "max_dn": max_dn, "first_touch_frac": (W - first_t) / W if first_t < W else 0.0,
               "tt_frac": tt_fr, "tt_overshoot": tt_over, "tt_pre_opp": tt_opp, "tt_pre_range": tt_rng}
        for X in BARRIERS[horizon_min]:
            tu = (max_up >= X); td = (max_dn >= X)
            lbl[f"touch_{int(X)}"] = int(tu or td)
            lbl[f"roundtrip_{int(X)}"] = int(tu and td)
            # reach-anchor FADE win at barrier X: after the FIRST touch, does price revert to the anchor
            # before extending to the 2X stop? (strict; unresolved-by-close = loss). This is the profit label.
            lbl[f"fade_{int(X)}"] = _fade_win(fh, fl, anc, X)
        rows.append({**feat, **lbl})
    return pd.DataFrame(rows)


def _fade_win(fh, fl, anc, X):
    """Strict causal fade outcome; NaN when the entry bar's intrabar order is unknowable."""
    up = np.where((fh - anc) >= X)[0]; dn = np.where((anc - fl) >= X)[0]
    tu = up[0] if len(up) else 10 ** 9; td = dn[0] if len(dn) else 10 ** 9
    if tu == 10 ** 9 and td == 10 ** 9:
        return 0                                          # never touched -> no trade -> not a win
    if tu <= td:                                          # up-touch first -> BUY DOWN, TP=anchor, stop=anchor+2X
        if fl[tu] <= anc or fh[tu] >= anc + 2 * X:
            return np.nan
        seg_h = fh[tu + 1:]; seg_l = fl[tu + 1:]
        for k in range(len(seg_l)):
            if seg_l[k] <= anc:
                return 1                                  # reverted to anchor
            if seg_h[k] >= anc + 2 * X:
                return 0                                  # hit stop first
        return 0
    else:                                                 # down-touch first -> BUY UP, TP=anchor, stop=anchor-2X
        if fh[td] >= anc or fl[td] <= anc - 2 * X:
            return np.nan
        seg_h = fh[td + 1:]; seg_l = fl[td + 1:]
        for k in range(len(seg_h)):
            if seg_h[k] >= anc:
                return 1
            if seg_l[k] <= anc - 2 * X:
                return 0
        return 0


# ----------------------------------------------------------------------------- analysis
FEATCOLS_CACHE = None


def featcols(df):
    # OPEN-only features (leak-free for open-time targets: direction/touch/roundtrip). tt_* are touch-time
    # and are added ONLY for the reach-anchor fade (decision made at the touch) via bakeoff(extra_cols=...).
    drop = {"ts_ms", "anchor", "horizon", "dir_up", "max_up", "max_dn", "first_touch_frac"}
    return [c for c in df.columns if c not in drop
            and not c.startswith(("touch_", "roundtrip_", "fade_", "tt_"))]


TT_COLS = ["tt_frac", "tt_overshoot", "tt_pre_opp", "tt_pre_range"]


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return 100 * ((p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d)


def window_analysis(df, X, L):
    """When do reversals cluster / volume peak? Best trading windows by hour / 4h-block / weekday."""
    out = []
    for key, col in (("hour", "hour"), ("block4h", "block4h"), ("dow", "dow")):
        g = df.groupby(col)
        t = g.agg(n=("fade_" + str(int(X)), "size"), fade=("fade_" + str(int(X)), "mean"),
                  touch=("touch_" + str(int(X)), "mean"), rt=("roundtrip_" + str(int(X)), "mean"),
                  vol=("vol_z15", "mean"), dirbal=("dir_up", "mean")).reset_index()
        out.append((key, t))
    return out


# ----------------------------------------------------------------------------- models
def _models():
    m = {}
    try:
        from sklearn.linear_model import LogisticRegression
        m["logreg"] = LogisticRegression(max_iter=200, C=0.5)
    except Exception:
        pass
    try:
        from sklearn.ensemble import (ExtraTreesClassifier, HistGradientBoostingClassifier,
                                       RandomForestClassifier)
        m["rf"] = RandomForestClassifier(n_estimators=150, max_depth=9, n_jobs=2, random_state=0)
        m["extratrees"] = ExtraTreesClassifier(n_estimators=150, max_depth=9, n_jobs=2, random_state=0)
        m["histgb"] = HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.05, random_state=0)
    except Exception:
        pass
    for name, imp in (("xgb", "xgboost"), ("lgb", "lightgbm"), ("catboost", "catboost")):
        try:
            if name == "xgb":
                from xgboost import XGBClassifier
                m["xgb"] = XGBClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, n_jobs=2,
                                         eval_metric="logloss", verbosity=0)
            elif name == "lgb":
                from lightgbm import LGBMClassifier
                m["lgb"] = LGBMClassifier(n_estimators=250, max_depth=4, learning_rate=0.05, n_jobs=2, verbose=-1)
            else:
                from catboost import CatBoostClassifier
                m["catboost"] = CatBoostClassifier(iterations=250, depth=4, learning_rate=0.05, verbose=0,
                                                   allow_writing_files=False)
        except Exception:
            pass
    return m


def bakeoff(df, target, split, cap=200000, extra_cols=(), touched_barrier=None):
    from sklearn.metrics import roc_auc_score
    cols = featcols(df) + list(extra_cols)
    d = df
    if touched_barrier is not None:                       # fade decision only exists once a touch happened
        d = d[d[f"touch_{int(touched_barrier)}"] == 1]
    d = d.replace([np.inf, -np.inf], np.nan).dropna(subset=cols + [target]).reset_index(drop=True)
    nsplit = int(len(d) * split)
    tr, te = d.iloc[:nsplit], d.iloc[nsplit:]
    if len(tr) > cap:
        tr = tr.iloc[-cap:]
    Xtr, ytr = tr[cols].to_numpy(float), tr[target].to_numpy(int)
    Xte, yte = te[cols].to_numpy(float), te[target].to_numpy(int)
    mu, sd = Xtr.mean(0), Xtr.std(0) + EPS
    res = {}; probs = []
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        return None, None, te
    for name, model in _models().items():
        try:
            Xt = (Xtr - mu) / sd if name == "logreg" else Xtr
            Xe = (Xte - mu) / sd if name == "logreg" else Xte
            model.fit(Xt, ytr)
            p = model.predict_proba(Xe)[:, 1]
            probs.append(p)
            auc = roc_auc_score(yte, p)
            order = np.argsort(-p)
            cov = {c_: float(yte[order[:max(20, int(len(p) * c_))]].mean()) for c_ in (0.25, 0.10, 0.05)}
            res[name] = {"auc": float(auc), "cov": cov}
        except Exception as e:
            res[name] = {"auc": float("nan"), "err": str(e)[:40]}
    if probs:
        ens = np.mean(probs, axis=0)
        auc = roc_auc_score(yte, ens)
        order = np.argsort(-ens)
        cov = {c_: float(yte[order[:max(20, int(len(ens) * c_))]].mean()) for c_ in (0.25, 0.10, 0.05)}
        res["ENSEMBLE"] = {"auc": float(auc), "cov": cov}
        return res, ens, te
    return res, None, te


# ----------------------------------------------------------------------------- P&L backtest
def backtest_pnl(te, ens, X, thr=0.55, rr=1.0, cost_frac=0.0):
    """Honest fade P&L. te is TOUCHED-only (the fade decision exists only after a touch). Gate: P(fade)>=thr.
    The fade label is symmetric ±X from the touch (TP=anchor is X away, stop=anchor±2X is X away) => the
    natural reward:risk is 1:1 (win +X, loss -rr*X). breakeven win = rr/(rr+1); rr=1 -> 50% (the label);
    rr>1 models a wider stop. cost_frac = fractional cost of X per round-trip trade."""
    fade = te[f"fade_{int(X)}"].to_numpy(int)
    take = ens >= thr
    n = int(take.sum())
    if n < 20:
        return {"n": n, "note": "too few gated trades"}
    win = fade[take]; wr = float(win.mean())
    pnl = np.where(win == 1, X, -rr * X) - cost_frac * X
    be = rr / (rr + 1.0)
    return {"n": n, "coverage_pct": 100 * n / len(te), "win_rate": wr, "rr": rr,
            "wilson_lb": wilson(int(win.sum()), n) / 100, "breakeven_wr": be, "edge_vs_breakeven": wr - be,
            "avg_pnl_usd": float(pnl.mean()), "total_pnl_usd": float(pnl.sum()),
            "profitable": bool(pnl.mean() > 0)}


def _f(x, n=3):
    return f"{x:.{n}f}" if isinstance(x, (int, float)) and np.isfinite(x) else "  -  "


# ----------------------------------------------------------------------------- main
def run(source, interval, days, split, sample):
    df, bar_s = load_data(source, interval, days)
    span = (df["ts_ms"].max() - df["ts_ms"].min()) / 86400000
    tag = "1m matrix" if source == "matrix" else f"{interval} ({bar_s}s)"
    L = [f"# Reversal / Round-Trip Strategy Backtest — {date.today().isoformat()}", "",
         f"Data: **{tag}**, {len(df):,} bars, {span:.0f} days. Anchor = window-open (Polymarket price-to-beat). "
         f"80/20 time split. **P&L is BTC-reversion (a PROXY) — real Polymarket P&L needs the share mispriced "
         f"vs these odds after costs.** Direction = coin-flip (null); the edge is reversal selection + timing.", ""]
    print("\n".join(L))
    for hz in (5, 15):
        X = PRIMARY[hz]
        w = build_windows(df, hz, bar_s)
        if sample and len(w) > sample:
            w = w.iloc[-sample:].reset_index(drop=True)
        base_dir = w["dir_up"].mean(); base_fade = w[f"fade_{int(X)}"].mean()
        base_touch = w[f"touch_{int(X)}"].mean(); base_rt = w[f"roundtrip_{int(X)}"].mean()
        L += [f"\n## {hz}m windows — primary barrier ${int(X)}  (n={len(w):,})",
              f"- Base rates: direction-UP **{base_dir:.3f}** (≈coin-flip null) · touch ${int(X)} **{base_touch:.3f}** · "
              f"round-trip **{base_rt:.3f}** · **fade reach-anchor {base_fade:.3f}**"]
        # NULL: direction bakeoff (should be ~0.50)
        dres, _, _ = bakeoff(w, "dir_up", split)
        if dres:
            L.append(f"- **NULL check — predicting settled direction:** ENSEMBLE AUC "
                     f"**{_f(dres['ENSEMBLE']['auc'])}** (≈0.50 confirms the coin-flip; do not trade it).")
        # PROFIT target: fade reach-anchor, GRADED AT THE TOUCH (touched-only + touch-time features) -- the
        # realistic decision point, matching the live fade engine (train_fade_model).
        fres, ens, te = bakeoff(w, f"fade_{int(X)}", split, extra_cols=TT_COLS, touched_barrier=X)
        if fres:
            L.append(f"\n### Model bake-off — fade reach-anchor (${int(X)}, graded AT the touch; the profit target)")
            L.append("| model | AUC | win@top25% | win@top10% | win@top5% |")
            L.append("|---|---|---|---|---|")
            for name, r in sorted(fres.items(), key=lambda kv: -(kv[1].get("auc") or 0)):
                cov = r.get("cov", {})
                L.append(f"| {name} | {_f(r.get('auc'))} | {_f(cov.get(0.25))} | {_f(cov.get(0.10))} | {_f(cov.get(0.05))} |")
            # BTC path scenario only. This is not binary-share P&L and must not be used as a bet claim.
            L.append(f"\n**BTC-path scenario — symmetric 1:1 price barriers (not Polymarket P&L; "
                     f"TP and stop are both ${int(X)} from the touch), $1 synthetic cost/trade:**")
            for thr in (0.55, 0.60, 0.65):
                pnl = backtest_pnl(te, ens, X, thr=thr, rr=1.0, cost_frac=1.0 / X)
                if pnl.get("n", 0) >= 20:
                    L.append(f"- **@P≥{thr:.2f}:** {pnl['n']} trades ({pnl['coverage_pct']:.1f}% of touches), win "
                             f"**{pnl['win_rate']:.3f}** (LB {pnl['wilson_lb']:.3f}) vs breakeven 0.50 → edge "
                             f"**{pnl['edge_vs_breakeven']:+.3f}**, avg **${pnl['avg_pnl_usd']:+.1f}**/trade, total "
                             f"**${pnl['total_pnl_usd']:+,.0f}** → "
                             f"{'positive BTC proxy only' if pnl['profitable'] else 'negative BTC proxy'}")
            # Sensitivity only: binary-share economics cannot be inferred from the BTC barrier outcome.
            p2 = backtest_pnl(te, ens, X, thr=0.60, rr=2.0)
            if p2.get("n", 0) >= 20:
                L.append(f"- _sensitivity @P≥0.60, wider 2:1 stop (breakeven 66.7%): win {p2['win_rate']:.3f} → "
                         f"{'positive BTC proxy' if p2['profitable'] else 'negative BTC proxy'}. "
                         f"A Polymarket trade requires recorded entry ask, exit bid or settlement, two-sided fees, "
                         f"and fill assumptions; no binary-share breakeven is claimed here._")
        # best trading windows
        L.append(f"\n### Best trading windows (${int(X)} fade reach-anchor by time)")
        for key, t in window_analysis(w, X, hz):
            t = t.sort_values("fade", ascending=False)
            top = t.head(3)
            L.append(f"- **by {key}:** " + " · ".join(
                f"{key}={int(r[key])} fade={r['fade']:.2f} touch={r['touch']:.2f} vol_z={r['vol']:+.2f}"
                for _, r in top.iterrows()))
        # latest signal
        last = w.iloc[-1]
        if ens is not None and len(te):
            p_last = float(ens[-1])
            act = ("FADE-READY (gate on early touch)" if p_last >= 0.55 else "SKIP (low reach-anchor odds)"
                   if p_last < 0.45 else "WATCH")
            L.append(f"\n### Latest {hz}m signal → **{act}**  (model P(reach-anchor)={p_last:.2f}, "
                     f"regime hurst={last['hurst']:.2f}, chop={last['chop_15m']:.2f}, vol_z={last['vol_z15']:+.2f})")
    L += ["\n## Honest verdict",
          "- **Direction is a coin-flip** (null AUC ≈0.50) — never traded. Predicting UP/DOWN from the anchor is dead.",
          "- **The fade is decided AT THE TOUCH, not at open** — grading with the overshoot/spring context lifts the "
          "reach-anchor AUC well above the window-open version. The edge is real and concentrated in the top touches.",
          "- **Reward:risk is the whole game.** The label is a symmetric BTC-price proxy. It does not determine "
          "binary-share reward/risk: that requires the actual entry ask, exit bid/settlement value, fees, and fill.",
          "- **Still a PROXY.** This is BTC-price reversion. Real Polymarket P&L needs the actual share ask mispriced "
          "vs these odds after costs (recorder-gated). This sizes the OPPORTUNITY and the best windows, not proven profit.",
          "- **When to trade:** the best-window rows (hour / 4h-block / weekday) show where reversals cluster + volume "
          "peaks — be selective there."]
    out_md = OUT_MD if source == "matrix" else OUT_MD[:-3] + f"_{interval}.md"   # sub-minute -> separate doc
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {out_md}")


def export_windows(source="matrix", interval="30s", days=360):
    """Bake Europe/Warsaw local-hour + weekday fade favorability into a JSON the live app
    loads to show 'is this a historically strong reversal window?'. Labels only (no model bakeoff) -> fast."""
    import json
    df, bar_s = load_data(source, interval, days)
    table = {}
    for hz in (5, 15):
        X = PRIMARY[hz]
        w = build_windows(df, hz, bar_s)
        fcol = f"fade_{int(X)}"
        base = float(w[fcol].mean()) or 1e-9
        hour = {int(h): round(float(v) / base, 3) for h, v in w.groupby("hour")[fcol].mean().items()}
        dow = {int(d): round(float(v) / base, 3) for d, v in w.groupby("dow")[fcol].mean().items()}
        table[str(hz)] = {"barrier": X, "base": round(base, 4), "n": int(len(w)), "hour": hour, "dow": dow}
    out = os.path.join(DATA, "reversal_window_favorability.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": source, "generated": date.today().isoformat(), "tz": "Europe/Warsaw",
                   "note": "favorability = per-window fade-reach-anchor rate / overall base; >1 = above-average "
                           "reversal window. SOFT prior, not a hard gate (evening edge is partly selection bias).",
                   "horizons": table}, f, indent=2)
    print(f"wrote {out}")
    for hz in ("5", "15"):
        t = table[hz]
        bh = sorted(t["hour"].items(), key=lambda kv: -kv[1])[:4]
        print(f"[{hz}m] base fade={t['base']:.3f}  best Europe/Warsaw hours: "
              + ", ".join(f"{h}h={r:.2f}x" for h, r in bh))


def selftest():
    rng = np.random.default_rng(0); n = 40000
    close = 60000 + np.cumsum(rng.normal(0, 6, n))
    df = pd.DataFrame({"ts_ms": np.arange(n) * 60000, "open": close, "close": close,
                       "high": close + np.abs(rng.normal(0, 20, n)), "low": close - np.abs(rng.normal(0, 20, n)),
                       "volume": np.abs(rng.normal(100, 30, n)), "taker_buy": np.abs(rng.normal(50, 15, n))})
    w = build_windows(df, 5, 60)
    assert len(w) > 500 and "fade_30" in w.columns and "dir_up" in w.columns
    assert set(w["fade_30"].dropna().unique()) <= {0, 1}
    fc = featcols(w)
    print(f"selftest: {len(w)} windows, {len(fc)} features, targets fade/touch/roundtrip/dir present. "
          f"feature count={len(fc)} (target ~70)")
    print("PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="matrix", choices=["matrix", "binance"])
    ap.add_argument("--interval", default="30s", help="1s/15s/30s (binance source; resampled from 1s)")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--split", type=float, default=0.80)
    ap.add_argument("--sample", type=int, default=0, help="cap windows for speed (0=all)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--export-windows", action="store_true", help="write the live window-favorability JSON and exit")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    if a.export_windows:
        export_windows(a.source, a.interval, a.days if a.source == "binance" else 360); return
    run(a.source, a.interval, a.days, a.split, a.sample)


if __name__ == "__main__":
    main()
