"""
edge_probe.py — unified leak-free EDGE-PROBE engine (offline, no-train).
=========================================================================
One harness to test "does feature X have edge?" for ANY hypothesis, the same disciplined way as
depth_edge_probe / entropy_edge_probe: load cached data → build a feature from PAST data only →
align to a strictly-forward label → temporal split → report DIRECTION AUC and TIMING (big-move) AUC
+ low/high lift → verdict. Adding a hypothesis = adding one builder to FEATURE_BUILDERS.

Why two targets per feature:
  • DIRECTION AUC — expected ~0.50 for most (confirms the 5m information ceiling); >0.55 = a real
    (and surprising → audit for leakage) directional edge.
  • TIMING AUC (above-median |move|) + lift — selectivity is the live thesis; a feature that TIMES
    big moves (even with no direction edge) is valuable as a P(big_move) gate.

Leak-free: a minute-m feature uses only aggregates through the CLOSE of minute m; labels are the
forward move close[m]→close[m+h]. Builders use rolling PAST windows only.

Data: cached Binance SPOT aggTrades (reused from the backfill cache).
Usage:  python backend/edge_probe.py --feature all --days 7
        python backend/edge_probe.py --feature cvd --days 7
        python backend/edge_probe.py --selftest
"""
import argparse
import os
import sys

import numpy as np

HORIZONS = (3, 5, 10, 15)


# ───────────────────────── per-minute bar aggregation (from ticks) ─────────────────────
def per_minute_bars(ts_ms, price, qty, is_buyer_maker):
    """PURE: ticks -> per-minute DataFrame-like dict of arrays. is_buyer_maker=True => taker SOLD
    (aggressive sell); False => taker BOUGHT. Large = qty >= 95th pct (whale prints)."""
    import pandas as pd
    m = (ts_ms // 60000).astype(np.int64)
    taker_buy_q = np.where(~is_buyer_maker, qty, 0.0)
    taker_sell_q = np.where(is_buyer_maker, qty, 0.0)
    large_thr = np.quantile(qty, 0.95) if len(qty) > 20 else qty.max() + 1
    large = qty >= large_thr
    large_signed = np.where(large, np.where(~is_buyer_maker, qty, -qty), 0.0)
    df = pd.DataFrame({"m": m, "price": price, "qty": qty, "tb": taker_buy_q,
                       "ts_": taker_sell_q, "ls": large_signed})
    g = df.groupby("m")
    out = {
        "minute": g["price"].last().index.values.astype(np.int64),
        "close": g["price"].last().values,
        "high": g["price"].max().values,
        "low": g["price"].min().values,
        "vol": g["qty"].sum().values,
        "count": g["qty"].count().values.astype(float),
        "taker_buy": g["tb"].sum().values,
        "taker_sell": g["ts_"].sum().values,
        "large_signed": g["ls"].sum().values,
    }
    return out


def _roll_sum(x, w):
    out = np.zeros(len(x))
    c = np.cumsum(x)
    out[:w] = c[:w]
    out[w:] = c[w:] - c[:-w]
    return out


def _roll_std(x, w):
    out = np.zeros(len(x))
    for i in range(len(x)):
        lo = max(0, i - w + 1)
        out[i] = np.std(x[lo:i + 1]) if i - lo >= 2 else 0.0
    return out


def _roll_mean(x, w):
    cnt = np.minimum(np.arange(1, len(x) + 1), w)
    return _roll_sum(x, w) / cnt


def _roll_var(x, w):
    m = _roll_mean(x, w)
    return np.maximum(_roll_mean(x * x, w) - m * m, 0.0)


# ───────────────────────── feature builders (the hypothesis registry) ──────────────────
def _f_cvd(b):
    """Cumulative volume delta (aggressive buy − sell) — the research dump's #1 claimed edge."""
    cvd = b["taker_buy"] - b["taker_sell"]
    safe_v = np.where(b["vol"] > 1, b["vol"], 1.0)
    norm = cvd / safe_v                                    # per-minute normalized delta
    X = np.column_stack([norm, _roll_sum(cvd, 5) / _roll_sum(b["vol"], 5).clip(1),
                         _roll_sum(cvd, 15) / _roll_sum(b["vol"], 15).clip(1)])
    return X, ["cvd_norm_1m", "cvd_ratio_5m", "cvd_ratio_15m"]


def _f_taker_ratio(b):
    """Aggressive-buy ratio (taker_buy / total) — flow imbalance, cleaner than raw CVD."""
    tot = (b["taker_buy"] + b["taker_sell"]).clip(1)
    r = b["taker_buy"] / tot
    X = np.column_stack([r, _roll_sum(b["taker_buy"], 5) / _roll_sum(tot, 5).clip(1),
                         _roll_sum(b["taker_buy"], 15) / _roll_sum(tot, 15).clip(1)])
    return X, ["taker_ratio_1m", "taker_ratio_5m", "taker_ratio_15m"]


def _f_large_trade(b):
    """Large-print (whale) signed flow — does aggressive whale direction predict price?"""
    safe_v = np.where(b["vol"] > 1, b["vol"], 1.0)
    X = np.column_stack([b["large_signed"] / safe_v, _roll_sum(b["large_signed"], 5) / _roll_sum(b["vol"], 5).clip(1),
                         _roll_sum(b["large_signed"], 15) / _roll_sum(b["vol"], 15).clip(1)])
    return X, ["large_signed_1m", "large_signed_5m", "large_signed_15m"]


def _f_realized_vol(b):
    """Rolling realized volatility — the TIMING BASELINE (vol clustering). The entropy head must
    BEAT this to justify itself; if realized vol times moves as well, entropy adds nothing."""
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret = np.zeros(len(logc)); ret[1:] = np.diff(logc)
    X = np.column_stack([_roll_std(ret, 15), _roll_std(ret, 30), _roll_std(ret, 60),
                         _roll_std(ret, 15) - _roll_std(ret, 60)])
    return X, ["rv_15m", "rv_30m", "rv_60m", "rv_term"]


def _f_session(b):
    """UTC time-of-day / session / weekend — does WHEN matter for direction or move size?"""
    hour = ((b["minute"] // 60) % 24).astype(float)
    dow = ((b["minute"] // 1440 + 4) % 7)                  # epoch min 0 = Thursday → +4
    X = np.column_stack([np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
                         ((hour >= 0) & (hour < 8)).astype(float),    # Asia
                         ((hour >= 13) & (hour < 21)).astype(float),  # US
                         (dow >= 5).astype(float)])                   # weekend
    return X, ["hour_sin", "hour_cos", "asia", "us", "weekend"]


def _f_intensity(b):
    """Trade-count intensity + volume acceleration — another TIMING candidate."""
    safe_c = np.where(b["count"] > 0, b["count"], 1.0)
    vacc = np.zeros(len(b["vol"])); vacc[1:] = np.diff(b["vol"])
    X = np.column_stack([np.log1p(b["count"]), np.log1p(b["vol"]),
                         _roll_sum(b["count"], 5) / safe_c.clip(1), vacc / np.where(b["vol"] > 1, b["vol"], 1.0)])
    return X, ["log_count", "log_vol", "count_accel_5m", "vol_accel"]


def _f_vpin(b):
    """VPIN-style order-flow toxicity: rolling mean of |buy−sell|/total. Direction-INVARIANT (|·|),
    so a pure TIMING candidate — high toxicity → informed trading → bigger moves (the entropy rival)."""
    tot = (b["taker_buy"] + b["taker_sell"]).clip(1)
    imb = np.abs(b["taker_buy"] - b["taker_sell"]) / tot
    X = np.column_stack([_roll_mean(imb, 15), _roll_mean(imb, 30), _roll_mean(imb, 50)])
    return X, ["vpin_15m", "vpin_30m", "vpin_50m"]


def _f_variance_ratio(b):
    """Lo-MacKinlay variance ratio VR(q)=Var(q-ret)/(q·Var(1-ret)) over a 60m window: VR>1 trending,
    VR<1 mean-reverting. Plus normalized recent drift — does the regime+drift predict direction?"""
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret1 = np.zeros(len(logc)); ret1[1:] = np.diff(logc)
    W = 60
    var1 = _roll_var(ret1, W)

    def vr(q):
        rq = np.zeros(len(logc)); rq[q:] = logc[q:] - logc[:-q]
        return _roll_var(rq, W) / (q * var1 + 1e-12)
    recent = _roll_mean(ret1, 15) / (np.sqrt(var1) + 1e-9)
    X = np.column_stack([vr(2), vr(5), vr(10), recent])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["vr_2", "vr_5", "vr_10", "recent_drift"]


def _f_autocorr(b):
    """Short-horizon return autocorrelation (rolling corr of ret[t], ret[t−1]): >0 momentum, <0
    mean-reversion. Combined with recent return (momentum-confirmed) — a direction hypothesis."""
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret = np.zeros(len(logc)); ret[1:] = np.diff(logc)
    lag = np.zeros(len(ret)); lag[1:] = ret[:-1]

    def ac(w):
        m, ml = _roll_mean(ret, w), _roll_mean(lag, w)
        cov = _roll_mean(ret * lag, w) - m * ml
        return cov / (np.sqrt(_roll_var(ret, w) * _roll_var(lag, w)) + 1e-12)
    ac60, ac120 = ac(60), ac(120)
    recent = _roll_mean(ret, 5) / (np.sqrt(_roll_var(ret, 30)) + 1e-9)
    X = np.column_stack([ac60, ac120, recent, ac60 * recent])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["ac1_60m", "ac1_120m", "recent_ret", "momo_confirmed"]


def _f_xvenue_divergence(b):
    """Spot-vs-perp cross-venue divergence. Requires crossvenue_flow.parquet built by A4 script.
    Perp leading spot / funding tension -> mean-reversion or directional signal."""
    import pandas as pd
    import os
    path = os.path.join(os.path.dirname(__file__), "../data/crossvenue_flow.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path} - run build_crossvenue_flow.py first")
    df = pd.read_parquet(path)
    df["minute"] = df["ts_ms"] // 60000
    df = df.set_index("minute")
    
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        aligned = df.reindex(b["minute"]).ffill().fillna(0.0)
    
    cvd_div = aligned["cvd_divergence"].values
    basis = aligned["perp_spot_basis_bps"].values
    
    X = np.column_stack([cvd_div, basis, _roll_mean(cvd_div, 15), _roll_mean(basis, 15)])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["cvd_div", "basis", "cvd_div_15m", "basis_15m"]


def _f_price_impact(b):
    """Amihud proxy / Kyle's Lambda proxy: |return| / volume. High impact = thin liquidity
    -> precursor to volatility expansion."""
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret = np.zeros(len(logc)); ret[1:] = np.diff(logc)
    abs_ret = np.abs(ret)
    vol = np.where(b["vol"] > 1, b["vol"], 1.0)
    
    impact_5m = _roll_sum(abs_ret, 5) / _roll_sum(vol, 5).clip(1)
    impact_15m = _roll_sum(abs_ret, 15) / _roll_sum(vol, 15).clip(1)
    impact_30m = _roll_sum(abs_ret, 30) / _roll_sum(vol, 30).clip(1)
    
    X = np.column_stack([impact_5m, impact_15m, impact_30m])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["impact_5m", "impact_15m", "impact_30m"]


def _f_range_compression(b):
    """High-Low range compression (Bollinger Band Width proxy). Coiled spring effect.
    15m MA of (High-Low) / 60m MA of (High-Low). Values < 1 indicate compression."""
    rng = b["high"] - b["low"]
    rng_15m = _roll_mean(rng, 15)
    rng_60m = _roll_mean(rng, 60)
    compression = rng_15m / (rng_60m + 1e-9)
    X = np.column_stack([compression, rng_15m])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["compression_ratio", "range_15m"]


def _f_trade_size_skew(b):
    """Average trade size (vol / count). Spikes indicate institutional size."""
    count = np.where(b["count"] > 0, b["count"], 1.0)
    avg_size = b["vol"] / count
    avg_15m = _roll_mean(avg_size, 15)
    avg_60m = _roll_mean(avg_size, 60)
    skew = avg_15m / (avg_60m + 1e-9)
    X = np.column_stack([avg_15m, skew])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["avg_trade_size", "trade_size_skew"]


def _f_ofi_chop(b):
    """Order Flow Imbalance (OFI) flips. Count of sign changes in CVD over a window.
    High flips = chop. Persistent sign = trend/momentum."""
    cvd = b["taker_buy"] - b["taker_sell"]
    sign = np.sign(cvd)
    flips = np.zeros(len(sign))
    flips[1:] = (sign[1:] != sign[:-1]).astype(float)
    
    flips_15m = _roll_sum(flips, 15)
    flips_30m = _roll_sum(flips, 30)
    
    X = np.column_stack([flips_15m, flips_30m])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["ofi_flips_15m", "ofi_flips_30m"]


def _f_absorption(b):
    """Price-Flow Divergence. When CVD goes one way but price goes the other,
    indicating limit orders absorbing aggressive flow."""
    cvd = b["taker_buy"] - b["taker_sell"]
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret = np.zeros(len(logc)); ret[1:] = np.diff(logc)
    
    cvd_15m = _roll_sum(cvd, 15)
    ret_15m = _roll_sum(ret, 15)
    
    # Absorption flag: Signs diverge strongly.
    divergence = np.where(np.sign(cvd_15m) != np.sign(ret_15m), np.abs(cvd_15m), 0.0)
    norm_divergence = divergence / _roll_sum(b["vol"], 15).clip(1)
    
    X = np.column_stack([norm_divergence, np.sign(cvd_15m) * norm_divergence])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["absorption_mag", "absorption_dir"]


def _f_liquidity_shock(b):
    """Intra-bar tick variance proxy. Since we don't have per-tick variance here,
    we use the 1-minute (high - low) / close as a micro-variance proxy and look
    for extreme localized spikes relative to recent bars."""
    micro_range = (b["high"] - b["low"]) / np.where(b["close"] > 0, b["close"], 1.0)
    avg_micro_15m = _roll_mean(micro_range, 15)
    avg_micro_60m = _roll_mean(micro_range, 60)
    
    shock = micro_range / (avg_micro_60m + 1e-9)
    X = np.column_stack([shock, avg_micro_15m])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["shock_magnitude", "micro_range_15m"]


def _f_trend_consistency(b):
    """Micro-trend consistency. Ratio of positive returns to total absolute returns
    within a window."""
    logc = np.log(np.where(b["close"] > 0, b["close"], 1.0))
    ret = np.zeros(len(logc)); ret[1:] = np.diff(logc)
    pos_ret = np.where(ret > 0, ret, 0.0)
    abs_ret = np.abs(ret)
    
    sum_pos_15m = _roll_sum(pos_ret, 15)
    sum_abs_15m = _roll_sum(abs_ret, 15)
    
    consistency = sum_pos_15m / (sum_abs_15m + 1e-9)
    X = np.column_stack([consistency, np.abs(consistency - 0.5)])
    return np.nan_to_num(X, posinf=0.0, neginf=0.0), ["consistency_ratio", "directional_strength"]


FEATURE_BUILDERS = {
    "cvd": _f_cvd, "taker_ratio": _f_taker_ratio, "large_trade": _f_large_trade,
    "realized_vol": _f_realized_vol, "session": _f_session, "intensity": _f_intensity,
    "vpin": _f_vpin, "variance_ratio": _f_variance_ratio, "autocorr": _f_autocorr,
    "xvenue_divergence": _f_xvenue_divergence,
    "price_impact": _f_price_impact, "range_compression": _f_range_compression,
    "trade_size_skew": _f_trade_size_skew, "ofi_chop": _f_ofi_chop,
    "absorption": _f_absorption, "liquidity_shock": _f_liquidity_shock, "trend_consistency": _f_trend_consistency,
}
DIRECTION_HYPOTHESES = {"cvd", "taker_ratio", "large_trade", "variance_ratio", "autocorr", "xvenue_divergence", "absorption", "trend_consistency"}
TIMING_HYPOTHESES = {"realized_vol", "intensity", "vpin", "price_impact", "range_compression", "trade_size_skew", "ofi_chop", "liquidity_shock", "absorption"}


# ───────────────────────── labels + evaluation (leak-free) ─────────────────────────────
def make_labels(close, h):
    """Forward direction (sign) + abs move for window close[m]→close[m+h]. NaN tail."""
    n = len(close)
    up = np.full(n, -1); absm = np.full(n, np.nan)
    end = n - h
    if end > 0:
        fwd = close[h:h + end]
        ref = close[:end]
        up[:end] = (fwd >= ref).astype(int)
        absm[:end] = np.abs(fwd - ref)
    return up, absm


def evaluate(X, up, absm):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    mask = (up >= 0) & np.isfinite(absm) & np.all(np.isfinite(X), axis=1)
    X, up, absm = X[mask], up[mask], absm[mask]
    n = len(up)
    if n < 400:
        return None
    cut = int(n * 0.70)
    def _auc(y):
        if len(np.unique(y[:cut])) < 2 or len(np.unique(y[cut:])) < 2:
            return None
        sc = StandardScaler().fit(X[:cut])
        lr = LogisticRegression(max_iter=300).fit(sc.transform(X[:cut]), y[:cut])
        return float(roc_auc_score(y[cut:], lr.predict_proba(sc.transform(X[cut:]))[:, 1]))
    big = (absm > np.median(absm)).astype(int)
    return {"n_test": n - cut, "dir_auc": _auc(up), "big_move_auc": _auc(big)}


# ───────────────────────── orchestration ──────────────────────────────────────────────
def _load_bars(days):
    from datetime import datetime, timedelta, timezone
    sys.path.insert(0, os.path.dirname(__file__))
    from backfill_trade_features import download_day, load_aggtrades
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    dates = [(end - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    bars = None
    for d in dates:
        try:
            ts, price, qty, m = load_aggtrades(download_day(d))
        except Exception as e:
            print(f"[{d}] skip ({str(e)[:55]})"); continue
        b = per_minute_bars(ts, price, qty, m)
        print(f"[{d}] {len(ts):,} trades -> {len(b['close'])} minute bars")
        if bars is None:
            bars = b
        else:
            for k in bars:
                bars[k] = np.concatenate([bars[k], b[k]])
    return bars


def main(feature, days):
    bars = _load_bars(days)
    if bars is None or len(bars["close"]) < 500:
        sys.exit("not enough bars")
    feats = list(FEATURE_BUILDERS) if feature == "all" else [feature]
    print(f"\n{len(bars['close'])} total minute-bars. Testing: {feats}\n")
    print(f"  {'feature':<14}{'h':>4}{'dir_AUC':>9}{'big_move_AUC':>14}  read")
    for fname in feats:
        try:
            X, names = FEATURE_BUILDERS[fname](bars)
        except Exception as e:
            print(f"  {fname:<14} builder error: {str(e)[:50]}"); continue
        for h in HORIZONS:
            up, absm = make_labels(bars["close"], h)
            r = evaluate(X, up, absm)
            if not r or r["dir_auc"] is None:
                print(f"  {fname:<14}{h:>3}m  (insufficient)"); continue
            da, ba = r["dir_auc"], r["big_move_auc"] or 0.5
            read = ("DIR edge?" if da >= 0.55 else "") + (" TIMING edge?" if ba >= 0.55 else "")
            print(f"  {fname:<14}{h:>3}m{da:>9.3f}{ba:>14.3f}  {read or 'no edge'}")
    print("\nGUIDE: dir_AUC>=.55 = surprising direction edge (AUDIT for leakage first -- 5m is ~0.50).")
    print("big_move_AUC>=.55 = a TIMING edge -> candidate P(big_move) selectivity gate (validate further).")


def selftest():
    rng = np.random.default_rng(0)
    n = 4000
    # synthetic per-minute bars with a planted relationship: PERSISTENT aggressive flow (AR(1)) ->
    # price drifts that way for several minutes, so CVD[t] correlates with the forward h-min move
    # (realistic: flow imbalance persists). Direction edge should be clearly learnable.
    minute = np.arange(n, dtype=np.int64) * 1  # minute indices (unit ok for session math)
    phi, eps = 0.85, rng.normal(0, 1, n)
    cvd_drive = np.zeros(n)
    for t in range(1, n):
        cvd_drive[t] = phi * cvd_drive[t - 1] + eps[t]
    tb = np.clip(50 + cvd_drive * 15 + rng.normal(0, 4, n), 1, None)
    tsell = np.clip(50 - cvd_drive * 15 + rng.normal(0, 4, n), 1, None)
    ret = 0.0012 * cvd_drive + rng.normal(0, 0.0006, n)    # each minute drifts with current flow
    close = 100.0 * np.exp(np.cumsum(np.concatenate([[0], ret[:-1]])))  # shift: ret[t] moves t->t+1
    bars = {"minute": minute, "close": close, "high": close, "low": close,
            "vol": tb + tsell, "count": np.full(n, 100.0),
            "taker_buy": tb, "taker_sell": tsell, "large_signed": cvd_drive * 5}

    # per_minute_bars sanity (on tiny ticks): taker buy/sell split by is_buyer_maker
    ts = np.array([0, 100, 60000, 60100], dtype=np.int64)
    pr = np.array([100.0, 100.1, 100.2, 100.3]); q = np.array([1.0, 2.0, 3.0, 4.0])
    ibm = np.array([False, True, False, False])  # min0: buy1 sell2 ; min1: buy7
    pb = per_minute_bars(ts, pr, q, ibm)
    assert pb["taker_buy"][0] == 1.0 and pb["taker_sell"][0] == 2.0 and pb["taker_buy"][1] == 7.0

    # CVD feature should show a DIRECTION edge on this planted (persistent-flow) data.
    Xc, _ = _f_cvd(bars)
    up, absm = make_labels(close, 5)
    rc = evaluate(Xc, up, absm)
    assert rc and rc["dir_auc"] > 0.60, f"planted CVD->dir signal should be learned, got {rc}"
    # NULL property: any feature against genuinely RANDOM labels must read ~0.5 (no manufactured edge).
    Xs, _ = _f_session(bars)
    up_rand = rng.integers(0, 2, n)
    absm_rand = rng.uniform(0, 100, n)
    rs = evaluate(Xs, up_rand, absm_rand)
    assert rs and abs(rs["dir_auc"] - 0.5) < 0.08, f"random labels must be ~0.5, got {rs['dir_auc']}"
    # builders all produce finite, row-aligned matrices
    for name, fn in FEATURE_BUILDERS.items():
        X, names = fn(bars)
        assert X.shape[0] == n and X.shape[1] == len(names) and np.all(np.isfinite(X)), f"{name} bad matrix"
    print("edge_probe self-test: ALL PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--feature", default="all", help="all | " + " | ".join(FEATURE_BUILDERS))
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.feature != "all" and a.feature not in FEATURE_BUILDERS:
        ap.error(f"unknown feature '{a.feature}'. choose: all | " + " | ".join(FEATURE_BUILDERS))
    else:
        main(a.feature, a.days)
