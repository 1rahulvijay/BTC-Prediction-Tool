#!/usr/bin/env python
"""
build_binance_updown_feature_dataset.py

Build a leak-safe BTC up/down / anchor-beat research dataset from Binance public data.
(See module docstring in the design plan.) Outputs to data/research/:
  binance_updown_features.parquet, binance_updown_rounds.parquet, binance_updown_feature_manifest.json

For real betting EV you STILL need Polymarket ask/spread from the recorder — this only builds BTC fair-value.
Train with time/round-grouped purged walk-forward, never random split.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"


def utc_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_date(s: str) -> datetime:
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def now_utc_floor_minute() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def safe_div(a, b, default=0.0):
    return np.where(np.abs(b) > 1e-12, a / b, default)


def normal_cdf_np(x: np.ndarray) -> np.ndarray:
    vec = np.vectorize(lambda z: 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0))))
    return vec(np.asarray(x, dtype=float))


def ensure_monotonic_index(df: pd.DataFrame, name: str = "timestamp") -> pd.DataFrame:
    return df.sort_values(name).drop_duplicates(name).reset_index(drop=True)


def retry_get(url: str, params: dict, timeout: int = 20, tries: int = 5, sleep: float = 0.35):
    last_err = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (418, 429):
                wait = sleep * (2 ** i) + 1.0
                print(f"[rate-limit] {r.status_code}; sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            wait = sleep * (2 ** i)
            print(f"[retry] {i+1}/{tries} {url} failed: {e}; sleep {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {tries} tries: {url} params={params} err={last_err}")


def fetch_binance_klines(symbol, interval, start_ms, end_ms, market="spot", limit=1000) -> pd.DataFrame:
    if market == "spot":
        url = f"{SPOT_BASE}/api/v3/klines"
    elif market == "futures":
        url = f"{FUTURES_BASE}/fapi/v1/klines"
    elif market == "mark":
        url = f"{FUTURES_BASE}/fapi/v1/markPriceKlines"
    elif market == "premium":
        url = f"{FUTURES_BASE}/fapi/v1/premiumIndexKlines"
    else:
        raise ValueError(f"unknown market={market}")
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        batch = retry_get(url, {"symbol": symbol, "interval": interval, "startTime": cursor,
                                "endTime": end_ms, "limit": limit})
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < limit:
            break
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame()
    cols = ["open_time_ms", "open", "high", "low", "close", "volume", "close_time_ms", "quote_volume",
            "n_trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(rows, columns=cols[: len(rows[0])])
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "n_trades" in df.columns:
        df["n_trades"] = pd.to_numeric(df["n_trades"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["open_time_ms"].astype("int64"), unit="ms", utc=True)
    return ensure_monotonic_index(df, "timestamp")


def fetch_funding_rates(symbol, start_ms, end_ms) -> pd.DataFrame:
    url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        batch = retry_get(url, {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1]["fundingTime"]) + 1
        if len(batch) < 1000:
            break
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["timestamp", "funding_rate"]].sort_values("timestamp").drop_duplicates("timestamp")


ROLL_WINDOWS = [3, 5, 10, 15, 30, 60, 120, 240, 360, 720, 1440]
EMA_SPANS = [5, 10, 20, 50, 100, 200]
RSI_WINDOWS = [7, 14, 21]
BB_WINDOWS = [20, 50, 100]


def add_base_market_features(df):
    out = df.copy()
    out["log_close"] = np.log(out["close"])
    out["ret_1m"] = out["log_close"].diff()
    out["ret_1m_bps"] = out["ret_1m"] * 10_000.0
    out["abs_ret_1m_bps"] = out["ret_1m_bps"].abs()
    out["hl_range_bps"] = (out["high"] / out["low"] - 1.0) * 10_000.0
    out["oc_body_bps"] = (out["close"] / out["open"] - 1.0) * 10_000.0
    out["upper_wick_bps"] = (out["high"] / np.maximum(out["open"], out["close"]) - 1.0) * 10_000.0
    out["lower_wick_bps"] = (np.minimum(out["open"], out["close"]) / out["low"] - 1.0) * 10_000.0
    out["close_pos_in_bar"] = safe_div(out["close"] - out["low"], out["high"] - out["low"], 0.5)
    out["quote_volume_log"] = np.log1p(out["quote_volume"].clip(lower=0))
    out["volume_log"] = np.log1p(out["volume"].clip(lower=0))
    out["trades_log"] = np.log1p(out["n_trades"].clip(lower=0))
    out["taker_buy_ratio"] = safe_div(out["taker_buy_base"], out["volume"], 0.5)
    out["taker_sell_base"] = (out["volume"] - out["taker_buy_base"]).clip(lower=0)
    out["taker_delta_base"] = out["taker_buy_base"] - out["taker_sell_base"]
    out["taker_delta_quote"] = out["taker_delta_base"] * out["close"]
    out["taker_delta_ratio"] = safe_div(out["taker_delta_base"], out["volume"], 0.0)
    out["cvd_base"] = out["taker_delta_base"].fillna(0).cumsum()
    out["cvd_quote"] = out["taker_delta_quote"].fillna(0).cumsum()
    return out


def add_time_features(df):
    out = df.copy()
    ts = out["timestamp"]
    hour = ts.dt.hour + ts.dt.minute / 60.0
    dow = ts.dt.dayofweek
    minute = ts.dt.minute
    out["hour_utc"] = hour
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow"] = dow
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["minute_sin"] = np.sin(2 * np.pi * minute / 60.0)
    out["minute_cos"] = np.cos(2 * np.pi * minute / 60.0)
    out["is_weekend"] = (dow >= 5).astype(int)
    out["session_asia"] = ((hour >= 0) & (hour < 8)).astype(int)
    out["session_europe"] = ((hour >= 7) & (hour < 16)).astype(int)
    out["session_us"] = ((hour >= 13) & (hour < 22)).astype(int)
    return out


def add_rolling_features(df):
    out = df.copy()
    for w in ROLL_WINDOWS:
        r = out["ret_1m_bps"]; vol = out["volume"]; qv = out["quote_volume"]; delta = out["taker_delta_base"]
        mp = max(2, w // 3)
        out[f"ret_sum_{w}m_bps"] = r.rolling(w, min_periods=mp).sum()
        out[f"ret_mean_{w}m_bps"] = r.rolling(w, min_periods=mp).mean()
        out[f"realized_vol_{w}m_bps"] = r.rolling(w, min_periods=mp).std()
        out[f"abs_ret_mean_{w}m_bps"] = r.abs().rolling(w, min_periods=mp).mean()
        out[f"ret_skew_{w}m"] = r.rolling(w, min_periods=min(w, max(5, w // 2))).skew()
        out[f"ret_kurt_{w}m"] = r.rolling(w, min_periods=min(w, max(5, w // 2))).kurt()
        roll_high = out["high"].rolling(w, min_periods=mp).max()
        roll_low = out["low"].rolling(w, min_periods=mp).min()
        out[f"range_{w}m_bps"] = (roll_high / roll_low - 1.0) * 10_000.0
        out[f"pos_in_range_{w}m"] = safe_div(out["close"] - roll_low, roll_high - roll_low, 0.5)
        out[f"volume_sum_{w}m"] = vol.rolling(w, min_periods=mp).sum()
        out[f"quote_volume_sum_{w}m"] = qv.rolling(w, min_periods=mp).sum()
        out[f"trades_sum_{w}m"] = out["n_trades"].rolling(w, min_periods=mp).sum()
        out[f"taker_buy_ratio_{w}m"] = safe_div(out["taker_buy_base"].rolling(w, min_periods=mp).sum(),
                                                vol.rolling(w, min_periods=mp).sum(), 0.5)
        out[f"taker_delta_sum_{w}m"] = delta.rolling(w, min_periods=mp).sum()
        out[f"taker_delta_ratio_{w}m"] = safe_div(delta.rolling(w, min_periods=mp).sum(),
                                                  vol.rolling(w, min_periods=mp).sum(), 0.0)
        out[f"cvd_change_{w}m"] = out["cvd_base"].diff(w)
        out[f"volume_z_{w}m"] = safe_div(vol - vol.rolling(w, min_periods=mp).mean(),
                                         vol.rolling(w, min_periods=mp).std(), 0.0)
        buy_vol = out["taker_buy_base"].rolling(w, min_periods=mp).sum()
        sell_vol = out["taker_sell_base"].rolling(w, min_periods=mp).sum()
        out[f"vpin_proxy_{w}m"] = safe_div((buy_vol - sell_vol).abs(), buy_vol + sell_vol, 0.0)
        path_len = r.abs().rolling(w, min_periods=mp).sum()
        net_move = r.rolling(w, min_periods=mp).sum().abs()
        out[f"path_efficiency_{w}m"] = safe_div(net_move, path_len, 0.0)
        out[f"range_compression_{w}m"] = safe_div(out[f"range_{w}m_bps"],
                                                  out[f"realized_vol_{w}m_bps"] * math.sqrt(max(w, 1)), 0.0)
    for short, lng in [(3, 15), (5, 30), (15, 60), (30, 120), (60, 240)]:
        out[f"rv_ratio_{short}_{lng}"] = safe_div(out[f"realized_vol_{short}m_bps"], out[f"realized_vol_{lng}m_bps"], 1.0)
        out[f"range_ratio_{short}_{lng}"] = safe_div(out[f"range_{short}m_bps"], out[f"range_{lng}m_bps"], 1.0)
    return out


def add_indicator_features(df):
    out = df.copy()
    for span in EMA_SPANS:
        ema = out["close"].ewm(span=span, adjust=False, min_periods=max(2, span // 3)).mean()
        out[f"ema_{span}"] = ema
        out[f"dist_ema_{span}_bps"] = (out["close"] / ema - 1.0) * 10_000.0
        out[f"ema_slope_{span}_bps"] = (ema / ema.shift(1) - 1.0) * 10_000.0
    for fast, slow in [(5, 20), (10, 50), (20, 100), (50, 200)]:
        out[f"ema_spread_{fast}_{slow}_bps"] = (out[f"ema_{fast}"] / out[f"ema_{slow}"] - 1.0) * 10_000.0
    delta = out["close"].diff()
    gain = delta.clip(lower=0); loss = (-delta).clip(lower=0)
    for w in RSI_WINDOWS:
        avg_gain = gain.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        avg_loss = loss.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        rs = safe_div(avg_gain, avg_loss, 0.0)
        out[f"rsi_{w}"] = 100.0 - (100.0 / (1.0 + rs))
    ema12 = out["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = out["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_bps"] = macd / out["close"] * 10_000.0
    out["macd_signal_bps"] = signal / out["close"] * 10_000.0
    out["macd_hist_bps"] = (macd - signal) / out["close"] * 10_000.0
    tr = pd.concat([out["high"] - out["low"], (out["high"] - out["close"].shift(1)).abs(),
                    (out["low"] - out["close"].shift(1)).abs()], axis=1).max(axis=1)
    for w in [7, 14, 30, 60]:
        atr = tr.ewm(alpha=1 / w, adjust=False, min_periods=w).mean()
        out[f"atr_{w}_bps"] = atr / out["close"] * 10_000.0
    for w in BB_WINDOWS:
        ma = out["close"].rolling(w, min_periods=max(5, w // 2)).mean()
        sd = out["close"].rolling(w, min_periods=max(5, w // 2)).std()
        out[f"bb_z_{w}"] = safe_div(out["close"] - ma, sd, 0.0)
        out[f"bb_width_{w}_bps"] = safe_div(4.0 * sd, ma, 0.0) * 10_000.0
        hh = out["high"].rolling(w, min_periods=max(5, w // 2)).max()
        ll = out["low"].rolling(w, min_periods=max(5, w // 2)).min()
        out[f"stoch_{w}"] = safe_div(out["close"] - ll, hh - ll, 0.5)
        out[f"donchian_width_{w}_bps"] = (hh / ll - 1.0) * 10_000.0
    out["shock_1m_vs_60m"] = safe_div(out["abs_ret_1m_bps"], out["realized_vol_60m_bps"], 0.0)
    out["shock_5m_vs_60m"] = safe_div(out["ret_sum_5m_bps"].abs(), out["realized_vol_60m_bps"] * math.sqrt(5), 0.0)
    out["volume_shock_5m"] = out["volume_z_60m"].rolling(5, min_periods=2).max()
    return out


def merge_futures_features(spot, symbol, start_ms, end_ms):
    out = spot.copy()
    try:
        fut = fetch_binance_klines(symbol, "1m", start_ms, end_ms, market="futures")
        fut = fut[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "n_trades"]].rename(
            columns={c: f"fut_{c}" for c in ["open", "high", "low", "close", "volume", "quote_volume", "n_trades"]})
        out = out.merge(fut, on="timestamp", how="left")
        out["fut_basis_bps"] = (out["fut_close"] / out["close"] - 1.0) * 10_000.0
        out["fut_ret_1m_bps"] = np.log(out["fut_close"]).diff() * 10_000.0
        out["spot_fut_ret_diff_1m_bps"] = out["ret_1m_bps"] - out["fut_ret_1m_bps"]
        for w in [5, 15, 60, 240]:
            mp = max(2, w // 3)
            out[f"fut_basis_mean_{w}m_bps"] = out["fut_basis_bps"].rolling(w, min_periods=mp).mean()
            out[f"fut_basis_z_{w}m"] = safe_div(out["fut_basis_bps"] - out[f"fut_basis_mean_{w}m_bps"],
                                                out["fut_basis_bps"].rolling(w, min_periods=mp).std(), 0.0)
            out[f"spot_fut_ret_diff_sum_{w}m_bps"] = out["spot_fut_ret_diff_1m_bps"].rolling(w, min_periods=mp).sum()
    except Exception as e:
        print(f"[warn] futures klines unavailable: {e}")
    try:
        mark = fetch_binance_klines(symbol, "1m", start_ms, end_ms, market="mark")
        mark = mark[["timestamp", "close"]].rename(columns={"close": "mark_close"})
        out = out.merge(mark, on="timestamp", how="left")
        out["mark_basis_bps"] = (out["mark_close"] / out["close"] - 1.0) * 10_000.0
        for w in [15, 60, 240]:
            mp = max(2, w // 3)
            out[f"mark_basis_z_{w}m"] = safe_div(out["mark_basis_bps"] - out["mark_basis_bps"].rolling(w, min_periods=mp).mean(),
                                                 out["mark_basis_bps"].rolling(w, min_periods=mp).std(), 0.0)
    except Exception as e:
        print(f"[warn] mark price klines unavailable: {e}")
    try:
        prem = fetch_binance_klines(symbol, "1m", start_ms, end_ms, market="premium")
        prem = prem[["timestamp", "close"]].rename(columns={"close": "premium_index"})
        out = out.merge(prem, on="timestamp", how="left")
        for w in [15, 60, 240]:
            mp = max(2, w // 3)
            out[f"premium_index_mean_{w}m"] = out["premium_index"].rolling(w, min_periods=mp).mean()
            out[f"premium_index_z_{w}m"] = safe_div(out["premium_index"] - out[f"premium_index_mean_{w}m"],
                                                    out["premium_index"].rolling(w, min_periods=mp).std(), 0.0)
    except Exception as e:
        print(f"[warn] premium index klines unavailable: {e}")
    try:
        fr = fetch_funding_rates(symbol, start_ms, end_ms)
        if not fr.empty:
            out = out.sort_values("timestamp"); fr = fr.sort_values("timestamp")
            out = pd.merge_asof(out, fr, on="timestamp", direction="backward")
            out["funding_rate"] = out["funding_rate"].ffill()
            out["funding_rate_bps"] = out["funding_rate"] * 10_000.0
            out["funding_rate_change"] = out["funding_rate"].diff()
            hour = out["timestamp"].dt.hour + out["timestamp"].dt.minute / 60.0
            next_funding_hour = np.ceil((hour + 1e-9) / 8.0) * 8.0
            next_funding_hour = np.where(next_funding_hour >= 24.0, 24.0, next_funding_hour)
            out["hours_to_funding"] = next_funding_hour - hour
            out["funding_clock_cos"] = np.cos(2 * np.pi * out["hours_to_funding"] / 8.0)
            out["funding_clock_sin"] = np.sin(2 * np.pi * out["hours_to_funding"] / 8.0)
    except Exception as e:
        print(f"[warn] funding rates unavailable: {e}")
    return out


@dataclass
class HorizonConfig:
    minutes: int


def build_round_table(base, horizons, tie_policy="down"):
    all_rounds = []
    df = base[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "n_trades"]].copy()
    df["ts_ns"] = df["timestamp"].astype("int64")
    minute_ns = 60 * 1_000_000_000
    for h in horizons:
        h_ns = h * minute_ns
        df_h = df.copy(); df_h["horizon_min"] = h
        df_h["round_start_ns"] = (df_h["ts_ns"] // h_ns) * h_ns
        g = df_h.groupby("round_start_ns", sort=True)
        rounds = g.agg(round_start=("timestamp", "first"), round_end=("timestamp", "last"),
                       anchor_price=("open", "first"), expiry_close=("close", "last"),
                       round_high=("high", "max"), round_low=("low", "min"),
                       round_volume=("volume", "sum"), round_quote_volume=("quote_volume", "sum"),
                       round_trades=("n_trades", "sum"), bars=("close", "size")).reset_index(drop=True)
        rounds = rounds[rounds["bars"] >= h].copy()
        rounds["horizon_min"] = h
        rounds["round_end_expected"] = rounds["round_start"] + pd.to_timedelta(h - 1, unit="m")
        rounds["expiry_timestamp"] = rounds["round_start"] + pd.to_timedelta(h, unit="m")
        rounds["expiry_return_bps"] = (rounds["expiry_close"] / rounds["anchor_price"] - 1.0) * 10_000.0
        rounds["max_up_bps"] = (rounds["round_high"] / rounds["anchor_price"] - 1.0) * 10_000.0
        rounds["max_down_bps"] = (rounds["round_low"] / rounds["anchor_price"] - 1.0) * 10_000.0
        rounds["round_range_bps"] = (rounds["round_high"] / rounds["round_low"] - 1.0) * 10_000.0
        if tie_policy == "neutral":
            rounds["label_up_win"] = np.where(rounds["expiry_close"] > rounds["anchor_price"], 1,
                                              np.where(rounds["expiry_close"] < rounds["anchor_price"], 0, np.nan))
        elif tie_policy == "down":
            rounds["label_up_win"] = (rounds["expiry_close"] > rounds["anchor_price"]).astype(int)
        elif tie_policy == "up":
            rounds["label_up_win"] = (rounds["expiry_close"] >= rounds["anchor_price"]).astype(int)
        else:
            raise ValueError("--tie-policy must be down, up, or neutral")
        rounds["label_down_win"] = 1 - rounds["label_up_win"]
        rounds["winner_side"] = np.where(rounds["label_up_win"] == 1, "UP", "DOWN")
        rounds["round_id"] = ("BTCUSDT_" + rounds["horizon_min"].astype(str) + "m_" +
                              rounds["round_start"].dt.strftime("%Y%m%d%H%M"))
        all_rounds.append(rounds)
    return pd.concat(all_rounds, ignore_index=True).sort_values(["horizon_min", "round_start"]).reset_index(drop=True)


def add_anchor_snapshot_features(base_features, rounds, horizons):
    all_rows = []
    base = base_features.copy()
    base["ts_ns"] = base["timestamp"].astype("int64")
    minute_ns = 60 * 1_000_000_000
    round_cols = ["round_id", "horizon_min", "round_start", "expiry_timestamp", "anchor_price",
                  "expiry_close", "round_high", "round_low", "expiry_return_bps", "max_up_bps",
                  "max_down_bps", "round_range_bps", "label_up_win", "label_down_win", "winner_side"]
    for h in horizons:
        h_ns = h * minute_ns
        snap = base.copy(); snap["horizon_min"] = h
        snap["round_start_ns"] = (snap["ts_ns"] // h_ns) * h_ns
        snap["round_start"] = pd.to_datetime(snap["round_start_ns"], utc=True)
        r = rounds[rounds["horizon_min"] == h][round_cols].copy()
        snap = snap.merge(r, on=["horizon_min", "round_start"], how="inner")
        snap["snapshot_idx_in_round"] = ((snap["timestamp"] - snap["round_start"]).dt.total_seconds() // 60).astype(int)
        snap = snap[(snap["snapshot_idx_in_round"] >= 0) & (snap["snapshot_idx_in_round"] < h)].copy()
        snap = snap.sort_values(["round_id", "timestamp"])
        g = snap.groupby("round_id", sort=False)
        snap["high_so_far"] = g["high"].cummax()
        snap["low_so_far"] = g["low"].cummin()
        snap["volume_so_far"] = g["volume"].cumsum()
        snap["quote_volume_so_far"] = g["quote_volume"].cumsum()
        snap["trades_so_far"] = g["n_trades"].cumsum()
        # FUTURE extremes (from the NEXT bar to expiry) — for the forward-looking line-cross label.
        # inclusive suffix min/max via reversed cummin/cummax, then shift(-1) to exclude the current bar.
        snap["_inc_min_low"] = snap[::-1].groupby("round_id", sort=False)["low"].cummin()[::-1]
        snap["_inc_max_high"] = snap[::-1].groupby("round_id", sort=False)["high"].cummax()[::-1]
        snap["future_min_low"] = snap.groupby("round_id", sort=False)["_inc_min_low"].shift(-1)
        snap["future_max_high"] = snap.groupby("round_id", sort=False)["_inc_max_high"].shift(-1)
        snap["seconds_elapsed"] = (snap["snapshot_idx_in_round"] + 1) * 60
        snap["seconds_left"] = (h * 60 - snap["seconds_elapsed"]).clip(lower=0)
        snap["distance_from_anchor_bps"] = (snap["close"] / snap["anchor_price"] - 1.0) * 10_000.0
        snap["distance_from_anchor_pct"] = snap["distance_from_anchor_bps"] / 100.0
        snap["abs_distance_from_anchor_bps"] = snap["distance_from_anchor_bps"].abs()
        snap["current_side"] = np.where(snap["close"] > snap["anchor_price"], 1,
                                        np.where(snap["close"] < snap["anchor_price"], -1, 0))
        snap["is_above_anchor"] = (snap["close"] > snap["anchor_price"]).astype(int)
        snap["is_below_anchor"] = (snap["close"] < snap["anchor_price"]).astype(int)

        def _cross_stats(s):
            sign = s.replace(0, np.nan).ffill().fillna(0)
            cross = ((sign != sign.shift(1)) & (sign.shift(1).fillna(0) != 0) & (sign != 0)).astype(int)
            age = []; cur_age = 0; last = 0
            for val in sign:
                if val == 0:
                    cur_age = 0
                elif val == last:
                    cur_age += 60
                else:
                    cur_age = 60
                last = val
                age.append(cur_age)
            return pd.DataFrame({"recent_cross_count": cross.cumsum().values,
                                 "current_side_age_seconds": age}, index=s.index)

        stats = g["current_side"].apply(_cross_stats).reset_index(level=0, drop=True)
        snap["recent_cross_count"] = stats["recent_cross_count"]
        snap["current_side_age_seconds"] = stats["current_side_age_seconds"]
        snap["time_above_frac"] = g["is_above_anchor"].cumsum() / (snap["snapshot_idx_in_round"] + 1)
        snap["time_below_frac"] = g["is_below_anchor"].cumsum() / (snap["snapshot_idx_in_round"] + 1)
        snap["max_favorable_bps_current_side"] = np.where(
            snap["current_side"] >= 0, (snap["high_so_far"] / snap["anchor_price"] - 1.0) * 10_000.0,
            (snap["anchor_price"] / snap["low_so_far"] - 1.0) * 10_000.0)
        snap["max_adverse_bps_current_side"] = np.where(
            snap["current_side"] >= 0, (snap["anchor_price"] / snap["low_so_far"] - 1.0) * 10_000.0,
            (snap["high_so_far"] / snap["anchor_price"] - 1.0) * 10_000.0)
        snap["range_so_far_bps"] = (snap["high_so_far"] / snap["low_so_far"] - 1.0) * 10_000.0
        snap["anchor_path_efficiency"] = safe_div(snap["distance_from_anchor_bps"].abs(), snap["range_so_far_bps"], 0.0)
        local_vol_1m_bps = snap["realized_vol_30m_bps"].fillna(snap["realized_vol_60m_bps"])
        sigma_sqrt_t = local_vol_1m_bps * np.sqrt(np.maximum(snap["seconds_left"], 1) / 60.0)
        snap["barrier_dz"] = safe_div(snap["abs_distance_from_anchor_bps"], sigma_sqrt_t, 0.0)
        snap["analytic_p_hold"] = normal_cdf_np(snap["barrier_dz"].clip(-8, 8).values)
        snap["analytic_p_up"] = np.where(snap["current_side"] > 0, snap["analytic_p_hold"],
                                         np.where(snap["current_side"] < 0, 1.0 - snap["analytic_p_hold"], 0.5))
        snap["analytic_p_down"] = 1.0 - snap["analytic_p_up"]
        snap["line_risk_zone"] = ((snap["abs_distance_from_anchor_bps"] < 2.0) & (snap["seconds_left"] > 60)).astype(int)
        snap["late_far_hold_zone"] = ((snap["abs_distance_from_anchor_bps"] > 10.0) & (snap["seconds_left"] < 60)).astype(int)
        snap["too_early_zone"] = (snap["seconds_left"] > 180).astype(int)
        snap["near_expiry_zone"] = (snap["seconds_left"] <= 30).astype(int)
        snap["label_current_side_wins"] = np.where(snap["current_side"] > 0, snap["label_up_win"],
                                                   np.where(snap["current_side"] < 0, snap["label_down_win"], np.nan))
        snap["label_current_side_loses"] = 1 - snap["label_current_side_wins"]
        # line cross = does price return THROUGH the anchor in the REMAINING time (from snapshot forward)?
        snap["label_line_cross_before_expiry"] = np.where(
            snap["current_side"] > 0, (snap["future_min_low"] <= snap["anchor_price"]).astype(int),
            np.where(snap["current_side"] < 0, (snap["future_max_high"] >= snap["anchor_price"]).astype(int), np.nan))
        snap["label_touched_up"] = (snap["round_high"] > snap["anchor_price"]).astype(int)
        snap["label_touched_down"] = (snap["round_low"] < snap["anchor_price"]).astype(int)
        snap["label_big_round_move_10bps"] = ((snap["max_up_bps"] >= 10.0) | (snap["max_down_bps"] <= -10.0)).astype(int)
        snap["label_big_round_move_20bps"] = ((snap["max_up_bps"] >= 20.0) | (snap["max_down_bps"] <= -20.0)).astype(int)
        all_rows.append(snap)
    matrix = pd.concat(all_rows, ignore_index=True)
    return matrix.sort_values(["timestamp", "horizon_min"]).reset_index(drop=True)


def finalize_matrix(matrix):
    id_cols = ["timestamp", "round_id", "horizon_min", "round_start", "expiry_timestamp",
               "snapshot_idx_in_round", "seconds_elapsed", "seconds_left", "anchor_price", "close",
               "high", "low", "open", "expiry_close", "winner_side", "current_side"]
    label_cols = [c for c in matrix.columns if c.startswith("label_")]
    helper_cols = {"open_time_ms", "close_time_ms", "ignore", "ts_ns", "round_start_ns",
                   "round_end", "round_end_expected", "bars",
                   "future_min_low", "future_max_high", "_inc_min_low", "_inc_max_high"}
    # KEEP the round-outcome regression targets as explicit columns (the bakeoff needs them).
    reg_targets = ["expiry_return_bps", "max_up_bps", "max_down_bps", "round_range_bps",
                   "round_volume", "round_quote_volume", "round_trades"]
    exclude = set(id_cols) | set(label_cols) | helper_cols | set(reg_targets)
    feature_cols = []
    for c in matrix.columns:
        if c in exclude:
            continue
        if pd.api.types.is_numeric_dtype(matrix[c]):
            feature_cols.append(c)
    matrix[feature_cols] = matrix[feature_cols].replace([np.inf, -np.inf], np.nan)
    med = matrix[feature_cols].median(numeric_only=True)
    matrix[feature_cols] = matrix[feature_cols].fillna(med).fillna(0.0)
    nunique = matrix[feature_cols].nunique(dropna=False)
    feature_cols = [c for c in feature_cols if nunique.get(c, 0) > 1]
    keep_cols = [c for c in id_cols if c in matrix.columns] + label_cols + \
                [c for c in reg_targets if c in matrix.columns] + feature_cols
    out = matrix[keep_cols].copy()
    manifest = {
        "id_cols": [c for c in id_cols if c in out.columns],
        "label_cols": label_cols,
        "regression_targets": [c for c in reg_targets if c in out.columns],
        "feature_cols": feature_cols,
        "n_features": [len(feature_cols)],
        "primary_labels": ["label_up_win", "label_down_win", "label_current_side_wins",
                           "label_line_cross_before_expiry", "label_big_round_move_10bps",
                           "label_big_round_move_20bps"],
        "notes": ["Features known at/before snapshot ts.",
                  "Polymarket EV needs live ask; this is BTC fair-value only.",
                  "Use round_id/time splits or purged walk-forward; never random-split."],
    }
    return out, manifest


def build_dataset(symbol, start, end, horizons, out_dir, tie_policy):
    out_dir.mkdir(parents=True, exist_ok=True)
    download_start = start - timedelta(days=3)
    start_ms = utc_ms(download_start); end_ms = utc_ms(end)
    print(f"[download] {symbol} spot 1m {download_start.isoformat()} -> {end.isoformat()}")
    spot = fetch_binance_klines(symbol, "1m", start_ms, end_ms, market="spot")
    if spot.empty:
        raise RuntimeError("No spot klines downloaded")
    print(f"[base] rows={len(spot):,} {spot['timestamp'].min()} -> {spot['timestamp'].max()}")
    df = add_base_market_features(spot)
    df = merge_futures_features(df, symbol, start_ms, end_ms)
    df = add_time_features(df)
    df = add_rolling_features(df)
    df = add_indicator_features(df)
    df = df[(df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] < pd.Timestamp(end))].copy()
    df = ensure_monotonic_index(df, "timestamp")
    print(f"[features] base rows after trim={len(df):,}, cols={len(df.columns):,}")
    print(f"[rounds] building horizons={list(horizons)}")
    rounds = build_round_table(df, horizons=horizons, tie_policy=tie_policy)
    print("[snapshots] building feature matrix")
    matrix_raw = add_anchor_snapshot_features(df, rounds, horizons=horizons)
    matrix, manifest = finalize_matrix(matrix_raw)
    n_features = len(manifest["feature_cols"])
    print(f"[manifest] n_features={n_features}, labels={len(manifest['label_cols'])}")
    features_path = out_dir / "binance_updown_features.parquet"
    rounds_path = out_dir / "binance_updown_rounds.parquet"
    manifest_path = out_dir / "binance_updown_feature_manifest.json"
    matrix.to_parquet(features_path, index=False)
    rounds.to_parquet(rounds_path, index=False)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[write] {features_path} rows={len(matrix):,} cols={len(matrix.columns):,}")
    print(f"[write] {rounds_path} rows={len(rounds):,}")
    print(f"[write] {manifest_path}")
    summary = matrix.groupby("horizon_min").agg(
        rows=("round_id", "size"), rounds=("round_id", "nunique"), up_rate=("label_up_win", "mean"),
        hold_rate=("label_current_side_wins", "mean"), line_cross_rate=("label_line_cross_before_expiry", "mean"),
        big10_rate=("label_big_round_move_10bps", "mean"))
    print("\n[label summary]\n" + summary.to_string())
    return features_path, rounds_path, manifest_path


def main():
    p = argparse.ArgumentParser(description="Build Binance BTC Polymarket-style up/down feature dataset.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--start", type=str, default=None)
    p.add_argument("--end", type=str, default=None)
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 15, 30])
    p.add_argument("--out", type=str, default="data/research")
    p.add_argument("--tie-policy", choices=["down", "up", "neutral"], default="down")
    args = p.parse_args()
    end = parse_date(args.end) if args.end else now_utc_floor_minute()
    start = parse_date(args.start) if args.start else end - timedelta(days=args.days)
    horizons = sorted(set(int(h) for h in args.horizons))
    if any(h <= 0 for h in horizons):
        raise ValueError("--horizons must be positive minutes")
    if start >= end:
        raise ValueError("--start must be before --end")
    build_dataset(args.symbol, start, end, horizons, Path(args.out), args.tie_policy)


if __name__ == "__main__":
    main()
