"""
Shared trade-derived feature computation — the train/serve CONSISTENCY keystone.
=================================================================================
The SAME functions here are used by BOTH:
  • the LIVE path (order_flow.py / the per-candle signal_history snapshot), and
  • the historical BACKFILL (backfill_trade_features.py, reading data.binance.vision).
so the model trains and serves on identically-defined features. If these two paths ever
compute CVD/VPIN differently, the model learns one distribution and is served another →
silent live degradation. That is the single biggest risk in the V3 plan, so it lives in
ONE module with a deterministic self-test.

MARKET: the live feed is BINANCE **SPOT** `btcusdt@aggTrade` (see data_ingestion.SPOT_WS).
The backfill MUST therefore use SPOT aggTrades:
    https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-YYYY-MM-DD.zip
NOT the futures (um) path. (Funding-rate features are a separate, inherently-futures feed.)

AGGRESSOR CONVENTION (Binance aggTrade `m` / is_buyer_maker):
    is_buyer_maker = True   → the buyer was the maker → the TAKER SOLD  → aggressive SELL → negative delta
    is_buyer_maker = False  → the buyer was the taker → the TAKER BOUGHT → aggressive BUY  → positive delta
This matches order_flow.OrderFlowAnalyzer.process_trade: `is_buy = not is_buyer_maker`.
"""

import numpy as np

# Live rolling-window definitions, mirrored from order_flow.OrderFlowAnalyzer:
#   cvd_1m  = get_time_based_cvd(60)   → signed volume in the 60s ENDING AT candle close
#   cvd_5m  = get_time_based_cvd(300)  → signed volume in the 300s ending at close
#   cvd_change = get_cvd_change(30)    → signed volume of the LAST 30 TRADES ending at close
CVD_1M_WINDOW_MS = 60_000
CVD_5M_WINDOW_MS = 300_000
CVD_CHANGE_TRADES = 30


def signed_quantity(quantity, is_buyer_maker) -> np.ndarray:
    """Per-trade signed volume (+ aggressive buy, − aggressive sell)."""
    q = np.asarray(quantity, dtype=np.float64)
    m = np.asarray(is_buyer_maker, dtype=bool)
    return np.where(m, -q, q)


def per_bar_cvd(ts_ms, signed_qty, bar_close_ms) -> dict:
    """For each bar-close timestamp, reproduce the EXACT rolling CVD values the live
    recorder reports at candle close. ts_ms / signed_qty are per-trade arrays (any order).

    Returns dict of arrays aligned to `bar_close_ms`:
        cvd_1m, cvd_5m, cvd_change, delta
    `delta` = signed volume within the trailing 60s (== cvd_1m for 1m bars; this is the
    per-bar order-flow imbalance used by CVD-divergence downstream).
    """
    ts = np.asarray(ts_ms, dtype=np.int64)
    sq = np.asarray(signed_qty, dtype=np.float64)
    order = np.argsort(ts, kind="mergesort")
    ts = ts[order]
    sq = sq[order]
    prefix = np.concatenate([[0.0], np.cumsum(sq)])  # prefix[i] = sum of first i trades

    closes = np.asarray(bar_close_ms, dtype=np.int64)

    def window_sum(end_ms: int, window_ms: int) -> float:
        lo = int(np.searchsorted(ts, end_ms - window_ms, side="right"))
        hi = int(np.searchsorted(ts, end_ms, side="right"))
        return float(prefix[hi] - prefix[lo])

    cvd_1m = np.array([window_sum(int(e), CVD_1M_WINDOW_MS) for e in closes], dtype=np.float64)
    cvd_5m = np.array([window_sum(int(e), CVD_5M_WINDOW_MS) for e in closes], dtype=np.float64)

    cc = np.empty(len(closes), dtype=np.float64)
    for i, e in enumerate(closes):
        hi = int(np.searchsorted(ts, int(e), side="right"))
        lo = max(0, hi - CVD_CHANGE_TRADES)
        cc[i] = float(prefix[hi] - prefix[lo])

    return {"cvd_1m": cvd_1m, "cvd_5m": cvd_5m, "cvd_change": cc, "delta": cvd_1m.copy()}


# Large-trade threshold — shared by live (order_flow streaming EWMA) and backfill (batch),
# so a trade is flagged "large" by the SAME rule in both. A trade is large if its notional
# exceeds LARGE_TRADE_MULT × the EWMA mean notional of prior trades. EWMA (not a fixed $
# threshold) so it adapts to volume regime, per the research's caution against fixed cutoffs.
LARGE_TRADE_EWMA_ALPHA = 0.001   # ~1000-trade effective lookback
LARGE_TRADE_MULT = 5.0


def large_trade_per_bar(ts_ms, price, quantity, is_buyer_maker, bar_close_ms,
                        window_ms: int = 60_000,
                        alpha: float = LARGE_TRADE_EWMA_ALPHA,
                        mult: float = LARGE_TRADE_MULT) -> dict:
    """Per-bar large-trade flow, computed identically to the live recorder (same alpha/mult).
    A trade is 'large' if its notional > mult × EWMA-of-prior-notional (causal). For each bar
    close, over the trailing `window_ms`:
        large_trade_delta     = Σ large signed_notional / Σ all notional   ∈ [-1,1]
        large_trade_imbalance = (Σ large buy − Σ large sell)/(Σ large buy + Σ large sell) ∈ [-1,1]
    Returns dict of arrays aligned to bar_close_ms. (Backfill uses pandas ewm; the live
    streaming EWMA in order_flow produces the identical recursion.)"""
    import pandas as pd
    ts = np.asarray(ts_ms, dtype=np.int64)
    px = np.asarray(price, dtype=np.float64)
    qty = np.asarray(quantity, dtype=np.float64)
    bm = np.asarray(is_buyer_maker, dtype=bool)
    order = np.argsort(ts, kind="mergesort")
    ts, px, qty, bm = ts[order], px[order], qty[order], bm[order]

    notional = px * qty
    signed = np.where(bm, -notional, notional)          # + aggressive buy, − aggressive sell
    if len(notional) == 0:
        z = np.zeros(len(bar_close_ms))
        return {"large_trade_delta": z, "large_trade_imbalance": z}
    # EWMA of notional using PRIOR trades (shift by 1) so the flag is causal.
    ewma = pd.Series(notional).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    ewma_prior = np.concatenate([[notional[0]], ewma[:-1]])
    is_large = notional > (mult * ewma_prior)

    large_signed = np.where(is_large, signed, 0.0)
    large_buy = np.where(is_large & (signed > 0), notional, 0.0)
    large_sell = np.where(is_large & (signed < 0), notional, 0.0)

    pre_n = np.concatenate([[0.0], np.cumsum(notional)])
    pre_ls = np.concatenate([[0.0], np.cumsum(large_signed)])
    pre_lb = np.concatenate([[0.0], np.cumsum(large_buy)])
    pre_lsell = np.concatenate([[0.0], np.cumsum(large_sell)])
    closes = np.asarray(bar_close_ms, dtype=np.int64)

    def wsum(pre, end, w):
        lo = int(np.searchsorted(ts, end - w, side="right"))
        hi = int(np.searchsorted(ts, end, side="right"))
        return pre[hi] - pre[lo]

    delta = np.empty(len(closes)); imb = np.empty(len(closes))
    for i, e in enumerate(closes):
        e = int(e)
        tot = wsum(pre_n, e, window_ms)
        ls = wsum(pre_ls, e, window_ms)
        lb = wsum(pre_lb, e, window_ms)
        lsell = wsum(pre_lsell, e, window_ms)
        delta[i] = (ls / tot) if tot > 0 else 0.0
        imb[i] = ((lb - lsell) / (lb + lsell)) if (lb + lsell) > 0 else 0.0
    return {"large_trade_delta": np.clip(delta, -1.0, 1.0),
            "large_trade_imbalance": np.clip(imb, -1.0, 1.0)}


def vpin_buckets(ts_ms, signed_qty, quantity, *, bucket_volume: float,
                 rolling_buckets: int = 50) -> dict:
    """Equal-VOLUME-bucket VPIN (Easley/López de Prado). A single trade may straddle a
    bucket boundary, so volume is split. For each completed bucket:
        imbalance = |buy_vol − sell_vol|
        vpin      = rolling_sum(imbalance, N) / (N × bucket_volume)
    Returns dict of arrays: bucket_end_ts, vpin (NaN until N buckets exist).

    Same function feeds live and backfill → identical `bucket_volume`/`rolling_buckets`
    must be used in both (see DEFAULT_* below).
    """
    ts = np.asarray(ts_ms, dtype=np.int64)
    sq = np.asarray(signed_qty, dtype=np.float64)
    q = np.asarray(quantity, dtype=np.float64)
    order = np.argsort(ts, kind="mergesort")
    ts, sq, q = ts[order], sq[order], q[order]

    if bucket_volume <= 0:
        return {"bucket_end_ts": np.array([], dtype=np.int64), "vpin": np.array([])}

    imbalances = []
    end_ts = []
    buy_in = sell_in = filled = 0.0
    for i in range(len(q)):
        remaining = q[i]
        is_sell = sq[i] < 0
        while remaining > 1e-15:
            cap = bucket_volume - filled
            take = remaining if remaining < cap else cap
            if is_sell:
                sell_in += take
            else:
                buy_in += take
            filled += take
            remaining -= take
            if filled >= bucket_volume - 1e-12:
                imbalances.append(abs(buy_in - sell_in))
                end_ts.append(int(ts[i]))
                buy_in = sell_in = filled = 0.0

    imb = np.asarray(imbalances, dtype=np.float64)
    et = np.asarray(end_ts, dtype=np.int64)
    vpin = np.full(len(imb), np.nan)
    if len(imb) >= rolling_buckets:
        csum = np.concatenate([[0.0], np.cumsum(imb)])
        for k in range(rolling_buckets - 1, len(imb)):
            s = csum[k + 1] - csum[k + 1 - rolling_buckets]
            vpin[k] = s / (rolling_buckets * bucket_volume)
    return {"bucket_end_ts": et, "vpin": vpin}


def map_vpin_to_bars(bucket_end_ts, vpin, bar_close_ms) -> np.ndarray:
    """Assign each bar the latest COMPLETED VPIN bucket value at/<= its close (backward
    as-of join). Bars before the first valid bucket get 0.0."""
    et = np.asarray(bucket_end_ts, dtype=np.int64)
    vp = np.asarray(vpin, dtype=np.float64)
    closes = np.asarray(bar_close_ms, dtype=np.int64)
    out = np.zeros(len(closes), dtype=np.float64)
    if len(et) == 0:
        return out
    idx = np.searchsorted(et, closes, side="right") - 1
    for i, j in enumerate(idx):
        if j >= 0 and not np.isnan(vp[j]):
            out[i] = vp[j]
    return out


def cvd_divergence(cvd_1m_series, closes, lookback: int = 20) -> np.ndarray:
    """Per-bar CVD/price divergence, computed identically live (from recorded cvd_1m
    history) and in backfill (from per_bar_cvd cvd_1m). Builds a cumulative CVD from the
    per-bar cvd_1m deltas, then compares its `lookback`-bar change-sign to price's:
        +1  bearish divergence (price up, CVD down)
        −1  bullish divergence (price down, CVD up)
         0  no divergence
    Causal (uses only past `lookback` bars) → no look-ahead.
    """
    d = np.asarray(cvd_1m_series, dtype=np.float64)
    c = np.asarray(closes, dtype=np.float64)
    cvd = np.cumsum(d)
    out = np.zeros(len(c), dtype=np.float64)
    for i in range(lookback, len(c)):
        dp = c[i] - c[i - lookback]
        dc = cvd[i] - cvd[i - lookback]
        if dp > 0 and dc < 0:
            out[i] = 1.0
        elif dp < 0 and dc > 0:
            out[i] = -1.0
    return out


# Shared VPIN parameterization — MUST be identical in live + backfill (a calibrated-per-run
# bucket volume would make historical VPIN a different feature than live VPIN = silent skew).
# 15 BTC ≈ one bucket per ~1 minute of typical BTCUSDT spot volume → 50-bucket rolling VPIN
# spans ~50 min of flow, and the live recorder warms up within ~1 hour of boot.
DEFAULT_ROLLING_BUCKETS = 50
DEFAULT_BUCKET_VOLUME_BTC = 15.0


def _selftest() -> None:
    """Deterministic consistency test — proves the formulas without any download."""
    # Trades: buys (+) and sells (−) at known seconds, one 1m bar closing at t=60_000.
    # times in ms within [0, 60s]; qty in BTC.
    ts = np.array([1_000, 5_000, 30_000, 55_000, 59_000], dtype=np.int64)
    qty = np.array([1.0, 2.0, 0.5, 3.0, 1.0], dtype=np.float64)
    bm = np.array([False, True, False, True, False])  # buy, sell, buy, sell, buy
    sq = signed_quantity(qty, bm)
    assert np.allclose(sq, [1.0, -2.0, 0.5, -3.0, 1.0]), sq

    bars = np.array([60_000], dtype=np.int64)
    r = per_bar_cvd(ts, sq, bars)
    # all 5 trades are within the trailing 60s of close=60_000 → cvd_1m = sum = -2.5
    assert abs(r["cvd_1m"][0] - (-2.5)) < 1e-9, r["cvd_1m"]
    assert abs(r["cvd_5m"][0] - (-2.5)) < 1e-9, r["cvd_5m"]
    assert abs(r["cvd_change"][0] - (-2.5)) < 1e-9, r["cvd_change"]  # <30 trades

    # Second bar close at 120_000 with no trades after 60s → cvd_1m must be 0 (nothing in trailing 60s)
    bars2 = np.array([120_000], dtype=np.int64)
    r2 = per_bar_cvd(ts, sq, bars2)
    assert abs(r2["cvd_1m"][0]) < 1e-9, r2["cvd_1m"]
    # but cvd_change (last 30 trades, time-agnostic) still sees all 5 → -2.5
    assert abs(r2["cvd_change"][0] - (-2.5)) < 1e-9, r2["cvd_change"]

    # VPIN: bucket_volume=4 BTC. total volume=7.5 → 1 full bucket (4) + remainder.
    v = vpin_buckets(ts, sq, qty, bucket_volume=4.0, rolling_buckets=1)
    # first bucket fills with: +1(buy),  −2(sell), +0.5(buy), then 0.5 of the −3 sell → buy=1.5, sell=2.5
    assert abs(v["vpin"][0] - (abs(1.5 - 2.5) / (1 * 4.0))) < 1e-9, v["vpin"]

    # Divergence: price up but CVD down over lookback=2 → +1 (bearish)
    cvd1m = np.array([0.0, -1.0, -1.0])
    closes = np.array([100.0, 101.0, 102.0])
    dv = cvd_divergence(cvd1m, closes, lookback=2)
    assert dv[2] == 1.0, dv

    # Large-trade: one huge aggressive BUY among small trades dominates the bar.
    ts2 = np.array([1000, 2000, 3000, 4000, 5000, 55000], dtype=np.int64)
    px2 = np.full(6, 60000.0)
    qty2 = np.array([0.01, 0.01, 0.01, 0.01, 0.01, 5.0])      # last is huge
    bm2 = np.array([True, True, True, True, True, False])      # last is a buy (taker)
    lt = large_trade_per_bar(ts2, px2, qty2, bm2, np.array([60_000], dtype=np.int64))
    assert lt["large_trade_delta"][0] > 0.9, lt["large_trade_delta"]
    assert lt["large_trade_imbalance"][0] == 1.0, lt["large_trade_imbalance"]

    print("trade_features self-test: PASS")


if __name__ == "__main__":
    _selftest()
