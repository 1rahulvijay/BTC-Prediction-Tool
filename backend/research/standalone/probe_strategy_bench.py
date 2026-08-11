"""
probe_strategy_bench.py - test published quant strategies on their NATIVE objective.
====================================================================================
Most quant papers were NOT written for 5m BTC direction. This bench evaluates each strategy
FAMILY on the thing it was actually published to do, on our data, and reports app-fit:

  1. Time-series MOMENTUM / REVERSAL (Moskowitz; Lo-MacKinlay) -> native: is next-period return
     sign predictable at ANY horizon (5m..4h)? Reports momentum hit-rate + lag-1 autocorr.
  2. HURST exponent (Mandelbrot) -> native: trending (H>0.5) vs mean-reverting (H<0.5) per horizon.
  3. VARIANCE RATIO (Lo-MacKinlay 1988) -> native: random-walk test; VR<1 revert, >1 trend.
  4. Intraday SEASONALITY (Heston-Korajczyk; Gao intraday momentum) -> native: hour/day vol &
     return effects.
  5. Volatility CLUSTERING / HAR (Engle; Corsi) -> native: is |return| autocorrelated (the edge)?

The honest question this answers: "do these strategies work on what they were built for, here?"
If return-sign is a coin-flip at EVERY horizon, the ceiling is horizon-wide; if momentum appears
at, say, 4h, that is a real (different-product) edge. All measured out-of-sample where it's a bet.

Read-only; OHLCV from binance_updown_rounds (5m bars aggregated up). ASCII output.

Usage:
  python backend/research/standalone/probe_strategy_bench.py
  python backend/research/standalone/probe_strategy_bench.py --selftest
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


def base_5m() -> pd.DataFrame:
    return TA.load_ohlcv(5)


def aggregate(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """Aggregate k consecutive 5m bars into one bar (open=first, close=last, high/low/vol)."""
    g = df.index // k
    out = pd.DataFrame({
        "open": df["open"].groupby(g).first(),
        "high": df["high"].groupby(g).max(),
        "low": df["low"].groupby(g).min(),
        "close": df["close"].groupby(g).last(),
        "volume": df["volume"].groupby(g).sum(),
    }).reset_index(drop=True)
    return out


def _binom_z(p, n):
    return (p - 0.5) / np.sqrt(0.25 / n) if n else 0.0


def hurst(ts: np.ndarray, max_lag: int = 40) -> float:
    """Rescaled-range Hurst exponent via lagged std scaling."""
    ts = np.asarray(ts, float)
    lags = range(2, min(max_lag, len(ts) // 2))
    tau = [np.std(ts[lag:] - ts[:-lag]) for lag in lags]
    tau = np.array(tau)
    ok = tau > 0
    if ok.sum() < 5:
        return float("nan")
    return float(np.polyfit(np.log(np.array(list(lags))[ok]), np.log(tau[ok]), 1)[0])


def variance_ratio(r: np.ndarray, q: int) -> float:
    r = np.asarray(r, float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < q * 4:
        return float("nan")
    var1 = np.var(r, ddof=1)
    rq = np.convolve(r, np.ones(q), "valid")
    varq = np.var(rq, ddof=1)
    return float(varq / (q * var1)) if var1 > 0 else float("nan")


def momentum_test():
    base = base_5m()
    print("\n" + "=" * 92)
    print("1) TIME-SERIES MOMENTUM / REVERSAL  (native: is next return-sign predictable at any horizon?)")
    print(f"{'horizon':<10}{'n':<8}{'momentum hit%':<16}{'z vs 50%':<12}{'lag1 autocorr':<16}{'VR(2)':<8}verdict")
    print("-" * 92)
    for label, k in (("5m", 1), ("15m", 3), ("30m", 6), ("1h", 12), ("2h", 24), ("4h", 48)):
        agg = aggregate(base, k)
        c = agg["close"]
        r = np.log(c / c.shift(1)).dropna().values
        if len(r) < 200:
            continue
        sig = np.sign(r[:-1]); act = np.sign(r[1:])
        m = sig != 0
        hit = float((sig[m] == act[m]).mean())
        z = _binom_z(hit, m.sum())
        ac = float(pd.Series(r).autocorr(lag=1))
        vr2 = variance_ratio(r, 2)
        verdict = ("MOMENTUM" if z > 3 and hit > 0.5 else "REVERSION" if z < -3 and hit < 0.5
                   else "random walk (coin-flip)")
        print(f"{label:<10}{len(r):<8}{hit*100:<16.2f}{z:<12.1f}{ac:<16.4f}{vr2:<8.3f}{verdict}")
    print("  READ: hit% ~50 + z in [-3,3] + VR~1 + autocorr~0 = random walk = no directional edge at that horizon.")


def hurst_vr_test():
    base = base_5m()
    print("\n" + "=" * 92)
    print("2-3) HURST exponent & VARIANCE RATIO  (native: trend vs mean-revert character per horizon)")
    print(f"{'horizon':<10}{'Hurst':<10}{'VR(2)':<10}{'VR(5)':<10}{'VR(10)':<10}character")
    print("-" * 92)
    for label, k in (("5m", 1), ("15m", 3), ("1h", 12), ("4h", 48)):
        agg = aggregate(base, k)
        lc = np.log(agg["close"].values)
        r = np.diff(lc)
        H = hurst(lc)
        v2, v5, v10 = variance_ratio(r, 2), variance_ratio(r, 5), variance_ratio(r, 10)
        ch = ("trending" if H > 0.55 else "mean-reverting" if H < 0.45 else "~random walk")
        print(f"{label:<10}{H:<10.3f}{v2:<10.3f}{v5:<10.3f}{v10:<10.3f}{ch}")
    print("  READ: H~0.5 and VR~1.0 across horizons = efficient random walk (no exploitable trend/reversion).")


def seasonality_test():
    base = base_5m()
    base["ts"] = TA.load_ohlcv(5)["ts"]
    r = np.log(base["close"] / base["close"].shift(1))
    base["ret"] = r
    base["absret"] = r.abs()
    base["hour"] = base["ts"].dt.hour
    base["dow"] = base["ts"].dt.dayofweek
    print("\n" + "=" * 92)
    print("4) INTRADAY SEASONALITY  (native: hour/day vol & directional effects)")
    hv = base.groupby("hour")["absret"].mean()
    print(f"  VOL by hour (UTC): peak h{hv.idxmax()}={hv.max()*1e4:.1f}bps  trough h{hv.idxmin()}={hv.min()*1e4:.1f}bps "
          f"(ratio {hv.max()/hv.min():.2f}x)  -> REAL vol seasonality" if hv.max()/hv.min() > 1.3 else "  weak vol seasonality")
    # directional seasonality: is any hour's mean return significant?
    g = base.groupby("hour")["ret"]
    zmax = (g.mean() / (g.std() / np.sqrt(g.count()))).abs().max()
    print(f"  DIRECTION by hour: max |z| of hourly mean-return = {zmax:.1f} "
          f"({'a session bias exists' if zmax > 3 else 'no significant hour is directional (coin-flip)'})")
    dv = base.groupby("dow")["absret"].mean()
    print(f"  VOL by day-of-week: peak d{dv.idxmax()} trough d{dv.idxmin()} (ratio {dv.max()/dv.min():.2f}x)")


def vol_clustering_test():
    base = base_5m()
    print("\n" + "=" * 92)
    print("5) VOLATILITY CLUSTERING / ARCH  (native: is |return| autocorrelated = the proven edge?)")
    for label, k in (("5m", 1), ("15m", 3), ("1h", 12)):
        agg = aggregate(base, k)
        r = np.log(agg["close"] / agg["close"].shift(1)).dropna()
        ac_abs = float(r.abs().autocorr(lag=1))
        ac_ret = float(r.autocorr(lag=1))
        print(f"  {label:<6} autocorr(|ret|)={ac_abs:+.3f} (vol clustering)   autocorr(ret)={ac_ret:+.3f} (direction)")
    print("  READ: |ret| autocorr >> 0 (predictable vol) while ret autocorr ~0 (coin-flip direction) "
          "= the whole thesis in one line.")


def selftest():
    # synthetic AR(1) with positive autocorr -> momentum detectable; check helpers run
    rng = np.random.default_rng(0); n = 5000
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = 0.3 * r[i - 1] + rng.normal(0, 1)   # positive autocorr = momentum
    H = hurst(np.cumsum(r)); vr = variance_ratio(r, 2)
    sig = np.sign(r[:-1]); act = np.sign(r[1:]); hit = (sig == act).mean()
    print(f"selftest: AR(1)+ -> momentum hit={hit:.3f} (expect>0.55), VR(2)={vr:.2f} (expect>1), Hurst={H:.2f}")
    ok = hit > 0.55 and vr > 1.0
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    momentum_test()
    hurst_vr_test()
    seasonality_test()
    vol_clustering_test()
    print("\nAPP-FIT: vol/seasonality effects -> sharpen the timing/selectivity + P(hold) heads. "
          "Any horizon showing real momentum/reversion (z>3) -> a candidate longer-horizon product. "
          "If every horizon is a random walk on direction, the ceiling is horizon-wide -- bet on "
          "selectivity + Polymarket mispricing, not direction.")


if __name__ == "__main__":
    main()
