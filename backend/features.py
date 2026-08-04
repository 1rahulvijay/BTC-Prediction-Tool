"""
Technical Indicators & Feature Engineering
Pure numpy implementations + expanded feature vector construction.
109 features: price/volume, order-flow microstructure, derivatives, advanced
indicators, feature interactions, cross-exchange signals, liquidations, volatility,
deep microstructure, regime/vol forecasting and institutional alpha feeds.
"""

import os
import numpy as np
from typing import Optional


# ──────────────────────────────────────────────
#  Technical Indicators
# ──────────────────────────────────────────────

def sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    if len(data) < period:
        return np.full(len(data), np.nan)
    kernel = np.ones(period) / period
    result = np.convolve(data, kernel, mode="full")[:len(data)]
    result[:period - 1] = np.nan
    return result


def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average."""
    result = np.full(len(data), np.nan)
    if len(data) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    result = np.full(len(closes), np.nan)
    if len(closes) < period + 1:
        return result

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
    result[period] = 100.0 - 100.0 / (1.0 + rs)

    for i in range(period, len(deltas)):
        gain = gains[i]
        loss = losses[i]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
        result[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    return result


def stochastic_rsi(closes: np.ndarray, rsi_period: int = 14, stoch_period: int = 14) -> np.ndarray:
    """Stochastic RSI — RSI normalized to 0-100 range over lookback window."""
    rsi_vals = rsi(closes, rsi_period)
    result = np.full(len(closes), np.nan)
    for i in range(rsi_period + stoch_period - 1, len(closes)):
        window = rsi_vals[i - stoch_period + 1:i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) < 2:
            continue
        rsi_min = np.min(valid)
        rsi_max = np.max(valid)
        rng = rsi_max - rsi_min
        if rng > 0:
            result[i] = (rsi_vals[i] - rsi_min) / rng * 100
        else:
            result[i] = 50.0
    return result


def adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average Directional Index — trend strength indicator (0-100)."""
    n = len(closes)
    result = np.full(n, np.nan)
    if n < period * 2 + 1:
        return result

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0

    atr_smooth = ema(tr, period)
    plus_di = np.where(atr_smooth > 0, ema(plus_dm, period) / atr_smooth * 100, 0)
    minus_di = np.where(atr_smooth > 0, ema(minus_dm, period) / atr_smooth * 100, 0)

    dx = np.where(
        (plus_di + minus_di) > 0,
        np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100,
        0
    )
    result = ema(dx, period)
    return result


def supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 10, multiplier: float = 3.0) -> np.ndarray:
    """SuperTrend Indicator."""
    n = len(closes)
    result = np.full(n, np.nan)
    if n < period:
        return result

    tr = np.zeros(n)
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)
    
    atr_vals = sma(tr, period)
    
    basic_ub = np.zeros(n)
    basic_lb = np.zeros(n)
    final_ub = np.zeros(n)
    final_lb = np.zeros(n)
    trend = np.ones(n)
    
    for i in range(period, n):
        basic_ub[i] = (highs[i] + lows[i]) / 2 + multiplier * atr_vals[i]
        basic_lb[i] = (highs[i] + lows[i]) / 2 - multiplier * atr_vals[i]
        
        if i == period:
            final_ub[i] = basic_ub[i]
            final_lb[i] = basic_lb[i]
            result[i] = final_lb[i] if closes[i] > final_ub[i] else final_ub[i]
            continue
            
        final_ub[i] = basic_ub[i] if basic_ub[i] < final_ub[i-1] or closes[i-1] > final_ub[i-1] else final_ub[i-1]
        final_lb[i] = basic_lb[i] if basic_lb[i] > final_lb[i-1] or closes[i-1] < final_lb[i-1] else final_lb[i-1]
        
        if closes[i] > final_ub[i-1]:
            trend[i] = 1
        elif closes[i] < final_lb[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]
            
        result[i] = final_lb[i] if trend[i] == 1 else final_ub[i]
        
    return result


def obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume."""
    result = np.zeros(len(closes))
    result[0] = volumes[0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result[i] = result[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            result[i] = result[i - 1] - volumes[i]
        else:
            result[i] = result[i - 1]
    return result


def williams_r(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Williams %R — oscillator (-100 to 0)."""
    result = np.full(len(closes), np.nan)
    for i in range(period - 1, len(closes)):
        hh = np.max(highs[i - period + 1:i + 1])
        ll = np.min(lows[i - period + 1:i + 1])
        rng = hh - ll
        if rng > 0:
            result[i] = -100 * (hh - closes[i]) / rng
        else:
            result[i] = -50.0
    return result


def cci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 20) -> np.ndarray:
    """Commodity Channel Index."""
    tp = (highs + lows + closes) / 3.0
    result = np.full(len(closes), np.nan)
    for i in range(period - 1, len(closes)):
        window = tp[i - period + 1:i + 1]
        mean_tp = np.mean(window)
        mean_dev = np.mean(np.abs(window - mean_tp))
        if mean_dev > 0:
            result[i] = (tp[i] - mean_tp) / (0.015 * mean_dev)
        else:
            result[i] = 0.0
    return result


def mfi(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray, period: int = 14) -> np.ndarray:
    """Money Flow Index — volume-weighted RSI (0-100)."""
    tp = (highs + lows + closes) / 3.0
    raw_money_flow = tp * volumes
    result = np.full(len(closes), np.nan)

    for i in range(period, len(closes)):
        pos_flow = 0.0
        neg_flow = 0.0
        for j in range(i - period + 1, i + 1):
            if j == 0:
                continue
            if tp[j] > tp[j - 1]:
                pos_flow += raw_money_flow[j]
            elif tp[j] < tp[j - 1]:
                neg_flow += raw_money_flow[j]

        if neg_flow > 0:
            mf_ratio = pos_flow / neg_flow
            result[i] = 100 - 100 / (1 + mf_ratio)
        else:
            result[i] = 100.0

    return result


def roc(closes: np.ndarray, period: int) -> np.ndarray:
    """Rate of Change."""
    result = np.full(len(closes), np.nan)
    for i in range(period, len(closes)):
        if closes[i - period] > 0:
            result[i] = (closes[i] - closes[i - period]) / closes[i - period] * 100
    return result


def macd(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD line, signal line, histogram."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow

    # Signal line = EMA of MACD line (ignoring NaN)
    valid_mask = ~np.isnan(macd_line)
    valid_macd = macd_line[valid_mask]
    signal_full = np.full(len(closes), np.nan)
    if len(valid_macd) >= signal:
        sig = ema(valid_macd, signal)
        signal_full[valid_mask] = sig

    histogram = macd_line - signal_full
    return macd_line, signal_full, histogram


def bollinger_bands(closes: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """Bollinger Bands: upper, middle, lower, bandwidth (0-1 position)."""
    middle = sma(closes, period)
    upper = np.full(len(closes), np.nan)
    lower = np.full(len(closes), np.nan)
    bandwidth = np.full(len(closes), np.nan)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1: i + 1]
        std = np.std(window)
        upper[i] = middle[i] + std_dev * std
        lower[i] = middle[i] - std_dev * std
        band_range = upper[i] - lower[i]
        bandwidth[i] = (closes[i] - lower[i]) / band_range if band_range > 0 else 0.5

    return upper, middle, lower, bandwidth


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    tr = np.zeros(len(closes))
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)
    return ema(tr, period)


# Bump when the NUMERIC MEANING of any feature changes without its name changing.
# artifact_identity hashes features.py into code_hash, so an edit here already
# invalidates artifacts under strict mode - but strict mode can be off, and a hash
# says only "something changed". This constant says WHAT changed, and
# check_feature_contract.py reports it in plain words.
FEATURE_SEMANTICS_VERSION = 5
FEATURE_SEMANTICS_CHANGELOG = {
    5: "2026-08-04 three feature defects, all value-changing. (a) volume_profile_lvn_distance: "
       "argmin() over ALL bins selected the first ZERO-volume bin - normally the lowest price "
       "never traded - so it measured the range bottom, not a low-volume node; now restricted "
       "to occupied bins. (b) time_to_funding: a single cos() is symmetric, so 25% and 75% "
       "through the cycle both mapped to 0; now a monotone fraction remaining. (c) "
       "cross_exchange_lead_lag: subtracted an ETH dollar change from a BTC dollar change "
       "(incomparable scales) with no lag at all; now ETH's LAGGED log return minus BTC's "
       "current log return. Models trained under v4 consumed all three and MUST be retrained.",
    4: "2026-07-31 cvd_slope_divergence: full-dataset standard deviation -> "
       "causal trailing scale; cross-asset leading gaps no longer backfill from a "
       "future first observation. Appending future data previously changed past rows.",
    3: "2026-07-28 vwap(): bar-count window -> TRUE duration window. The bar count was "
       "derived from the median timestamp delta, which over-reached across gaps (a 6h gap "
       "made a '1440 bar' window span 29.98h, not 24h). Now resolved against the clock "
       "per bar. Models trained under v2 saw the over-reaching window.",
    2: "2026-07-28 vwap(): cumulative-from-bar-0 -> trailing time-anchored window. "
       "Models trained under v1 consumed a near-constant VWAP and MUST be retrained.",
    1: "original: vwap() cumulative from bar 0",
}


def vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray,
         period: int = 1440, times: np.ndarray | None = None,
         window_seconds: float | None = None) -> np.ndarray:
    """Trailing VWAP. Window is a DURATION when `times` is supplied, else a bar count.

    2026-07-28 fix: this was cumulative from bar 0 (np.cumsum). Over a long buffer the
    denominator grows so large that recent volume cannot move the line - at 30 days of 1m
    bars a new bar carries ~1/43,200 of the weight - so the feature flatlines toward a
    constant and stops carrying information.

    The naive replacement (a fixed 1440-BAR window) silently means 24h on 1m bars but FIVE
    DAYS on 5m bars. Pass `times` (epoch seconds or ms, auto-detected) and the window is
    resolved in real time instead:

        vwap(h, l, c, v, times=t)                      -> trailing 24 hours
        vwap(h, l, c, v, times=t, window_seconds=3600) -> trailing 1 hour
        vwap(h, l, c, v, period=1440)                  -> trailing 1440 BARS (legacy)

    With `times` the bar spacing is measured from the median delta, so irregular or gapped
    series resolve to the intended duration rather than an accidental multiple of it.

    LIMIT: the ms->s detection is magnitude-based (> 1e11), the same rule features._t_s
    uses. It needs real epoch values. Relative offsets starting near 0 are ambiguous
    between units by construction and are treated as seconds.

    Causal by construction: element k of a 'full' convolution sums only inputs j <= k, so
    no bar sees the future. The first `period-1` bars are a natural expanding window.
    """
    n = len(closes)
    if n == 0:
        return np.array([], dtype=float)
    tp = (highs + lows + closes) / 3.0

    # ---- true DURATION window (requires timestamps) --------------------------------
    if times is not None and len(times) == n and n >= 2:
        t = np.asarray(times, dtype=np.float64)
        t = np.where(t > 1e11, t / 1000.0, t)          # ms -> s, same rule as _t_s
        # `is not None`, not truthiness: window_seconds=0 is falsy and would otherwise
        # silently become the 24h default instead of being rejected.
        span = 86_400.0 if window_seconds is None else float(window_seconds)
        if span <= 0:
            raise ValueError(f"window_seconds must be > 0, got {window_seconds}")

        # An earlier version derived a BAR COUNT from the median delta. That silently
        # over-reaches whenever a gap sits inside the window: with a 6h recorder gap
        # among 1m bars, a "1440 bar" window covered 29.98 HOURS, not 24. The window is
        # now resolved against the clock for every bar, so gaps and irregular spacing
        # cannot stretch it.
        #
        # An earlier version repaired a backwards clock with np.maximum.accumulate. That
        # made the search work but CONCEALED corrupt input: a decreasing timestamp means
        # the kline history is out of order, and silently repairing it turns corrupt data
        # into apparently valid model input. Authoritative feature generation rejects it.
        # Duplicates ARE permitted - two observations can legitimately share a second -
        # and searchsorted(side="left") treats them as one boundary, deterministically.
        if not np.isfinite(t).all():
            raise ValueError("vwap: timestamps must be finite (found NaN or Inf)")
        diffs = np.diff(t)
        if np.any(diffs < 0):
            bad = int(np.argmax(diffs < 0)) + 1
            raise ValueError(
                f"vwap: timestamps must be non-decreasing; index {bad} goes backwards "
                f"({t[bad]:.0f} < {t[bad - 1]:.0f}). Refusing to repair a corrupt clock."
            )
        left = np.searchsorted(t, t - span, side="left")

        c_tpv = np.concatenate(([0.0], np.cumsum(tp * volumes)))
        c_vol = np.concatenate(([0.0], np.cumsum(volumes)))
        idx = np.arange(n)
        win_tpv = c_tpv[idx + 1] - c_tpv[left]
        win_vol = c_vol[idx + 1] - c_vol[left]
        return np.where(win_vol > 0, win_tpv / np.where(win_vol > 0, win_vol, 1.0), closes)

    # ---- explicit fixed BAR-COUNT window (legacy / no timestamps) --------------------
    if window_seconds is not None:
        raise ValueError("window_seconds requires `times`; bar spacing is unknown without it")
    if int(period) < 1:
        raise ValueError(f"vwap period must be >= 1 bar, got {period}")
    # A window longer than the buffer degrades back into the cumulative bug this replaced.
    period = min(int(period), n)
    kernel = np.ones(period)
    rolling_tpv = np.convolve(tp * volumes, kernel, mode="full")[:n]
    rolling_vol = np.convolve(volumes, kernel, mode="full")[:n]
    return np.where(rolling_vol > 0, rolling_tpv / rolling_vol, closes)


def heikin_ashi_trend(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Heikin-Ashi trend signal: +1 bullish, -1 bearish, 0 neutral."""
    n = len(closes)
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    # Trend signal: consecutive bullish/bearish HA candles
    result = np.zeros(n)
    for i in range(1, n):
        if ha_close[i] > ha_open[i]:
            result[i] = 1.0  # bullish
        elif ha_close[i] < ha_open[i]:
            result[i] = -1.0  # bearish
    return result


def compute_ewma_volatility(closes: np.ndarray, lambda_: float = 0.94) -> dict:
    """RiskMetrics EWMA volatility."""
    if len(closes) < 2:
        return {'ewma_vol': 0.0, 'vol_acceleration': 0.0}
    log_returns = np.diff(np.log(closes + 1e-9))
    ewma_vol = float(np.var(log_returns[-1:]))
    for r in log_returns[-50:]:
        ewma_vol = lambda_ * ewma_vol + (1 - lambda_) * (r ** 2)
    current_ewma = np.sqrt(ewma_vol)
    past_ewma = float(np.std(log_returns[-11:-1])) if len(log_returns) >= 11 else current_ewma
    return {'ewma_vol': current_ewma, 'vol_acceleration': current_ewma - past_ewma}

def compute_sr_features(closes: list, current_price: float, 
                         lookback: int = 200) -> dict:
    """
    Identifies nearest support and resistance from recent price history.
    Returns normalized distance to each as model features.
    """
    if len(closes) < lookback:
        return {'dist_to_resistance': 0.0, 'dist_to_support': 0.0,
                'sr_compression': 0.0}
    
    recent = closes[-lookback:]
    
    # Find local maxima and minima (simplified pivot detection)
    resistance_levels = []
    support_levels = []
    
    for i in range(2, len(recent) - 2):
        if recent[i] > recent[i-1] and recent[i] > recent[i+1] and \
           recent[i] > recent[i-2] and recent[i] > recent[i+2]:
            resistance_levels.append(recent[i])
        if recent[i] < recent[i-1] and recent[i] < recent[i+1] and \
           recent[i] < recent[i-2] and recent[i] < recent[i+2]:
            support_levels.append(recent[i])
    
    above = [r for r in resistance_levels if r > current_price]
    below = [s for s in support_levels if s < current_price]
    
    nearest_resistance = min(above) if above else current_price * 1.01
    nearest_support = max(below) if below else current_price * 0.99
    
    dist_to_res = (nearest_resistance - current_price) / current_price
    dist_to_sup = (current_price - nearest_support) / current_price
    
    # Compression: how tight is price between S and R?
    sr_compression = 1.0 / (dist_to_res + dist_to_sup + 1e-9)
    
    return {
        'dist_to_resistance': dist_to_res,
        'dist_to_support': dist_to_sup,
        'sr_compression': min(sr_compression, 10.0) / 10.0  # cap and normalize
    }

def opening_range_breakout(times_s: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                           closes: np.ndarray, or_minutes: int = 60):
    """Opening Range Breakout anchored to 00:00 UTC (BTC has no real 'open'; UTC midnight is
    the defensible, reproducible session anchor). For each UTC day the opening range = the
    high/low of the first `or_minutes`. Returns (orb_position, orb_breakout):
      orb_position = (close − OR_mid)/OR_width, scaled  — where price sits vs the range
      orb_breakout = +1 above OR high / −1 below OR low / 0 inside
    Causal: bars still INSIDE the forming OR window (and before the range completes) get 0,
    so there is no look-ahead. After the window closes the range is fixed for that day."""
    n = len(closes)
    pos = np.zeros(n)
    brk = np.zeros(n)
    day = (times_s // 86400).astype(np.int64)
    into_day = times_s - day * 86400
    or_sec = or_minutes * 60
    in_or = into_day < or_sec
    or_hi, or_lo = {}, {}
    for d in np.unique(day):
        m = (day == d) & in_or
        if m.any():
            or_hi[d] = float(highs[m].max())
            or_lo[d] = float(lows[m].min())
    for i in range(n):
        d = int(day[i])
        if (d in or_hi) and (not in_or[i]) and closes[i] > 0:
            hi, lo = or_hi[d], or_lo[d]
            w = hi - lo
            if w > 0:
                mid = (hi + lo) / 2.0
                pos[i] = np.clip((closes[i] - mid) / w, -3.0, 3.0) / 3.0
                brk[i] = 1.0 if closes[i] > hi else (-1.0 if closes[i] < lo else 0.0)
    return pos, brk


def rolling_volume_profile(closes: np.ndarray, volumes: np.ndarray,
                           window: int = 120, n_bins: int = 40):
    """Rolling volume-at-price profile (TPO/market-profile proxy) computed per-bar over a
    trailing window. Returns (poc_dist, lvn_dist, value_area_pos) as fractions of price:
      poc_dist        = (close − POC_price)/close   (POC = max-volume price bin)
      lvn_dist        = (close − LVN_price)/close    (LVN = min-volume bin = low-volume node)
      value_area_pos  = where close sits in [window low, window high]  ∈ [0,1]
    np.bincount keeps this C-fast (O(n·window) but the inner op is vectorized)."""
    n = len(closes)
    poc_dist = np.zeros(n)
    lvn_dist = np.zeros(n)
    va_pos = np.full(n, 0.5)
    for i in range(n):
        lo = max(0, i - window + 1)
        wc = closes[lo:i + 1]
        wv = volumes[lo:i + 1]
        pmin = wc.min()
        pmax = wc.max()
        rng = pmax - pmin
        if rng <= 0 or closes[i] <= 0:
            continue
        idx = ((wc - pmin) / rng * (n_bins - 1)).astype(np.int64)
        vol_by_bin = np.bincount(idx, weights=wv, minlength=n_bins)
        poc_price = pmin + (vol_by_bin.argmax() + 0.5) / n_bins * rng
        # A low-volume NODE is a thin bin that trading actually reached, not the emptiest
        # slot in the histogram. argmin() over all bins returns the first ZERO-volume bin -
        # usually the lowest price never traded in the window - so this feature was measuring
        # the bottom of the range rather than a node. Restrict to OCCUPIED bins.
        _occupied = np.flatnonzero(vol_by_bin > 0)
        if len(_occupied) == 0:
            lvn_dist[i] = 0.0
            continue
        _lvn_bin = _occupied[np.argmin(vol_by_bin[_occupied])]
        lvn_price = pmin + (_lvn_bin + 0.5) / n_bins * rng
        poc_dist[i] = (closes[i] - poc_price) / closes[i]
        lvn_dist[i] = (closes[i] - lvn_price) / closes[i]
        va_pos[i] = (closes[i] - pmin) / rng
    return poc_dist, lvn_dist, va_pos


# ──────────────────────────────────────────────
#  Feature Engine
# ──────────────────────────────────────────────

FEATURE_NAMES = [
    # Price & Volume (0-6)
    "price_return", "volume_norm", "rsi", "macd_hist", "bb_position",
    "atr_norm", "vwap_deviation",
    # Order Flow & Microstructure (7-15)
    "cvd_change", "cvd_1m", "cvd_5m", "book_imbalance", "obi_5", "obi_10", "obi_20", 
    "trade_intensity", "spread_norm",
    # Derivatives & Sentiment (16-20)
    "funding_rate", "funding_velocity", "oi_change", "long_short_ratio", "fear_greed_norm",
    # Advanced Indicators (21-33)
    "stoch_rsi", "adx_norm", "obv_change", "williams_r_norm",
    "cci_norm", "mfi_norm", "price_vs_ema9", "price_vs_ema21",
    "price_vs_sma50", "volume_ma_ratio", "roc_5", "roc_10",
    "heikin_ashi_trend",
    # Feature Interactions (34-37)
    "rsi_x_adx", "vol_x_trend", "obi_x_atr", "funding_x_oi",
    # Cross-Exchange Features (38-41)
    "coinbase_premium_norm", "global_oi_change",
    "coinbase_premium_velocity_norm", "oi_divergence_norm",
    # Liquidations (42-45)
    "long_liq_volume", "short_liq_volume", "liq_imbalance", "liq_acceleration",
    # Volatility (46-50)
    "rv_1m", "rv_5m", "rv_15m", "vol_acceleration", "ewma_vol",
    # Chainlink Oracle (51)
    "chainlink_price_norm",
    # Liquidity Walls & Vacuum (52-56)
    "wall_imbalance", "distance_to_bid_wall_norm", "distance_to_ask_wall_norm",
    "spread_expansion_ratio", "vacuum_detected",
    # Support & Resistance (57-59)
    "dist_to_resistance", "dist_to_support", "sr_compression",
    # Fair-Value Engine (60) — multi-exchange mean-reversion signal
    "fv_deviation",
    # Deep Microstructure (61-67)
    "bid_wall_persistence", "ask_wall_persistence", "bid_wall_growth", "ask_wall_growth",
    "queue_depletion_rate", "liquidity_sweep_bullish", "liquidity_sweep_bearish",
    # Advanced Microstructure (68-72)
    "spoof_score", "absorption_ratio", "bid_consume_rate", "ask_consume_rate", "queue_pressure",
    # Regime & Volatility Forecasting (73-77)
    "regime_transition_prob", "regime_entropy", "vol_forecast_1m", "vol_forecast_5m", "vol_forecast_15m",
    "put_call_ratio", "options_skew_25d", "max_pain_distance", "atm_iv_norm",
    "basis_spread", "basis_velocity", "stablecoin_flow", "exchange_netflow",
    # Cross-Asset Correlation (86-93)
    "eth_btc_price_ratio", "sol_btc_price_ratio", "eth_volume_norm", "sol_volume_norm",
    "eth_imbalance", "sol_imbalance", "macro_dxy_norm", "macro_us10y_norm",
    # Multi-Timeframe Context (94-96)
    "mtf_trend_alignment", "mtf_volatility_ratio", "mtf_support_distance",
    # Deep Order Flow (97-100)
    "order_add_cancel_imbalance", "absorption_persistence", "book_replenishment_rate", "cross_exchange_lead_lag",
    # Rolling Volume Profile (101-102)
    "volume_profile_poc_distance", "volume_profile_lvn_distance",
    # Funding Interactions (103-104)
    "funding_oi_interaction", "time_to_funding",
    # Polymarket / Events (105-108)
    "polymarket_relevant_event", "polymarket_probability_change", "polymarket_liquidity", "polymarket_event_shock",
    # V3 directional batch (109-114) — APPEND ONLY; never reorder 0-108 (saved models index by position).
    # Class-A (kline-derived, full history immediately): twap_deviation, exhaustion, volume_profile_value_area_pos.
    # Backfilled trade-derived (data.binance.vision SPOT aggTrades via backfill_trade_features + live recorder):
    #   vpin, cvd_delta_divergence. Plus oi_momentum (from existing OI feed).
    "twap_deviation", "exhaustion", "volume_profile_value_area_pos",
    "vpin", "cvd_delta_divergence", "oi_momentum",
    # Opening Range Breakout (115-116), anchored 00:00 UTC, 60-min range. Class-A (kline-derived).
    "orb_position", "orb_breakout",
    # Microstructure trade-flow transforms (117-123), research-backed (OFI/price-impact family).
    # All DERIVED from the already-consistent cvd_1m series + klines + OI — no new live
    # recording, ride the same backfill/live path as CVD (train/serve consistent).
    "delta_ratio", "delta_acceleration", "flow_efficiency", "cvd_slope_divergence",
    "rv_upside", "rv_downside", "price_oi_interaction",
    # Large-trade flow (124-125) — backfillable from trades (NO L2 needed); recorded live by
    # order_flow with the SAME EWMA threshold as the backfill.
    "large_trade_delta", "large_trade_imbalance",
    # Trend-persistence batch (126-129) — added 2026-06-11. Live evidence showed the model
    # MEAN-REVERTING into slow grind-trends (15 straight DOWN leans into a +$255 LOW_VOL
    # uptrend, ~33% correct): it inherited a DOWN lean from a bearish training window and
    # couldn't switch UP when the current move trended up. These kline-derived features let
    # the trees SEE "quiet but persistent drift" so the RANGE/low-vol experts can follow a
    # trend instead of always fading it. Full history immediately (no recording wait).
    "trend_efficiency", "signed_streak", "momentum_fast_slow", "return_acceleration",
    # Regime / term-structure / session batch (130-135) — added 2026-06-13. APPEND ONLY.
    # ALL kline/timestamp-derived → computed identically at train AND serve by the SINGLE
    # builder build_features_from_klines (no recorder, no overlay, no side table), so they
    # have PERFECT train/serve parity and NONE of the constant-in-training problem that
    # caps the L2/options slots. variance_ratio = Lo–MacKinlay trend-vs-meanrevert;
    # rv_term_structure = short-vs-long realized-vol; session_* / is_weekend = UTC regime.
    "variance_ratio", "rv_term_structure",
    "session_asia", "session_eu", "session_us", "is_weekend",
]
NUM_FEATURES = len(FEATURE_NAMES)

def calculate_schema_hash(names: list[str]) -> str:
    import hashlib
    schema_str = ",".join(names)
    return hashlib.sha256(schema_str.encode('utf-8')).hexdigest()[:12]

def get_feature_schema() -> dict:
    return {
        "count": len(FEATURE_NAMES),
        "names": FEATURE_NAMES,
        "schema_hash": calculate_schema_hash(FEATURE_NAMES),
    }

# ── Feature retirement (safe, reversible prune) ──────────────────────────────
# Operator-managed list of features whose columns are zeroed in build_features.
# Zeroing (rather than dropping) keeps the matrix dimension stable so saved models
# stay loadable, while removing the feature's influence (a constant column can't be
# split on). Populated by analytics.apply_feature_retirement() once enough SHAP
# evidence exists; empty by default so it is a no-op until deliberately triggered.
# retired_features is no longer JSON-based. It uses the `feature_retirement_events` table.
def load_retired_feature_indices() -> list:
    try:
        import duckdb
        import os
        try:
            from database import DB_PATH as db_path
        except Exception:
            data_dir = os.environ.get("BTC_DATA_DIR") or os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "data"
            )
            db_path = os.environ.get("BTC_DB_PATH") or os.path.join(data_dir, "analytics.duckdb")
        if not os.path.exists(db_path):
            return []
        with duckdb.connect(db_path, read_only=True) as conn:
            try:
                df = conn.execute("SELECT feature FROM feature_retirement_events WHERE status = 'retired'").df()
                names = df["feature"].tolist()
                return [FEATURE_NAMES.index(n) for n in names if n in FEATURE_NAMES]
            except Exception:
                return []
    except Exception as e:
        # Importing features.py should never fail or spam the terminal because a
        # separate live backend process holds DuckDB's Windows file lock. In that
        # case feature retirement simply becomes inactive for this isolated process.
        if os.environ.get("BTC_VERBOSE_FEATURE_RETIREMENT") == "1":
            print(f"Failed to load retired features: {e}")
    return []


RETIRED_FEATURE_IDX = load_retired_feature_indices()
LOOKBACK = 60


def clamp(val: float, lo: float, hi: float) -> float:
    if val is None or np.isnan(val):
        return 0.0
    return max(lo, min(hi, float(val)))


def compute_adaptive_threshold_series(
    closes: np.ndarray,
    atr_arr: np.ndarray,
    smoothing_span: int = 100,
) -> np.ndarray:
    """Return a causal volatility threshold for every decision candle.

    Each value uses only ATR and price observations available at or before that
    candle. Applying one threshold derived from the final rows to the entire
    history makes old labels depend on the newest volatility regime.
    """
    closes = np.asarray(closes, dtype=np.float64).reshape(-1)
    atr_arr = np.asarray(atr_arr, dtype=np.float64).reshape(-1)
    cost_floor = float(os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008"))
    cost_floor = min(max(cost_floor, 0.0), 0.003)
    thresholds = np.full(len(closes), cost_floor, dtype=np.float64)
    if len(closes) == 0 or len(atr_arr) == 0:
        return thresholds

    alpha = 2.0 / (max(2, int(smoothing_span)) + 1.0)
    atr_ewma = None
    price_ewma = None
    valid_count = 0
    for index in range(min(len(closes), len(atr_arr))):
        price = float(closes[index])
        atr_value = float(atr_arr[index])
        if not (np.isfinite(price) and price > 0 and np.isfinite(atr_value) and atr_value >= 0):
            continue
        if atr_ewma is None:
            atr_ewma = atr_value
            price_ewma = price
        else:
            atr_ewma = alpha * atr_value + (1.0 - alpha) * atr_ewma
            price_ewma = alpha * price + (1.0 - alpha) * price_ewma
        valid_count += 1
        if valid_count >= 10 and price_ewma and price_ewma > 0:
            thresholds[index] = np.clip(0.15 * atr_ewma / price_ewma, cost_floor, 0.003)

    return thresholds


def compute_adaptive_threshold(closes: np.ndarray, atr_arr: np.ndarray) -> float:
    """Return the latest causal threshold for compatibility with diagnostics."""
    thresholds = compute_adaptive_threshold_series(closes, atr_arr)
    if len(thresholds):
        return float(thresholds[-1])
    return float(os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008"))


def _ffill_zeros(arr: np.ndarray) -> np.ndarray:
    """Forward-fill ZERO gaps in a per-bar price series.

    Cross-asset feeds (ETH/SOL) record 0 during outages/startup; diffing across a
    0 bar turns one missing sample into two false full-scale moves (e.g. the
    lead-lag feature saturates its clip at ±1.0 on pure feed noise). Leading zeros
    remain zero; only already-observed values are carried forward, and an all-zero series is returned as-is
    (constant → zero variance → harmless)."""
    out = np.asarray(arr, dtype=np.float64).copy()
    nz = np.flatnonzero(out != 0.0)
    if nz.size == 0 or nz.size == out.size:
        return out
    pos = np.where(out != 0.0, np.arange(out.size), 0)
    np.maximum.accumulate(pos, out=pos)
    return out[pos]


def build_features_from_klines(
    klines: list[dict],
    order_flow_summary: Optional[dict] = None,
    derivatives_data: Optional[dict] = None,
    sentiment_data: Optional[dict] = None,
    signal_history: Optional[dict] = None,
) -> np.ndarray:
    """
    Build a 2D feature matrix [timesteps, NUM_FEATURES] from kline history
    plus current market state.

    Returns shape (N-1, NUM_FEATURES) — one row per timestep gap (return-based).

    `signal_history`: optional dict of per-candle arrays (aligned to `klines`,
    length == len(klines)) produced by LiveSignalHistoryBuffer.get_aligned_series().
    When provided, the live-signal columns (order flow, liquidations, derivatives,
    walls, cross-exchange, chainlink) vary per-bar so the models can learn from
    them. When absent, those columns fall back to broadcasting the current snapshot
    to every row (legacy behaviour — inert during training but correct at inference).
    """
    if len(klines) < 30:
        return np.empty((0, NUM_FEATURES))

    closes = np.array([k["close"] for k in klines], dtype=np.float64)
    highs = np.array([k["high"] for k in klines], dtype=np.float64)
    lows = np.array([k["low"] for k in klines], dtype=np.float64)
    volumes = np.array([k["volume"] for k in klines], dtype=np.float64)
    opens = np.array([k["open"] for k in klines], dtype=np.float64)
    times = np.array([k.get("time", 0) for k in klines], dtype=np.float64)

    # Compute all indicators
    rsi_arr = rsi(closes)
    stoch_rsi_arr = stochastic_rsi(closes)
    _, _, macd_hist = macd(closes)
    _, _, _, bb_pos = bollinger_bands(closes)
    atr_arr = atr(highs, lows, closes)
    # Pass `times` so the VWAP window is a real trailing 24 HOURS. Without it the window
    # is a bar count, which silently becomes five days if this is ever fed 5m bars.
    vwap_arr = vwap(highs, lows, closes, volumes, times=times)
    adx_arr = adx(highs, lows, closes)
    obv_arr = obv(closes, volumes)
    wr_arr = williams_r(highs, lows, closes)
    cci_arr = cci(highs, lows, closes)
    mfi_arr = mfi(highs, lows, closes, volumes)
    ema9_arr = ema(closes, 9)
    ema21_arr = ema(closes, 21)
    sma50_arr = sma(closes, 50)
    vol_sma_arr = sma(volumes, 20)
    roc5_arr = roc(closes, 5)
    roc10_arr = roc(closes, 10)
    ha_trend_arr = heikin_ashi_trend(opens, highs, lows, closes)

    n = len(closes)
    features = np.zeros((n - 1, NUM_FEATURES))

    max_vol_window = 50

    for i in range(1, n):
        row = i - 1

        # 0: price return
        price_ret = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] > 0 else 0
        features[row, 0] = clamp(price_ret * 100, -5, 5) / 5.0

        # 1: volume normalised
        vol_window = volumes[max(0, i - max_vol_window): i + 1]
        max_vol = np.max(vol_window) if len(vol_window) > 0 else 1.0
        features[row, 1] = clamp(volumes[i] / max_vol if max_vol > 0 else 0, 0, 1)

        # 2: RSI (0-1)
        features[row, 2] = clamp(rsi_arr[i] / 100.0 if not np.isnan(rsi_arr[i]) else 0.5, 0, 1)

        # 3: MACD histogram
        mh = macd_hist[i] if not np.isnan(macd_hist[i]) else 0
        features[row, 3] = clamp(mh * 100, -5, 5) / 5.0

        # 4: Bollinger Band position (0-1)
        features[row, 4] = clamp(bb_pos[i] if not np.isnan(bb_pos[i]) else 0.5, 0, 1)

        # 5: ATR normalised
        atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else 0
        features[row, 5] = clamp((atr_val / closes[i] * 100) if closes[i] > 0 else 0, 0, 5) / 5.0

        # 6: VWAP deviation
        vwap_dev = (closes[i] - vwap_arr[i]) / closes[i] if closes[i] > 0 else 0
        features[row, 6] = clamp(vwap_dev * 100, -3, 3) / 3.0

        # --- Advanced Indicators (21-33) ---

        # 21: Stochastic RSI
        features[row, 21] = clamp(stoch_rsi_arr[i] / 100.0 if not np.isnan(stoch_rsi_arr[i]) else 0.5, 0, 1)

        # 22: ADX normalised
        features[row, 22] = clamp(adx_arr[i] / 100.0 if not np.isnan(adx_arr[i]) else 0.25, 0, 1)

        # 23: OBV change
        if i >= 5:
            obv_chg = (obv_arr[i] - obv_arr[i - 5]) / (abs(obv_arr[i - 5]) + 1e-9)
            features[row, 23] = clamp(obv_chg, -1, 1)

        # 24: Williams %R
        features[row, 24] = clamp((wr_arr[i] + 100) / 100.0 if not np.isnan(wr_arr[i]) else 0.5, 0, 1)

        # 25: CCI
        features[row, 25] = clamp(cci_arr[i] / 300.0 if not np.isnan(cci_arr[i]) else 0, -1, 1)

        # 26: MFI
        features[row, 26] = clamp(mfi_arr[i] / 100.0 if not np.isnan(mfi_arr[i]) else 0.5, 0, 1)

        # 27: Price vs EMA9
        if not np.isnan(ema9_arr[i]) and ema9_arr[i] > 0:
            features[row, 27] = clamp((closes[i] - ema9_arr[i]) / ema9_arr[i] * 100, -2, 2) / 2.0
        
        # 28: Price vs EMA21
        if not np.isnan(ema21_arr[i]) and ema21_arr[i] > 0:
            features[row, 28] = clamp((closes[i] - ema21_arr[i]) / ema21_arr[i] * 100, -3, 3) / 3.0

        # 29: Price vs SMA50
        if not np.isnan(sma50_arr[i]) and sma50_arr[i] > 0:
            features[row, 29] = clamp((closes[i] - sma50_arr[i]) / sma50_arr[i] * 100, -5, 5) / 5.0

        # 30: Volume / MA(20) ratio
        if not np.isnan(vol_sma_arr[i]) and vol_sma_arr[i] > 0:
            features[row, 30] = clamp(volumes[i] / vol_sma_arr[i], 0, 5) / 5.0

        # 31: ROC 5-period
        features[row, 31] = clamp(roc5_arr[i] if not np.isnan(roc5_arr[i]) else 0, -3, 3) / 3.0

        # 32: ROC 10-period
        features[row, 32] = clamp(roc10_arr[i] if not np.isnan(roc10_arr[i]) else 0, -5, 5) / 5.0

        # 33: Heikin-Ashi trend
        features[row, 33] = ha_trend_arr[i]

    # ── Live-signal features (7-20, 38-45) ──────────────────────────────
    # When a per-candle `signal_history` is supplied (aligned to klines), these
    # columns vary per bar so the trees can learn from them. Otherwise we fall back
    # to broadcasting the current snapshot to every row (legacy behaviour).
    of = order_flow_summary or {}
    der = derivatives_data or {}
    sent = sentiment_data or {}
    sh = signal_history or {}

    safe = np.where(closes > 0, closes, 1.0)

    def series(key, snapshot_val):
        arr = sh.get(key)
        if arr is not None and len(arr) == n:
            return np.asarray(arr, dtype=np.float64)
        return np.full(n, float(snapshot_val or 0.0), dtype=np.float64)

    # Snapshot fallbacks (used when no history array is present for a key)
    snap_funding = 0.0
    bybit_fr = der.get("bybit_funding_rate")
    if bybit_fr is not None:
        snap_funding = bybit_fr.get("rate", 0.0) if isinstance(bybit_fr, dict) else float(bybit_fr)
    elif der.get("funding_rate"):
        fr = der["funding_rate"]
        snap_funding = fr.get("rate", 0.0) if isinstance(fr, dict) else float(fr)

    snap_oi_ch = 0.0
    oi_hist = der.get("oi_history") or []
    if (
        len(oi_hist) >= 2
        and isinstance(oi_hist[0], dict)
        and isinstance(oi_hist[-1], dict)
        and oi_hist[0].get("sum_oi_value", 0) > 0
    ):
        snap_oi_ch = (
            (oi_hist[-1].get("sum_oi_value", 0.0) - oi_hist[0].get("sum_oi_value", 0.0))
            / oi_hist[0].get("sum_oi_value", 1.0)
            * 100
        )

    snap_ls = 1.0
    long_short = der.get("long_short_ratio") or []
    if long_short and isinstance(long_short[0], dict):
        snap_ls = long_short[0].get("ratio", 1.0)

    fg = sent.get("fear_greed")
    snap_fg = fg.get("value", 50) if isinstance(fg, dict) else 50

    liqs = der.get("liquidations", {})
    liqs = liqs if isinstance(liqs, dict) else {}
    walls = of.get("liquidity_walls", {}) or {}
    vac = of.get("liquidity_vacuum", {}) or {}

    # Per-bar raw arrays (history if available, else constant snapshot)
    cvd_change_raw = series("cvd_change", of.get("cvd_change", 0))
    cvd_1m_raw     = series("cvd_1m", of.get("cvd_1m", 0))
    cvd_5m_raw     = series("cvd_5m", of.get("cvd_5m", 0))
    imb_raw        = series("imbalance", of.get("imbalance", 0))
    obi5_raw       = series("obi_5", of.get("obi_5", 0))
    obi10_raw      = series("obi_10", of.get("obi_10", 0))
    obi20_raw      = series("obi_20", of.get("obi_20", 0))
    ti_raw         = series("trade_intensity", of.get("trade_intensity", 50))
    spread_raw     = series("spread_bps", of.get("spread_bps", 2.5))
    funding_raw    = series("funding_rate", snap_funding)
    oi_change_raw  = series("oi_change", snap_oi_ch)
    ls_raw         = series("ls_ratio", snap_ls)
    fg_raw         = series("fear_greed", snap_fg)
    cb_prem_raw    = series("coinbase_premium", der.get("coinbase_premium", 0.0))
    goi_raw        = series("global_oi_change", der.get("global_oi_change", 0.0))
    cbvel_raw      = series("coinbase_premium_velocity", der.get("coinbase_premium_velocity", 0.0))
    oidiv_raw      = series("oi_divergence", der.get("oi_divergence", 0.0))
    longliq_raw    = series("long_liq_vol", liqs.get("long_vol", 0.0))
    shortliq_raw   = series("short_liq_vol", liqs.get("short_vol", 0.0))
    liqimb_raw     = series("liq_imbalance", liqs.get("imbalance", 0.0))
    
    bwall_pers_raw = series("bid_wall_persistence", walls.get("bid_wall_persistence", 0.0))
    awall_pers_raw = series("ask_wall_persistence", walls.get("ask_wall_persistence", 0.0))
    bwall_gro_raw  = series("bid_wall_growth", walls.get("bid_wall_growth", 1.0))
    awall_gro_raw  = series("ask_wall_growth", walls.get("ask_wall_growth", 1.0))
    qdep_raw       = series("queue_depletion_rate", of.get("queue_depletion_rate", 1.0))
    swp_bull_raw   = series("liquidity_sweep_bullish", of.get("liquidity_sweep_bullish", 0.0))
    swp_bear_raw   = series("liquidity_sweep_bearish", of.get("liquidity_sweep_bearish", 0.0))

    # Normalize (vectorized); drop first bar so rows align to closes[1:]
    features[:, 7]  = np.clip(cvd_change_raw[1:] * 0.001, -1, 1)
    features[:, 8]  = np.clip(cvd_1m_raw[1:] * 0.001, -1, 1)
    features[:, 9]  = np.clip(cvd_5m_raw[1:] * 0.001, -1, 1)
    features[:, 10] = np.clip(imb_raw[1:], -1, 1)
    features[:, 11] = np.clip(obi5_raw[1:], -1, 1)
    features[:, 12] = np.clip(obi10_raw[1:], -1, 1)
    features[:, 13] = np.clip(obi20_raw[1:], -1, 1)
    features[:, 14] = np.clip(ti_raw[1:] / 100.0, 0, 1)
    features[:, 15] = np.clip(spread_raw[1:] / 5.0, 0, 1)
    features[:, 16] = np.clip(funding_raw[1:] * 10000, -5, 5) / 5.0
    # 17: funding_velocity (was a dead 0.0 stub) — per-bar Δ funding rate. Rising funding
    # into a move = crowded positioning = reversal risk. Scaled so typical Δ spans the band.
    _fund_vel = np.zeros(n)
    _fund_vel[1:] = funding_raw[1:] - funding_raw[:-1]
    features[:, 17] = np.clip(_fund_vel[1:] * 100000.0, -1.0, 1.0)
    features[:, 18] = np.clip(oi_change_raw[1:], -5, 5) / 5.0
    features[:, 19] = np.clip((ls_raw[1:] - 1) * 0.5, -1, 1)
    features[:, 20] = np.clip(fg_raw[1:] / 100.0, 0, 1)

    # Cross-exchange (38-41)
    prem_pct = cb_prem_raw / safe * 100.0
    features[:, 38] = np.clip(prem_pct[1:] / 0.1, -1, 1)
    features[:, 39] = np.clip(goi_raw[1:], -5, 5) / 5.0
    features[:, 40] = np.clip(cbvel_raw[1:] / 5.0, -1, 1)
    features[:, 41] = np.clip(oidiv_raw[1:], -5, 5) / 5.0

    # Liquidations (42-45)
    features[:, 42] = np.clip(longliq_raw[1:] / 1_000_000, 0, 5) / 5.0
    features[:, 43] = np.clip(shortliq_raw[1:] / 1_000_000, 0, 5) / 5.0
    features[:, 44] = np.clip(liqimb_raw[1:] / 1_000_000, -5, 5) / 5.0
    # 45: liq_acceleration (was a dead 0.0 stub) — 2nd difference of liq imbalance; a
    # liquidation cascade accelerating in one direction is a strong short-horizon driver.
    _liq_acc = np.zeros(n)
    _liq_acc[2:] = liqimb_raw[2:] - 2.0 * liqimb_raw[1:-1] + liqimb_raw[:-2]
    features[:, 45] = np.clip(_liq_acc[1:] / 1_000_000, -1.0, 1.0)

    # Volatility (46-50) — PER-BAR rolling values so the models can actually learn
    # from these columns. Previously a single snapshot value was broadcast to every
    # training row (zero variance => trees could never split on it = dead features).
    returns_full = np.zeros(n)
    returns_full[1:] = np.diff(closes) / np.where(closes[:-1] > 0, closes[:-1], 1.0)

    def _rolling_std(x, w):
        m = len(x)
        out = np.zeros(m)
        cs = np.cumsum(x)
        cs2 = np.cumsum(x * x)
        for k in range(m):
            lo = max(0, k - w + 1)
            cnt = k - lo + 1
            s = cs[k] - (cs[lo - 1] if lo > 0 else 0.0)
            s2 = cs2[k] - (cs2[lo - 1] if lo > 0 else 0.0)
            var = max(0.0, s2 / cnt - (s / cnt) ** 2)
            out[k] = np.sqrt(var)
        return out

    rv2 = _rolling_std(returns_full, 2) * 100.0
    rv5 = _rolling_std(returns_full, 5) * 100.0
    rv15 = _rolling_std(returns_full, 15) * 100.0

    # EWMA (RiskMetrics) volatility, computed recursively per-bar.
    ewma = np.zeros(n)
    lam = 0.94
    for k in range(1, n):
        ewma[k] = lam * ewma[k - 1] + (1 - lam) * (returns_full[k] ** 2)
    ewma_vol = np.sqrt(ewma)
    vol_accel = np.zeros(n)
    vol_accel[1:] = ewma_vol[1:] - ewma_vol[:-1]

    # Rows are aligned to closes[1:] (feature row r corresponds to bar r+1).
    features[:, 46] = np.clip(rv2[1:], 0, 1)
    features[:, 47] = np.clip(rv5[1:], 0, 1)
    features[:, 48] = np.clip(rv15[1:], 0, 1)
    features[:, 49] = np.clip(vol_accel[1:] * 1000, -1, 1)
    features[:, 50] = np.clip(ewma_vol[1:] * 100, 0, 5) / 5.0

    # Chainlink Oracle (51) — per-bar normed difference vs Binance price.
    cl_raw = series("chainlink_price", der.get("chainlink_price", 0.0) or 0.0)
    cl_diff = np.where(cl_raw > 0, (cl_raw - closes) / safe * 100.0, 0.0)
    features[:, 51] = np.clip(cl_diff[1:] / 0.1, -1.0, 1.0)

    # Liquidity Walls & Vacuum (52-56) — per-bar.
    wimb_raw = series("wall_imbalance", walls.get("wall_imbalance", 0.0))
    dbid_raw = series("dist_bid_wall", walls.get("distance_to_bid_wall", 0.0))
    dask_raw = series("dist_ask_wall", walls.get("distance_to_ask_wall", 0.0))
    sexp_raw = series("spread_expansion", vac.get("spread_expansion_ratio", 1.0))
    vacf_raw = series("vacuum_detected", 1.0 if vac.get("vacuum_detected", False) else 0.0)
    features[:, 52] = np.clip(wimb_raw[1:], -1.0, 1.0)
    features[:, 53] = np.clip(dbid_raw[1:] / safe[1:] * 100.0, 0, 1)
    features[:, 54] = np.clip(dask_raw[1:] / safe[1:] * 100.0, 0, 1)
    features[:, 55] = np.clip(sexp_raw[1:] / 5.0, 0, 1)
    features[:, 56] = vacf_raw[1:]

    # Support & Resistance (57-59) — PER-BAR rolling extremes (vectorized) so the
    # distance-to-level columns vary across the training set instead of being a
    # single broadcast snapshot. Nearest resistance/support = rolling max-high /
    # min-low over a 200-bar window.
    sr_window = 200
    roll_res = np.array(highs, dtype=np.float64)
    roll_sup = np.array(lows, dtype=np.float64)
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        if n >= sr_window:
            roll_res[sr_window - 1:] = sliding_window_view(highs, sr_window).max(axis=1)
            roll_sup[sr_window - 1:] = sliding_window_view(lows, sr_window).min(axis=1)
            # warm-up region: expanding extremes
            for k in range(min(sr_window - 1, n)):
                roll_res[k] = highs[:k + 1].max()
                roll_sup[k] = lows[:k + 1].min()
    except Exception:
        pass

    safe_close = np.where(closes > 0, closes, 1.0)
    dist_res = (roll_res - closes) / safe_close
    dist_sup = (closes - roll_sup) / safe_close
    # Compression: how tightly price is sandwiched between S and R. 1.0 = very tight
    # (small S–R band), 0.0 = wide band (>=5%). Scaled so it varies instead of
    # saturating to a constant.
    range_pct = dist_res + dist_sup
    sr_comp = 1.0 - np.clip(range_pct / 0.05, 0.0, 1.0)
    features[:, 57] = np.clip(dist_res[1:] * 100, 0, 5) / 5.0
    features[:, 58] = np.clip(dist_sup[1:] * 100, 0, 5) / 5.0
    features[:, 59] = sr_comp[1:]

    # Fair-Value Engine (60) — synthetic multi-exchange "true price" = weighted blend
    # of Binance spot, Coinbase spot (Binance + premium) and the Chainlink oracle.
    # The deviation of Binance from fair value is a mean-reversion signal: trading
    # below fair value tends to revert upward, and vice-versa. Uses the per-bar
    # premium / chainlink arrays built above so it is learnable (not a snapshot).
    P_b = closes
    P_c = closes + cb_prem_raw
    has_cl = cl_raw > 0
    wb = np.where(has_cl, 0.40, 0.55)
    wc = np.where(has_cl, 0.35, 0.45)
    wl = np.where(has_cl, 0.25, 0.0)
    fair_value = wb * P_b + wc * P_c + wl * np.where(has_cl, cl_raw, 0.0)
    # Guard the denominator so numpy doesn't eagerly divide-by-zero before np.where masks it.
    _fv_safe = np.where(fair_value > 0, fair_value, 1.0)
    fv_dev_pct = np.where(fair_value > 0, (P_b - fair_value) / _fv_safe * 100.0, 0.0)
    features[:, 60] = np.clip(fv_dev_pct[1:] / 0.1, -1.0, 1.0)
    
    # Deep Microstructure (61-67) — per-bar
    features[:, 61] = np.clip(bwall_pers_raw[1:] / 600.0, 0, 1.0)  # scale by 10 mins
    features[:, 62] = np.clip(awall_pers_raw[1:] / 600.0, 0, 1.0)
    features[:, 63] = np.clip(bwall_gro_raw[1:] / 5.0, 0, 1.0)     # cap at 5x growth
    features[:, 64] = np.clip(awall_gro_raw[1:] / 5.0, 0, 1.0)
    features[:, 65] = np.clip(qdep_raw[1:] / 5.0, 0, 1.0)          # normalized queue depletion
    features[:, 66] = swp_bull_raw[1:]
    features[:, 67] = swp_bear_raw[1:]

    # Advanced Microstructure (68-72) — from order_flow summary
    spoof_raw       = series("spoof_score", of.get("spoof_score", 0.0))
    absorb_raw      = series("absorption_ratio", of.get("absorption_ratio", 0.0))
    bidconsume_raw  = series("bid_consume_rate", of.get("bid_consume_rate", 0.0))
    askconsume_raw  = series("ask_consume_rate", of.get("ask_consume_rate", 0.0))
    qpressure_raw   = series("queue_pressure", of.get("queue_pressure", 0.0))
    features[:, 68] = np.clip(spoof_raw[1:], 0, 1.0)
    features[:, 69] = np.clip(absorb_raw[1:] / 5.0, 0, 1.0)
    features[:, 70] = np.clip(bidconsume_raw[1:] / 10.0, 0, 1.0)
    features[:, 71] = np.clip(askconsume_raw[1:] / 10.0, 0, 1.0)
    features[:, 72] = np.clip(qpressure_raw[1:], -1.0, 1.0)

    # Regime & Volatility Forecasting (73-77) — from regime_data in derivatives
    regime_data = der.get("regime_data", {}) or {}
    rtrans_prob = float(regime_data.get("transition_probability", 0.0))
    rentropy    = float(regime_data.get("regime_entropy", 0.0))
    vol_fc      = regime_data.get("vol_forecast", {}) or {}
    rtrans_raw = series("regime_transition_prob", rtrans_prob)
    rentropy_raw = series("regime_entropy", rentropy)
    vol1_raw = series("vol_forecast_1m", vol_fc.get("vol_forecast_1m", 0.0))
    vol5_raw = series("vol_forecast_5m", vol_fc.get("vol_forecast_5m", 0.0))
    vol15_raw = series("vol_forecast_15m", vol_fc.get("vol_forecast_15m", 0.0))
    features[:, 73] = np.clip(rtrans_raw[1:], 0.0, 1.0)
    features[:, 74] = np.clip(rentropy_raw[1:] / 3.0, 0.0, 1.0)
    features[:, 75] = np.clip(vol1_raw[1:], 0.0, 1.0)
    features[:, 76] = np.clip(vol5_raw[1:], 0.0, 1.0)
    features[:, 77] = np.clip(vol15_raw[1:], 0.0, 1.0)

    # Institutional Alpha (78-85) — from institutional_data in derivatives
    inst = der.get("institutional", {}) or {}
    options  = inst.get("options", {}) or {}
    basis    = inst.get("basis", {}) or {}
    stable   = inst.get("stablecoin", {}) or {}
    exflow   = inst.get("exchange_flow", {}) or {}

    pcr_raw = series("put_call_ratio", options.get("put_call_ratio", 1.0))
    skew_raw = series("options_skew_25d", options.get("skew_25d", 0.0))
    max_pain_raw = series("max_pain", options.get("max_pain", 0.0))
    atm_iv_raw = series("atm_iv", options.get("atm_iv", 0.0))
    basis_spread_raw = series("basis_spread", basis.get("basis_spread", 0.0))
    basis_velocity_raw = series("basis_velocity", basis.get("basis_velocity", 0.0))
    stable_flow_raw = series("stablecoin_flow", stable.get("stablecoin_flow", 0.0))
    exflow_raw = series("exchange_netflow", exflow.get("exchange_netflow", 0.0))

    features[:, 78] = np.clip(pcr_raw[1:] - 1.0, -1.0, 1.0)  # 1.0=neutral
    features[:, 79] = np.clip(skew_raw[1:] * 10, -1.0, 1.0)
    # Max pain distance: (max_pain - current_price) / current_price, normalized
    mp_dist = np.where(max_pain_raw > 0, (max_pain_raw - closes) / safe, 0.0)
    features[:, 80] = np.clip(mp_dist[1:] * 10, -1.0, 1.0)
    features[:, 81] = np.clip(atm_iv_raw[1:] * 2, 0, 1.0)  # typical ATM IV 0.3-0.8
    features[:, 82] = np.clip(basis_spread_raw[1:] * 1000, -1.0, 1.0)
    features[:, 83] = np.clip(basis_velocity_raw[1:] * 100, -1.0, 1.0)
    features[:, 84] = np.clip(stable_flow_raw[1:], -1.0, 1.0)
    features[:, 85] = np.clip(exflow_raw[1:], -1.0, 1.0)

    # Cross-Asset Correlation (86-93)
    # PRICE series get zero-gap forward-fill (see _ffill_zeros): a 0 outage bar
    # floors the ratio features and makes the lead-lag diff (feature 100) emit two
    # false full-scale spikes. Applied inside this shared build path, so training
    # and serving stay consistent. Volumes/imbalances keep their honest 0
    # (real "no data" semantics, bounded effect).
    eth_price_raw = _ffill_zeros(series("eth_price", 0.0))
    sol_price_raw = _ffill_zeros(series("sol_price", 0.0))
    eth_vol_raw = series("eth_volume", 0.0)
    sol_vol_raw = series("sol_volume", 0.0)
    eth_imb_raw = series("eth_imbalance", 0.0)
    sol_imb_raw = series("sol_imbalance", 0.0)
    macro_dxy_raw = series("macro_dxy", 104.5)
    macro_us10y_raw = series("macro_us10y", 4.25)

    features[:, 86] = np.clip((eth_price_raw[1:] / safe[1:]) / 10.0, 0.0, 1.0)
    features[:, 87] = np.clip((sol_price_raw[1:] / safe[1:]) * 10.0, 0.0, 1.0)
    features[:, 88] = np.clip(eth_vol_raw[1:] / 5000.0, 0.0, 1.0)
    features[:, 89] = np.clip(sol_vol_raw[1:] / 50000.0, 0.0, 1.0)
    features[:, 90] = np.clip(eth_imb_raw[1:], -1.0, 1.0)
    features[:, 91] = np.clip(sol_imb_raw[1:], -1.0, 1.0)
    features[:, 92] = np.clip((macro_dxy_raw[1:] - 100) / 20.0, 0.0, 1.0)
    features[:, 93] = np.clip(macro_us10y_raw[1:] / 10.0, 0.0, 1.0)

    # Multi-Timeframe Context (94-96)
    ret_1m = closes[1:] - closes[:-1]
    ret_5m = closes[1:] - closes[np.maximum(0, np.arange(1, n) - 5)]
    ret_15m = closes[1:] - closes[np.maximum(0, np.arange(1, n) - 15)]
    trend_align = (np.sign(ret_1m) + np.sign(ret_5m) + np.sign(ret_15m)) / 3.0
    features[:, 94] = trend_align
    features[:, 95] = np.clip(rv2[1:] / (rv15[1:] + 1e-9), 0.0, 5.0) / 5.0
    features[:, 96] = np.clip(dist_sup[1:] * 100, 0, 5) / 5.0

    # Deep Order Flow (97-100)
    bids_add = series("bids_added", of.get("bids_added", 0.0))
    asks_add = series("asks_added", of.get("asks_added", 0.0))
    bids_canc = series("bids_canceled", of.get("bids_canceled", 0.0))
    asks_canc = series("asks_canceled", of.get("asks_canceled", 0.0))
    add_canc_imb = ((bids_add + asks_add) - (bids_canc + asks_canc)) / (bids_add + asks_add + bids_canc + asks_canc + 1e-9)
    features[:, 97] = np.clip(add_canc_imb[1:], -1.0, 1.0)
    absorb_pers = series("absorption_ratio", of.get("absorption_ratio", 0.0))
    features[:, 98] = np.clip(absorb_pers[1:] / 5.0, 0, 1.0)
    replenish = series("book_replenishment_rate", of.get("book_replenishment_rate", 1.0))
    features[:, 99] = np.clip(replenish[1:] / 5.0, 0, 1.0)
    # This subtracted an ETH DOLLAR change from a BTC DOLLAR change and divided by the BTC
    # price. ETH and BTC trade two orders of magnitude apart, so the difference was dominated
    # by BTC's larger nominal moves and the result was not a spread in any unit. It was also
    # SIMULTANEOUS, so it could not measure lead-lag at all.
    #
    # Now: ETH's LAGGED log return minus BTC's current log return. Same units (log return),
    # and the ETH term is genuinely from the previous bar, so the feature can express "ETH
    # moved first". Whether that has value is an empirical question this makes askable.
    _eth_safe = np.where(eth_price_raw > 0, eth_price_raw, np.nan)
    _eth_logret = np.zeros_like(safe)
    with np.errstate(invalid="ignore", divide="ignore"):
        _eth_logret[1:] = np.log(_eth_safe[1:] / _eth_safe[:-1])
    _eth_logret = np.nan_to_num(_eth_logret, nan=0.0, posinf=0.0, neginf=0.0)
    _btc_logret = np.zeros_like(safe)
    with np.errstate(invalid="ignore", divide="ignore"):
        _btc_logret[1:] = np.log(safe[1:] / safe[:-1])
    _btc_logret = np.nan_to_num(_btc_logret, nan=0.0, posinf=0.0, neginf=0.0)
    _eth_lagged = np.zeros_like(_eth_logret)
    _eth_lagged[1:] = _eth_logret[:-1]          # ETH from the PREVIOUS bar
    features[:, 100] = np.clip((_eth_lagged[1:] - _btc_logret[1:]) * 100.0, -1.0, 1.0)

    # Rolling Volume Profile (101-102) — REAL TPO/market-profile (was VWAP proxy / 0.0 stub).
    _poc_dist, _lvn_dist, _va_pos = rolling_volume_profile(closes, volumes)
    features[:, 101] = np.clip(_poc_dist[1:] * 100.0, -2.0, 2.0) / 2.0   # signed dist to POC
    features[:, 102] = np.clip(_lvn_dist[1:] * 100.0, -2.0, 2.0) / 2.0   # signed dist to LVN

    # Funding Interactions (103-104)
    features[:, 103] = features[:, 16] * features[:, 18] # funding * oi_change
    # 104: time_to_funding (was 0.0 stub) — fraction of the 8h funding cycle REMAINING until
    # the next settlement (00/08/16 UTC). Cyclical via cos so it's smooth across the boundary.
    _t_s = np.where(times > 1e11, times / 1000.0, times)  # normalize ms→s if needed
    _cycle = 8 * 3600.0
    _into = np.mod(_t_s, _cycle)
    _ttf = 1.0 - (_into / _cycle)                          # 1.0 just-settled → 0.0 about-to-settle
    # A SINGLE cosine is not a cyclical encoding - it is symmetric, so 25% and 75% through
    # the funding cycle both map to 0 and the model cannot tell "just settled" from "about to
    # settle". Slot 104 keeps a monotone fraction-remaining (unambiguous, and the quantity the
    # name promises); the sin/cos pair belongs in two slots and is a future append.
    features[:, 104] = np.clip(_ttf[1:], 0.0, 1.0)

    # Polymarket / Events (105-108) (Stubbed as requested)
    features[:, 105] = 0.0
    features[:, 106] = 0.0
    features[:, 107] = 0.0
    features[:, 108] = 0.0

    # ── V3 directional batch (109-114) ──────────────────────────────────────
    import trade_features as _tf  # shared keystone (same fns as backfill + live recorder)
    # 109: TWAP deviation — price vs its rolling time-weighted mean (mean-reversion lean).
    _tw = sma(closes, 20)
    _twap_dev = np.where((~np.isnan(_tw)) & (closes > 0),
                         (closes - np.nan_to_num(_tw)) / safe, 0.0)
    features[:, 109] = np.clip(_twap_dev[1:] * 100.0, -3.0, 3.0) / 3.0
    # 110: exhaustion — a continued move on FADING range+volume → fade (−sign of the move).
    _k = 5
    _tr_abs = np.zeros(n); _tr_abs[1:] = np.abs(closes[1:] - closes[:-1])
    _exh = np.zeros(n)
    if n > _k:
        _ret_k = np.zeros(n); _ret_k[_k:] = closes[_k:] - closes[:-_k]
        _tr_slope = np.zeros(n); _tr_slope[_k:] = _tr_abs[_k:] - _tr_abs[:-_k]
        _vol_slope = np.zeros(n); _vol_slope[_k:] = volumes[_k:] - volumes[:-_k]
        _fade = (_tr_slope < 0) & (_vol_slope < 0) & (np.abs(_ret_k) > 0)
        _exh = np.where(_fade, -np.sign(_ret_k), 0.0)
    features[:, 110] = _exh[1:]
    # 111: volume-profile value-area position (where price sits in the rolling range).
    features[:, 111] = np.clip(_va_pos[1:], 0.0, 1.0)
    # 112: VPIN (order-flow toxicity) — backfilled from SPOT aggTrades + recorded live.
    # 2026-06-28: snapshot was hardcoded 0.0, so a warm live vpin never reached the model. Now falls
    # back to of.get("vpin") (train path is unaffected: overlay_backfill overwrites this from parquet).
    _vpin_raw = series("vpin", of.get("vpin", 0.0))
    features[:, 112] = np.clip(_vpin_raw[1:], 0.0, 1.0)
    # 113: CVD/price divergence — SAME shared fn as the backfill (causal, no look-ahead).
    _div = _tf.cvd_divergence(cvd_1m_raw, closes, lookback=20)
    features[:, 113] = _div[1:]
    # 114: OI momentum — Δ of OI change (acceleration of open interest).
    _oi_mom = np.zeros(n)
    _oi_mom[1:] = oi_change_raw[1:] - oi_change_raw[:-1]
    features[:, 114] = np.clip(_oi_mom[1:], -5.0, 5.0) / 5.0
    # 115-116: Opening Range Breakout (00:00 UTC, 60-min range). Kline-derived; uses the
    # same `times` array as time_to_funding (normalized to seconds).
    _orb_pos, _orb_brk = opening_range_breakout(_t_s, highs, lows, closes)
    features[:, 115] = _orb_pos[1:]
    features[:, 116] = _orb_brk[1:]

    # ── Microstructure trade-flow transforms (117-123) ───────────────────────
    # Research-backed (OFI / signed-price-impact family). All derived from the already
    # train/serve-consistent cvd_1m series + klines + OI, so they need no new recording and
    # ride the same backfill path as CVD. Per-bar return computed once here.
    _ret = np.zeros(n)
    _ret[1:] = (closes[1:] - closes[:-1]) / np.where(closes[:-1] > 0, closes[:-1], 1.0)
    _safe_vol = np.where(volumes > 1.0, volumes, 1.0)
    # 117: delta_ratio — normalized per-bar order-flow imbalance (signed vol / total vol).
    features[:, 117] = np.clip((cvd_1m_raw / _safe_vol)[1:], -1.0, 1.0)
    # 118: delta_acceleration — change in per-bar delta (flow speeding up / fading).
    _d_acc = np.zeros(n); _d_acc[1:] = cvd_1m_raw[1:] - cvd_1m_raw[:-1]
    features[:, 118] = np.clip(_d_acc[1:] * 0.001, -1.0, 1.0)
    # 119: flow_efficiency / signed price impact — return per unit of aggressive flow.
    _denom = np.where(np.abs(cvd_1m_raw) > 1e-6, cvd_1m_raw, np.nan)
    _flow_eff = np.nan_to_num(_ret / _denom, nan=0.0, posinf=0.0, neginf=0.0)
    features[:, 119] = np.clip(_flow_eff[1:] * 1e5, -1.0, 1.0)
    # 120: continuous CVD-slope divergence (research's better, non-binary form) —
    # normalized price slope minus normalized cumulative-CVD slope over a window.
    _w = 20
    _cvd_cum = np.cumsum(cvd_1m_raw)
    _pslope = np.zeros(n); _pslope[_w:] = (closes[_w:] - closes[:-_w]) / np.where(closes[:-_w] > 0, closes[:-_w], 1.0)
    _cslope = np.zeros(n); _cslope[_w:] = _cvd_cum[_w:] - _cvd_cum[:-_w]
    _cvd_scale = _rolling_std(_cvd_cum, 100)
    _cn = np.where(_cvd_scale > 1e-9, _cvd_scale, 1.0)
    _div_cont = _pslope * 100.0 - (_cslope / _cn)
    features[:, 120] = np.clip(_div_cont[1:], -1.0, 1.0)
    # 121-122: upside / downside realized volatility (semivariance) — kline regime signal.
    _rv_up = _rolling_std(np.where(_ret > 0, _ret, 0.0), 15) * 100.0
    _rv_dn = _rolling_std(np.where(_ret < 0, _ret, 0.0), 15) * 100.0
    features[:, 121] = np.clip(_rv_up[1:], 0.0, 1.0)
    features[:, 122] = np.clip(_rv_dn[1:], 0.0, 1.0)
    # 123: price × OI interaction (research #11 — same OI move means different things by
    # price direction; the interaction term lets the trees split on the joint sign).
    features[:, 123] = np.clip(_ret[1:] * 100.0 * oi_change_raw[1:], -1.0, 1.0)
    # 124-125: large-trade flow — backfilled from trades + recorded live (already normalized
    # to [-1,1] by order_flow / trade_features). Big-player aggressive flow & its imbalance.
    # Live serving drops these sparse history arrays so the current order-flow snapshot
    # is used. Passing a literal zero here defeated that overlay and left both selected
    # model features dead-zero even after the 2026-06-28 parity fix.
    _ltd_raw = series("large_trade_delta", of.get("large_trade_delta", 0.0))
    _lti_raw = series("large_trade_imbalance", of.get("large_trade_imbalance", 0.0))
    features[:, 124] = np.clip(_ltd_raw[1:], -1.0, 1.0)
    features[:, 125] = np.clip(_lti_raw[1:], -1.0, 1.0)
    # ── Trend-persistence batch (126-129) — kline-derived, fixes mean-reversion-into-trend ──
    _w = 20
    # 126: trend_efficiency = |net move| / sum(|per-bar moves|) over w bars. 1=clean trend,
    # ~0=chop. SIGNED by net direction so the trees get "strong UP trend" vs "strong DOWN".
    _net = np.zeros(n); _net[_w:] = closes[_w:] - closes[:-_w]
    _absmove = np.abs(np.diff(closes, prepend=closes[0]))
    _sum_abs = np.zeros(n)
    _cs_abs = np.cumsum(_absmove)
    _sum_abs[_w:] = _cs_abs[_w:] - _cs_abs[:-_w]
    _eff = np.where(_sum_abs > 1e-9, _net / np.where(_sum_abs > 1e-9, _sum_abs, 1.0), 0.0)  # [-1,1]
    features[:, 126] = np.clip(_eff[1:], -1.0, 1.0)
    # 127: signed_streak — consecutive same-direction bars (persistence), normalized & signed.
    _sign = np.sign(np.diff(closes, prepend=closes[0]))
    _streak = np.zeros(n)
    for i in range(1, n):
        _streak[i] = (_streak[i-1] + _sign[i]) if (_sign[i] != 0 and _sign[i] == _sign[i-1]) else _sign[i]
    features[:, 127] = np.clip(_streak[1:] / 8.0, -1.0, 1.0)  # saturate ~8-bar streaks
    # 128: momentum_fast_slow — fast (5-bar) minus slow (20-bar) % return; trend strength.
    _r_fast = np.zeros(n); _r_fast[5:] = (closes[5:] - closes[:-5]) / np.where(closes[:-5] > 0, closes[:-5], 1.0)
    _r_slow = np.zeros(n); _r_slow[_w:] = (closes[_w:] - closes[:-_w]) / np.where(closes[:-_w] > 0, closes[:-_w], 1.0)
    features[:, 128] = np.clip((_r_fast[1:] - _r_slow[1:]) * 100.0, -2.0, 2.0) / 2.0
    # 129: return_acceleration — 2nd difference of price (curvature); rising vs fading move.
    _ret1 = np.diff(closes, prepend=closes[0])
    _acc = np.diff(_ret1, prepend=_ret1[0]) / np.where(closes > 0, closes, 1.0)
    features[:, 129] = np.clip(_acc[1:] * 10000.0, -2.0, 2.0) / 2.0

    # ── Regime / term-structure / session batch (130-135) — kline+timestamp only ──────
    # PERFECT train/serve parity: same arrays (closes, returns_full, rv5/rv15, _t_s) used
    # identically here whether building the train matrix or the live row. Append-only.
    # 130: variance_ratio (Lo–MacKinlay) — Var(q-bar return)/(q·Var(1-bar return)) over a
    #      trailing window W. >1 trending/momentum, <1 mean-reverting, ~1 random walk.
    #      Centered at 0 (VR−1) and clipped: lets the trees SEE the trend-vs-chop regime.
    _q, _W = 5, 30
    _cs_r = np.cumsum(returns_full)
    _rq = np.zeros(n); _rq[_q:] = _cs_r[_q:] - _cs_r[:-_q]      # trailing q-bar return
    _var1 = _rolling_std(returns_full, _W) ** 2
    _varq = _rolling_std(_rq, _W) ** 2
    _vr = np.where(_var1 > 1e-18, _varq / (_q * np.where(_var1 > 1e-18, _var1, 1.0)), 1.0)
    features[:, 130] = np.clip(_vr[1:] - 1.0, -1.0, 1.0)
    # 131: rv_term_structure — short-horizon RV (rv5) vs long-horizon RV (rv15), both already
    #      computed above. >0 short-term vol elevated (expansion/stress), <0 calming. Centered.
    _rvts = np.where(rv15 > 1e-9, rv5 / np.where(rv15 > 1e-9, rv15, 1.0) - 1.0, 0.0)
    features[:, 131] = np.clip(_rvts[1:], -1.0, 1.0)
    # 132-134: UTC trading-session flags — BTC vol/flow regime differs by session; the EU+US
    #      overlap (13–16 UTC) is the high-activity window. Overlapping ranges are intentional
    #      (the trees combine them). Sharp, interpretable, parity-perfect.
    _hr = (_t_s.astype(np.int64) // 3600) % 24
    features[:, 132] = ((_hr >= 0) & (_hr < 8)).astype(np.float64)[1:]    # Asia
    features[:, 133] = ((_hr >= 7) & (_hr < 16)).astype(np.float64)[1:]   # Europe
    features[:, 134] = ((_hr >= 13) & (_hr < 21)).astype(np.float64)[1:]  # US
    # 135: is_weekend — Sat/Sun UTC (thinner liquidity, distinct mean-reversion regime).
    #      Epoch day 0 (1970-01-01) was Thursday → (days+3)%7 gives 0=Mon … 5=Sat,6=Sun.
    _dow = (_t_s.astype(np.int64) // 86400 + 3) % 7
    features[:, 135] = (_dow >= 5).astype(np.float64)[1:]

    # Feature Interactions (34-37) — vectorized, per-bar. Placed last so every
    # referenced column is already populated.
    features[:, 34] = features[:, 2] * features[:, 22]    # RSI * ADX
    features[:, 35] = features[:, 1] * features[:, 33]    # Volume * HA Trend
    features[:, 36] = features[:, 11] * features[:, 5]    # OBI_5 * ATR
    features[:, 37] = features[:, 16] * features[:, 18]   # Funding * OI change

    # Apply feature retirement (zero out persistently low-value columns).
    for _ri in RETIRED_FEATURE_IDX:
        if 0 <= _ri < features.shape[1]:
            features[:, _ri] = 0.0

    # Safety net: never emit NaN/inf. Tree models tolerate NaN, but LogisticRegression /
    # SGD do not — an un-sanitized cell causes sklearn's "invalid value encountered in
    # divide" (all-zero predict_proba row) and a degenerate linear vote. Features are
    # already normalized around 0, so 0.0 is the correct neutral fill.
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    return features


def compute_indicator_snapshot(klines: list[dict]) -> dict:
    """
    Compute indicator values for the frontend dashboard display.
    Returns the latest values of all key indicators per timeframe.
    """
    if len(klines) < 30:
        return {}

    closes = np.array([k["close"] for k in klines], dtype=np.float64)
    highs = np.array([k["high"] for k in klines], dtype=np.float64)
    lows = np.array([k["low"] for k in klines], dtype=np.float64)
    volumes = np.array([k["volume"] for k in klines], dtype=np.float64)

    rsi_arr = rsi(closes)
    stoch_rsi_arr = stochastic_rsi(closes)
    _, _, macd_hist_arr = macd(closes)
    bb_upper, bb_mid, bb_lower, bb_pos = bollinger_bands(closes)
    atr_arr = atr(highs, lows, closes)
    mfi_arr_val = mfi(highs, lows, closes, volumes)
    cci_arr_val = cci(highs, lows, closes)
    wr_arr = williams_r(highs, lows, closes)
    adx_arr = adx(highs, lows, closes)
    ema9_arr = ema(closes, 9)
    ema21_arr = ema(closes, 21)
    sma50_arr = sma(closes, 50)
    supertrend_arr = supertrend(highs, lows, closes)

    def safe_last(arr):
        valid = arr[~np.isnan(arr)]
        return float(valid[-1]) if len(valid) > 0 else None

    return {
        "rsi": safe_last(rsi_arr),
        "stoch_rsi": safe_last(stoch_rsi_arr),
        "macd_hist": safe_last(macd_hist_arr),
        "bb_position": safe_last(bb_pos),
        "bb_upper": safe_last(bb_upper),
        "bb_lower": safe_last(bb_lower),
        "bb_mid": safe_last(bb_mid),
        "atr": safe_last(atr_arr),
        "mfi": safe_last(mfi_arr_val),
        "cci": safe_last(cci_arr_val),
        "williams_r": safe_last(wr_arr),
        "adx": safe_last(adx_arr),
        "ema9": safe_last(ema9_arr),
        "ema21": safe_last(ema21_arr),
        "sma50": safe_last(sma50_arr),
        "supertrend": safe_last(supertrend_arr),
        # OB/OS status determination
        "rsi_status": _ob_os_status(safe_last(rsi_arr), 70, 30),
        "stoch_rsi_status": _ob_os_status(safe_last(stoch_rsi_arr), 80, 20),
        "mfi_status": _ob_os_status(safe_last(mfi_arr_val), 80, 20),
        "cci_status": "overbought" if (safe_last(cci_arr_val) or 0) > 100 else ("oversold" if (safe_last(cci_arr_val) or 0) < -100 else "neutral"),
        "williams_r_status": "overbought" if (safe_last(wr_arr) or -50) > -20 else ("oversold" if (safe_last(wr_arr) or -50) < -80 else "neutral"),
        "bb_status": "overbought" if (safe_last(bb_pos) or 0.5) > 0.95 else ("oversold" if (safe_last(bb_pos) or 0.5) < 0.05 else "neutral"),
        "trend_strength": "strong" if (safe_last(adx_arr) or 0) > 25 else ("weak" if (safe_last(adx_arr) or 0) < 15 else "moderate"),
    }


def compute_indicator_series(klines: list[dict], limit: int = 300) -> dict:
    """
    Export backend-calculated indicator series for the chart.

    Keeping RSI and SuperTrend here prevents the frontend from drifting away from
    the exact formulas used by the feature engine.
    """
    if len(klines) < 30:
        return {"rsi": [], "supertrend": []}

    recent = klines[-limit:]
    closes = np.array([k["close"] for k in recent], dtype=np.float64)
    highs = np.array([k["high"] for k in recent], dtype=np.float64)
    lows = np.array([k["low"] for k in recent], dtype=np.float64)
    times = [k["time"] for k in recent]

    rsi_arr = rsi(closes)
    supertrend_arr = supertrend(highs, lows, closes)

    def to_chart_points(values: np.ndarray) -> list[dict]:
        points = []
        for t, v in zip(times, values):
            if v is None or np.isnan(v):
                continue
            points.append({"time": t, "value": round(float(v), 4)})
        return points

    return {
        "rsi": to_chart_points(rsi_arr),
        "supertrend": to_chart_points(supertrend_arr),
    }


def _ob_os_status(val, ob_thresh, os_thresh):
    """Determine overbought/oversold status."""
    if val is None:
        return "neutral"
    if val >= ob_thresh:
        return "overbought"
    elif val <= os_thresh:
        return "oversold"
    return "neutral"


from target_contract import (  # noqa: E402
    AMBIGUOUS as _AMBIGUOUS, DOWN as _DOWN, UP as _UP,
    label_endpoint as _label_endpoint,
    label_first_touch as _label_first_touch)


def build_sequences(
    features: np.ndarray,
    closes: np.ndarray,
    lookback: int = LOOKBACK,
    horizons: list[int] = None,
    atr_arr: np.ndarray = None,
    highs: np.ndarray = None,
    lows: np.ndarray = None,
    return_magnitude: bool = False,
    memmap_path: str = None,
    return_valid_mask: bool = False,
    return_settlement_labels: bool = False,
):
    """
    Build sliding-window sequences for ML training.
    Uses adaptive threshold based on ATR to prevent majority-class domination.

    If `highs`/`lows` are provided, the triple-barrier labels use true intrabar
    extremes (rigorous). Otherwise they fall back to closes (approximate).

    Returns:
        X: np.ndarray shape (N, lookback, NUM_FEATURES)
        Y: dict[horizon -> np.ndarray shape (N, 3)] one-hot [DOWN, NEUTRAL, UP]
        (if return_magnitude) Ymag: dict[horizon -> np.ndarray] realized absolute
            close-to-close move over the horizon, as a fraction of price. Used to
            train a separate magnitude regressor (direction and size are different
            objectives with different losses).
    """
    if horizons is None:
        horizons = [5, 15]  # pruned 2026-06-21: only the two tradeable market horizons

    # Compute a decision-time threshold series. Historical labels must use the
    # volatility state available at that point, not the dataset's final state.
    if atr_arr is not None:
        threshold_series = compute_adaptive_threshold_series(closes, atr_arr)
    else:
        cost_floor = float(os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008"))
        threshold_series = np.full(len(closes), min(max(cost_floor, 0.0), 0.003))

    max_h = max(horizons)

    n_samples = max(0, len(features) - max_h - lookback)
    shape = (n_samples, lookback, features.shape[1])
    if memmap_path and n_samples > 0:
        os.makedirs(os.path.dirname(memmap_path), exist_ok=True)
        if os.path.exists(memmap_path):
            os.remove(memmap_path)
        X = np.memmap(memmap_path, mode="w+", dtype=np.float32, shape=shape)
    else:
        X = np.empty(shape, dtype=np.float32)
    Y = {h: np.zeros((n_samples, 3), dtype=np.float32) for h in horizons}
    Ymag = {h: np.zeros(n_samples, dtype=np.float32) for h in horizons}
    # True where the row carries a USABLE directional label. False only for AMBIGUOUS
    # rows - a single bar touched both barriers, so first-touch order is unknowable.
    Yvalid = {h: np.ones(n_samples, dtype=bool) for h in horizons}
    #: SETTLEMENT labels: where price ENDS, the question Polymarket resolves on.
    #: Every settlement row is usable - endpoint direction has no ambiguous case.
    Ysettle = {h: np.zeros((n_samples, 3), dtype=np.float32) for h in horizons}

    for row, i in enumerate(range(lookback, len(features) - max_h)):
        X[row] = features[i - lookback: i]

        # Entry = close of candle `i`, which is the SAME candle the last feature row
        # (features[i-1] -> candle i) is built from. This matches inference exactly
        # (seq ends at the latest candle and we predict from its close), removing the
        # 1-bar train/serve skew that came from entering at closes[i+1].
        current_price = closes[i]
        threshold = float(threshold_series[i])

        for h in horizons:
            # Magnitude target: realized |move| over the horizon as a fraction.
            end_idx = min(i + h, len(closes) - 1)
            mag = abs(closes[end_idx] - current_price) / current_price if current_price > 0 else 0.0
            Ymag[h][row] = mag
            # TRIPLE BARRIER METHOD
            # We look ahead up to h periods.
            # Barrier 1 (TP): current_price * (1 + threshold)
            # Barrier 2 (SL): current_price * (1 - threshold)
            # Barrier 3 (Timeout): end of h periods
            
            # ONE definition of the label, shared with the live grader. See
            # backend/target_contract.py: two implementations of "direction" cannot be kept in
            # agreement by discipline, and when they drifted apart a first-touch model was being
            # graded on endpoint settlement and corrected by the difference.
            stop = min(i + h + 1, len(closes))
            path_high = (highs[i + 1:stop] if highs is not None else closes[i + 1:stop])
            path_low = (lows[i + 1:stop] if lows is not None else closes[i + 1:stop])

            # BOTH questions are labelled, because they are different questions and a head
            # trained on one may not price the other. The PATH label is which barrier is
            # touched first; the SETTLEMENT label is where price actually ENDS. Emitting only
            # the first is what left the product with no settlement head to train.
            outcome = _label_first_touch(current_price, path_high, path_low, threshold)
            settle_idx = min(i + h, len(closes) - 1)
            settlement = _label_endpoint(current_price, closes[settle_idx], threshold)
            if settlement == _UP:
                Ysettle[h][row, 2] = 1.0
            elif settlement == _DOWN:
                Ysettle[h][row, 0] = 1.0
            else:
                Ysettle[h][row, 1] = 1.0

            if outcome == _UP:
                Y[h][row, 2] = 1.0
            elif outcome == _DOWN:
                Y[h][row, 0] = 1.0
            else:
                # AMBIGUOUS keeps a NEUTRAL one-hot so `argmax` stays well defined for any
                # caller that ignores the mask (an all-zero row would argmax to DOWN, which is
                # worse than the bug being fixed). The MASK is what excludes it from training.
                Y[h][row, 1] = 1.0
                if outcome == _AMBIGUOUS:
                    Yvalid[h][row] = False

    if isinstance(X, np.memmap):
        X.flush()

    if return_settlement_labels:
        # The SETTLEMENT head's labels. Returned only on request so every existing caller is
        # untouched, but available so a settlement head can actually be trained - without
        # these, "use a settlement probability" was advice with no way to follow it.
        if return_magnitude and return_valid_mask:
            return X, Y, Ymag, Yvalid, Ysettle
        if return_valid_mask:
            return X, Y, Yvalid, Ysettle
        return X, Y, Ysettle
    if return_valid_mask:
        # Callers that ask for the mask get it; callers that do not are UNCHANGED, which is why
        # AMBIGUOUS still carries a NEUTRAL one-hot rather than an all-zero row.
        if return_magnitude:
            return X, Y, Ymag, Yvalid
        return X, Y, Yvalid
    if return_magnitude:
        return X, Y, Ymag
    return X, Y
