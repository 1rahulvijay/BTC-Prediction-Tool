"""
Technical Indicators & Feature Engineering
Pure numpy implementations + expanded feature vector construction.
109 features: price/volume, order-flow microstructure, derivatives, advanced
indicators, feature interactions, cross-exchange signals, liquidations, volatility,
deep microstructure, regime/vol forecasting and institutional alpha feeds.
"""

import os
import json
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


def vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """VWAP — cumulative."""
    tp = (highs + lows + closes) / 3.0
    cum_tpv = np.cumsum(tp * volumes)
    cum_vol = np.cumsum(volumes)
    result = np.where(cum_vol > 0, cum_tpv / cum_vol, closes)
    return result


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
        lvn_price = pmin + (vol_by_bin.argmin() + 0.5) / n_bins * rng
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
        with duckdb.connect(db_path) as conn:
            try:
                df = conn.execute("SELECT feature FROM feature_retirement_events WHERE status = 'retired'").df()
                names = df["feature"].tolist()
                return [FEATURE_NAMES.index(n) for n in names if n in FEATURE_NAMES]
            except Exception:
                return []
    except Exception as e:
        print(f"Failed to load retired features: {e}")
    return []


RETIRED_FEATURE_IDX = load_retired_feature_indices()
LOOKBACK = 60


def clamp(val: float, lo: float, hi: float) -> float:
    if val is None or np.isnan(val):
        return 0.0
    return max(lo, min(hi, float(val)))


def compute_adaptive_threshold(closes: np.ndarray, atr_arr: np.ndarray) -> float:
    """
    Adaptive threshold based on recent ATR.
    Instead of fixed 0.03%, use median ATR% * 0.15 as the neutral zone boundary.
    This prevents the majority-NEUTRAL class problem.
    """
    # Cost floor: the neutral band must be at least the round-trip trading cost + buffer,
    # so the model is never trained to label a sub-cost move as a tradeable UP/DOWN.
    # ~0.08% covers ~0.04% taker x2; tune via BTC_LABEL_COST_FLOOR.
    cost_floor = float(os.environ.get("BTC_LABEL_COST_FLOOR", "0.0008"))
    valid_atr = atr_arr[~np.isnan(atr_arr)]
    if len(valid_atr) < 10:
        return cost_floor  # fallback

    median_atr = np.median(valid_atr[-100:])
    median_price = np.median(closes[-100:])

    if median_price <= 0:
        return cost_floor

    atr_pct = median_atr / median_price
    # 15% of ATR%, never below the cost floor, capped so high-vol bands stay sane.
    threshold = max(cost_floor, min(0.003, atr_pct * 0.15))
    return threshold


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
    vwap_arr = vwap(highs, lows, closes, volumes)
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
    eth_price_raw = series("eth_price", 0.0)
    sol_price_raw = series("sol_price", 0.0)
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
    eth_ret = eth_price_raw[1:] - eth_price_raw[:-1]
    features[:, 100] = np.clip((eth_ret - ret_1m) / (safe[1:] + 1e-9) * 1000, -1.0, 1.0)

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
    features[:, 104] = np.cos(2.0 * np.pi * _ttf[1:])      # cyclical encoding ∈ [-1,1]

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
    _vpin_raw = series("vpin", 0.0)
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
    _cn = np.std(_cvd_cum) if np.std(_cvd_cum) > 1e-9 else 1.0
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
    _ltd_raw = series("large_trade_delta", 0.0)
    _lti_raw = series("large_trade_imbalance", 0.0)
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


def build_sequences(
    features: np.ndarray,
    closes: np.ndarray,
    lookback: int = LOOKBACK,
    horizons: list[int] = None,
    atr_arr: np.ndarray = None,
    highs: np.ndarray = None,
    lows: np.ndarray = None,
    return_magnitude: bool = False,
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
        horizons = [1, 3, 5, 7, 10, 15]  # full set — was [1,5,10,15], silently dropped 3m/7m

    # Compute adaptive threshold (used as the TP/SL barrier)
    if atr_arr is not None:
        threshold = compute_adaptive_threshold(closes, atr_arr)
    else:
        threshold = 0.0003

    max_h = max(horizons)

    X = []
    Y = {h: [] for h in horizons}
    Ymag = {h: [] for h in horizons}

    for i in range(lookback, len(features) - max_h):
        seq = features[i - lookback: i]
        X.append(seq)

        # Entry = close of candle `i`, which is the SAME candle the last feature row
        # (features[i-1] -> candle i) is built from. This matches inference exactly
        # (seq ends at the latest candle and we predict from its close), removing the
        # 1-bar train/serve skew that came from entering at closes[i+1].
        current_price = closes[i]

        for h in horizons:
            # Magnitude target: realized |move| over the horizon as a fraction.
            end_idx = min(i + h, len(closes) - 1)
            mag = abs(closes[end_idx] - current_price) / current_price if current_price > 0 else 0.0
            Ymag[h].append(mag)
            # TRIPLE BARRIER METHOD
            # We look ahead up to h periods.
            # Barrier 1 (TP): current_price * (1 + threshold)
            # Barrier 2 (SL): current_price * (1 - threshold)
            # Barrier 3 (Timeout): end of h periods
            
            upper_barrier = current_price * (1 + threshold)
            lower_barrier = current_price * (1 - threshold)
            
            hit_up = False
            hit_down = False
            
            for j in range(1, h + 1):
                idx = i + j
                if idx >= len(closes):
                    break

                # Use true intrabar high/low when available, else approximate with close.
                future_high = highs[idx] if highs is not None else closes[idx]
                future_low = lows[idx] if lows is not None else closes[idx]

                touched_up = future_high >= upper_barrier
                touched_down = future_low <= lower_barrier

                if touched_up and touched_down:
                    # Both barriers touched in the same bar — intrabar order is
                    # unknown, so resolve by the bar's net direction (close vs entry).
                    if closes[idx] >= current_price:
                        hit_up = True
                    else:
                        hit_down = True
                    break
                elif touched_up:
                    hit_up = True
                    break
                elif touched_down:
                    hit_down = True
                    break
                    
            if hit_up:
                Y[h].append([0, 0, 1])   # UP
            elif hit_down:
                Y[h].append([1, 0, 0])   # DOWN
            else:
                Y[h].append([0, 1, 0])   # NEUTRAL (Timeout)

    X = np.array(X, dtype=np.float32)
    Y = {h: np.array(labels, dtype=np.float32) for h, labels in Y.items()}

    if return_magnitude:
        Ymag = {h: np.array(v, dtype=np.float32) for h, v in Ymag.items()}
        return X, Y, Ymag
    return X, Y
