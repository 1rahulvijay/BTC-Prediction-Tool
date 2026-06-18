#!/usr/bin/env python
"""
Build a leak-safe Binance BTC anchor/up-down research dataset.

This is a standalone research builder. It does not touch live app models or saved
model bundles. It creates Polymarket-style anchor windows from Binance BTCUSDT 1m
spot candles and emits snapshot rows for multi-head model bakeoffs.

Default output:
  data/research/binance_updown_features.parquet
  data/research/binance_updown_rounds.parquet
  data/research/binance_updown_feature_manifest.json
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests


SPOT_BASE = "https://api.binance.com"
ROLL_WINDOWS = (3, 5, 10, 15, 30, 60, 120, 240, 720, 1440)
EMA_SPANS = (5, 10, 20, 50, 100, 200)


def parse_utc(value: str) -> datetime:
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def utc_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def safe_div(a, b, default=0.0):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.where(np.abs(b) > 1e-12, a / b, default)


def normal_cdf(x):
    vec = np.vectorize(lambda z: 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0))))
    return vec(np.asarray(x, dtype=float))


def retry_get(url: str, params: dict, tries: int = 5, timeout: int = 20):
    last = None
    for i in range(tries):
        try:
            res = requests.get(url, params=params, timeout=timeout)
            if res.status_code in (418, 429):
                wait = 1.0 + 0.5 * (2 ** i)
                print(f"[rate-limit] {res.status_code}; sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            res.raise_for_status()
            return res.json()
        except Exception as exc:
            last = exc
            wait = 0.4 * (2 ** i)
            print(f"[retry] {i + 1}/{tries} {url}: {exc}; sleep {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"GET failed: {url} params={params} err={last}")


def fetch_spot_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    url = f"{SPOT_BASE}/api/v3/klines"
    rows = []
    cursor = start_ms
    while cursor < end_ms:
        batch = retry_get(
            url,
            {
                "symbol": symbol,
                "interval": "1m",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        if not batch:
            break
        rows.extend(batch)
        last_open = int(batch[-1][0])
        next_cursor = last_open + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.03)

    if not rows:
        raise RuntimeError("No Binance spot klines returned")

    cols = [
        "open_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time_ms",
        "quote_volume",
        "n_trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]
    df = pd.DataFrame(rows, columns=cols)
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["n_trades"] = pd.to_numeric(df["n_trades"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["open_time_ms"].astype("int64"), unit="ms", utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def load_or_download(symbol: str, start: datetime, end: datetime, cache_path: Path, refresh: bool) -> pd.DataFrame:
    warmup_start = start - timedelta(days=3)
    if cache_path.exists() and not refresh:
        try:
            cached = pd.read_parquet(cache_path)
            cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True)
            if cached["timestamp"].min() <= pd.Timestamp(warmup_start) and cached["timestamp"].max() >= pd.Timestamp(end - timedelta(minutes=1)):
                print(f"[cache] using {cache_path} rows={len(cached):,}")
                return cached[(cached["timestamp"] >= pd.Timestamp(warmup_start)) & (cached["timestamp"] < pd.Timestamp(end))].copy()
        except Exception as exc:
            print(f"[cache] ignored invalid cache: {exc}")

    print(f"[download] {symbol} 1m {warmup_start.isoformat()} -> {end.isoformat()}")
    df = fetch_spot_klines(symbol, utc_ms(warmup_start), utc_ms(end))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    print(f"[cache] wrote {cache_path} rows={len(df):,}")
    return df


def add_market_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["log_close"] = np.log(out["close"])
    out["ret_1m"] = out["log_close"].diff()
    out["ret_1m_bps"] = out["ret_1m"] * 10_000
    out["abs_ret_1m_bps"] = out["ret_1m_bps"].abs()
    out["hl_range_bps"] = (out["high"] / out["low"] - 1.0) * 10_000
    out["oc_body_bps"] = (out["close"] / out["open"] - 1.0) * 10_000
    out["upper_wick_bps"] = (out["high"] / np.maximum(out["open"], out["close"]) - 1.0) * 10_000
    out["lower_wick_bps"] = (np.minimum(out["open"], out["close"]) / out["low"] - 1.0) * 10_000
    out["close_pos_in_bar"] = safe_div(out["close"] - out["low"], out["high"] - out["low"], 0.5)
    out["volume_log"] = np.log1p(out["volume"].clip(lower=0))
    out["quote_volume_log"] = np.log1p(out["quote_volume"].clip(lower=0))
    out["trades_log"] = np.log1p(out["n_trades"].clip(lower=0))
    out["taker_sell_base"] = (out["volume"] - out["taker_buy_base"]).clip(lower=0)
    out["taker_buy_ratio"] = safe_div(out["taker_buy_base"], out["volume"], 0.5)
    out["taker_delta_base"] = out["taker_buy_base"] - out["taker_sell_base"]
    out["taker_delta_ratio"] = safe_div(out["taker_delta_base"], out["volume"], 0.0)
    out["cvd_base"] = out["taker_delta_base"].fillna(0).cumsum()

    ts = out["timestamp"]
    hour = ts.dt.hour + ts.dt.minute / 60.0
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * ts.dt.dayofweek / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * ts.dt.dayofweek / 7.0)
    out["minute_sin"] = np.sin(2 * np.pi * ts.dt.minute / 60.0)
    out["minute_cos"] = np.cos(2 * np.pi * ts.dt.minute / 60.0)
    out["is_weekend"] = (ts.dt.dayofweek >= 5).astype(int)
    out["session_asia"] = ((hour >= 0) & (hour < 8)).astype(int)
    out["session_europe"] = ((hour >= 7) & (hour < 16)).astype(int)
    out["session_us"] = ((hour >= 13) & (hour < 22)).astype(int)

    for span in EMA_SPANS:
        ema = out["close"].ewm(span=span, adjust=False, min_periods=max(3, span // 3)).mean()
        out[f"ema_{span}"] = ema
        out[f"dist_ema_{span}_bps"] = (out["close"] / ema - 1.0) * 10_000
        out[f"ema_slope_{span}_bps"] = (ema / ema.shift(1) - 1.0) * 10_000

    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    for window in (7, 14, 21):
        avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
        avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
        rs = safe_div(avg_gain, avg_loss, 0.0)
        out[f"rsi_{window}"] = 100.0 - (100.0 / (1.0 + rs))

    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - out["close"].shift(1)).abs(),
            (out["low"] - out["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    for window in (7, 14, 30, 60):
        atr = tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
        out[f"atr_{window}_bps"] = atr / out["close"] * 10_000

    for window in ROLL_WINDOWS:
        minp = max(2, window // 3)
        ret = out["ret_1m_bps"]
        roll_high = out["high"].rolling(window, min_periods=minp).max()
        roll_low = out["low"].rolling(window, min_periods=minp).min()
        vol_sum = out["volume"].rolling(window, min_periods=minp).sum()
        buy_sum = out["taker_buy_base"].rolling(window, min_periods=minp).sum()
        sell_sum = out["taker_sell_base"].rolling(window, min_periods=minp).sum()

        out[f"ret_sum_{window}m_bps"] = ret.rolling(window, min_periods=minp).sum()
        out[f"ret_mean_{window}m_bps"] = ret.rolling(window, min_periods=minp).mean()
        out[f"realized_vol_{window}m_bps"] = ret.rolling(window, min_periods=minp).std()
        out[f"range_{window}m_bps"] = (roll_high / roll_low - 1.0) * 10_000
        out[f"pos_in_range_{window}m"] = safe_div(out["close"] - roll_low, roll_high - roll_low, 0.5)
        out[f"volume_sum_{window}m"] = vol_sum
        out[f"quote_volume_sum_{window}m"] = out["quote_volume"].rolling(window, min_periods=minp).sum()
        out[f"trades_sum_{window}m"] = out["n_trades"].rolling(window, min_periods=minp).sum()
        out[f"taker_buy_ratio_{window}m"] = safe_div(buy_sum, vol_sum, 0.5)
        out[f"taker_delta_sum_{window}m"] = out["taker_delta_base"].rolling(window, min_periods=minp).sum()
        out[f"taker_delta_ratio_{window}m"] = safe_div(buy_sum - sell_sum, buy_sum + sell_sum, 0.0)
        out[f"cvd_change_{window}m"] = out["cvd_base"].diff(window)
        out[f"vpin_proxy_{window}m"] = safe_div((buy_sum - sell_sum).abs(), buy_sum + sell_sum, 0.0)
        out[f"volume_z_{window}m"] = safe_div(
            out["volume"] - out["volume"].rolling(window, min_periods=minp).mean(),
            out["volume"].rolling(window, min_periods=minp).std(),
            0.0,
        )
        path_len = ret.abs().rolling(window, min_periods=minp).sum()
        net_move = ret.rolling(window, min_periods=minp).sum().abs()
        out[f"path_efficiency_{window}m"] = safe_div(net_move, path_len, 0.0)

    for short, long in ((3, 15), (5, 30), (15, 60), (30, 120), (60, 240)):
        out[f"rv_ratio_{short}_{long}"] = safe_div(out[f"realized_vol_{short}m_bps"], out[f"realized_vol_{long}m_bps"], 1.0)
        out[f"range_ratio_{short}_{long}"] = safe_div(out[f"range_{short}m_bps"], out[f"range_{long}m_bps"], 1.0)

    for window in (20, 50, 100):
        ma = out["close"].rolling(window, min_periods=max(5, window // 2)).mean()
        sd = out["close"].rolling(window, min_periods=max(5, window // 2)).std()
        out[f"bb_z_{window}"] = safe_div(out["close"] - ma, sd, 0.0)
        out[f"bb_width_{window}_bps"] = safe_div(4.0 * sd, ma, 0.0) * 10_000

    out["shock_1m_vs_60m"] = safe_div(out["abs_ret_1m_bps"], out["realized_vol_60m_bps"], 0.0)
    out["shock_5m_vs_60m"] = safe_div(out["ret_sum_5m_bps"].abs(), out["realized_vol_60m_bps"] * math.sqrt(5), 0.0)
    return out


def build_rounds(df: pd.DataFrame, horizons: list[int], tie_policy: str) -> pd.DataFrame:
    rows = []
    tmp = df[["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "n_trades"]].copy()
    minute_ns = 60 * 1_000_000_000
    tmp["ts_ns"] = tmp["timestamp"].astype("int64")
    for horizon in horizons:
        h_ns = horizon * minute_ns
        cur = tmp.copy()
        cur["horizon_min"] = horizon
        cur["round_start_ns"] = (cur["ts_ns"] // h_ns) * h_ns
        cur["round_start"] = pd.to_datetime(cur["round_start_ns"], utc=True)
        g = cur.groupby("round_start", sort=True)
        r = g.agg(
            anchor_price=("open", "first"),
            expiry_close=("close", "last"),
            round_high=("high", "max"),
            round_low=("low", "min"),
            round_volume=("volume", "sum"),
            round_quote_volume=("quote_volume", "sum"),
            round_trades=("n_trades", "sum"),
            bars=("close", "size"),
        ).reset_index()
        r = r[r["bars"] >= horizon].copy()
        r["horizon_min"] = horizon
        r["expiry_timestamp"] = r["round_start"] + pd.to_timedelta(horizon, unit="m")
        r["expiry_return_bps"] = (r["expiry_close"] / r["anchor_price"] - 1.0) * 10_000
        r["max_up_bps"] = (r["round_high"] / r["anchor_price"] - 1.0) * 10_000
        r["max_down_bps"] = (r["round_low"] / r["anchor_price"] - 1.0) * 10_000
        r["round_range_bps"] = (r["round_high"] / r["round_low"] - 1.0) * 10_000
        if tie_policy == "up":
            r["label_up_win"] = (r["expiry_close"] >= r["anchor_price"]).astype(int)
        elif tie_policy == "neutral":
            r["label_up_win"] = np.where(r["expiry_close"] > r["anchor_price"], 1, np.where(r["expiry_close"] < r["anchor_price"], 0, np.nan))
        else:
            r["label_up_win"] = (r["expiry_close"] > r["anchor_price"]).astype(int)
        r["label_down_win"] = 1 - r["label_up_win"]
        r["winner_side"] = np.where(r["label_up_win"] == 1, "UP", "DOWN")
        r["round_id"] = "BTCUSDT_" + r["horizon_min"].astype(str) + "m_" + r["round_start"].dt.strftime("%Y%m%d%H%M")
        rows.append(r)
    return pd.concat(rows, ignore_index=True).sort_values(["horizon_min", "round_start"]).reset_index(drop=True)


def build_snapshots(features: pd.DataFrame, rounds: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    out_rows = []
    base = features.copy()
    base["ts_ns"] = base["timestamp"].astype("int64")
    minute_ns = 60 * 1_000_000_000
    round_cols = [
        "round_id", "horizon_min", "round_start", "expiry_timestamp", "anchor_price",
        "expiry_close", "round_high", "round_low", "round_volume", "round_quote_volume",
        "round_trades", "expiry_return_bps", "max_up_bps", "max_down_bps", "round_range_bps",
        "label_up_win", "label_down_win", "winner_side",
    ]

    for horizon in horizons:
        h_ns = horizon * minute_ns
        snap = base.copy()
        snap["horizon_min"] = horizon
        snap["round_start_ns"] = (snap["ts_ns"] // h_ns) * h_ns
        snap["round_start"] = pd.to_datetime(snap["round_start_ns"], utc=True)
        r = rounds[rounds["horizon_min"] == horizon][round_cols].copy()
        snap = snap.merge(r, on=["horizon_min", "round_start"], how="inner")
        snap["snapshot_idx_in_round"] = ((snap["timestamp"] - snap["round_start"]).dt.total_seconds() // 60).astype(int)
        snap = snap[(snap["snapshot_idx_in_round"] >= 0) & (snap["snapshot_idx_in_round"] < horizon)].copy()
        snap = snap.sort_values(["round_id", "timestamp"]).reset_index(drop=True)

        g = snap.groupby("round_id", sort=False)
        snap["high_so_far"] = g["high"].cummax()
        snap["low_so_far"] = g["low"].cummin()
        snap["volume_so_far"] = g["volume"].cumsum()
        snap["quote_volume_so_far"] = g["quote_volume"].cumsum()
        snap["trades_so_far"] = g["n_trades"].cumsum()
        snap["future_high_from_snapshot"] = g["high"].transform(lambda s: s.iloc[::-1].cummax().iloc[::-1])
        snap["future_low_from_snapshot"] = g["low"].transform(lambda s: s.iloc[::-1].cummin().iloc[::-1])

        snap["seconds_elapsed"] = (snap["snapshot_idx_in_round"] + 1) * 60
        snap["seconds_left"] = (horizon * 60 - snap["seconds_elapsed"]).clip(lower=0)
        snap["distance_from_anchor_bps"] = (snap["close"] / snap["anchor_price"] - 1.0) * 10_000
        snap["abs_distance_from_anchor_bps"] = snap["distance_from_anchor_bps"].abs()
        snap["current_side"] = np.where(snap["close"] > snap["anchor_price"], 1, np.where(snap["close"] < snap["anchor_price"], -1, 0))
        snap["is_above_anchor"] = (snap["close"] > snap["anchor_price"]).astype(int)
        snap["is_below_anchor"] = (snap["close"] < snap["anchor_price"]).astype(int)
        snap["time_above_frac"] = g["is_above_anchor"].cumsum() / (snap["snapshot_idx_in_round"] + 1)
        snap["time_below_frac"] = g["is_below_anchor"].cumsum() / (snap["snapshot_idx_in_round"] + 1)
        snap["range_so_far_bps"] = (snap["high_so_far"] / snap["low_so_far"] - 1.0) * 10_000
        snap["anchor_path_efficiency"] = safe_div(snap["abs_distance_from_anchor_bps"], snap["range_so_far_bps"], 0.0)

        sign = snap["current_side"].replace(0, np.nan)
        snap["side_ffill"] = g["current_side"].transform(lambda s: s.replace(0, np.nan).ffill().fillna(0))
        snap["side_changed"] = g["side_ffill"].transform(lambda s: ((s != s.shift(1)) & (s.shift(1).fillna(0) != 0) & (s != 0)).astype(int))
        snap["recent_cross_count"] = g["side_changed"].cumsum()
        snap.drop(columns=["side_ffill", "side_changed"], inplace=True)

        local_vol = snap["realized_vol_30m_bps"].fillna(snap["realized_vol_60m_bps"]).replace(0, np.nan)
        sigma = local_vol * np.sqrt(np.maximum(snap["seconds_left"], 1) / 60.0)
        snap["barrier_dz"] = safe_div(snap["abs_distance_from_anchor_bps"], sigma, 0.0)
        snap["analytic_p_hold"] = normal_cdf(snap["barrier_dz"].clip(-8, 8))
        snap["analytic_p_up"] = np.where(snap["current_side"] > 0, snap["analytic_p_hold"], np.where(snap["current_side"] < 0, 1 - snap["analytic_p_hold"], 0.5))
        snap["analytic_p_down"] = 1 - snap["analytic_p_up"]

        snap["label_current_side_wins"] = np.where(
            snap["current_side"] > 0,
            snap["label_up_win"],
            np.where(snap["current_side"] < 0, snap["label_down_win"], np.nan),
        )
        snap["label_line_cross_before_expiry"] = np.where(
            snap["current_side"] > 0,
            (snap["future_low_from_snapshot"] <= snap["anchor_price"]).astype(int),
            np.where(snap["current_side"] < 0, (snap["future_high_from_snapshot"] >= snap["anchor_price"]).astype(int), np.nan),
        )
        snap["label_big_round_move_10bps"] = ((snap["max_up_bps"] >= 10.0) | (snap["max_down_bps"] <= -10.0)).astype(int)
        snap["label_big_round_move_20bps"] = ((snap["max_up_bps"] >= 20.0) | (snap["max_down_bps"] <= -20.0)).astype(int)

        # Model-ready aliases requested in the design notes.
        snap["target_up_win"] = snap["label_up_win"]
        snap["target_current_side_hold"] = snap["label_current_side_wins"]
        snap["target_line_cross"] = snap["label_line_cross_before_expiry"]
        snap["target_big_move_10bps"] = snap["label_big_round_move_10bps"]
        snap["target_big_move_20bps"] = snap["label_big_round_move_20bps"]
        snap["target_expiry_return_bps"] = snap["expiry_return_bps"]
        snap["target_max_up_bps"] = snap["max_up_bps"]
        snap["target_max_down_bps"] = snap["max_down_bps"]
        snap["target_range_bps"] = snap["round_range_bps"]
        snap["target_log_volume"] = np.log1p(snap["round_volume"].clip(lower=0))
        snap["target_log_quote_volume"] = np.log1p(snap["round_quote_volume"].clip(lower=0))
        snap["target_log_trades"] = np.log1p(snap["round_trades"].clip(lower=0))

        out_rows.append(snap)

    return pd.concat(out_rows, ignore_index=True).sort_values(["timestamp", "horizon_min"]).reset_index(drop=True)


def finalize(matrix: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    id_cols = [
        "timestamp", "round_id", "horizon_min", "round_start", "expiry_timestamp",
        "snapshot_idx_in_round", "seconds_elapsed", "seconds_left", "anchor_price",
        "open", "high", "low", "close", "expiry_close", "winner_side", "current_side",
    ]
    label_cols = [c for c in matrix.columns if c.startswith("label_")]
    target_cols = [c for c in matrix.columns if c.startswith("target_")]
    future_cols = {
        "round_high", "round_low", "round_volume", "round_quote_volume", "round_trades",
        "expiry_return_bps", "max_up_bps", "max_down_bps", "round_range_bps",
        "future_high_from_snapshot", "future_low_from_snapshot",
    }
    helper_cols = {"open_time_ms", "close_time_ms", "ignore", "ts_ns", "round_start_ns"}
    exclude = set(id_cols) | set(label_cols) | set(target_cols) | future_cols | helper_cols
    feature_cols = [
        c for c in matrix.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(matrix[c])
    ]
    matrix[feature_cols] = matrix[feature_cols].replace([np.inf, -np.inf], np.nan)
    med = matrix[feature_cols].median(numeric_only=True)
    matrix[feature_cols] = matrix[feature_cols].fillna(med).fillna(0.0)
    nunique = matrix[feature_cols].nunique(dropna=False)
    feature_cols = [c for c in feature_cols if int(nunique.get(c, 0)) > 1]
    keep = [c for c in id_cols if c in matrix.columns] + label_cols + target_cols + feature_cols
    out = matrix[keep].copy()
    manifest = {
        "id_cols": [c for c in id_cols if c in out.columns],
        "label_cols": label_cols,
        "target_cols": target_cols,
        "feature_cols": feature_cols,
        "n_features": len(feature_cols),
        "notes": [
            "Chronological or round-grouped splits only. Never random-split snapshot rows.",
            "Features use data available at or before snapshot timestamp.",
            "Profitability still requires live market ask/spread/fees comparison.",
        ],
    }
    return out, manifest


def build_dataset(args) -> tuple[Path, Path, Path]:
    end = parse_utc(args.end) if args.end else datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = parse_utc(args.start) if args.start else end - timedelta(days=args.days)
    horizons = sorted(set(int(h) for h in args.horizons))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / f"{args.symbol.lower()}_1m_cache.parquet"

    raw = load_or_download(args.symbol, start, end, cache_path, args.refresh_cache)
    raw = raw[(raw["timestamp"] >= pd.Timestamp(start - timedelta(days=3))) & (raw["timestamp"] < pd.Timestamp(end))].copy()
    features = add_market_features(raw)
    features = features[(features["timestamp"] >= pd.Timestamp(start)) & (features["timestamp"] < pd.Timestamp(end))].copy()
    print(f"[features] rows={len(features):,} cols={len(features.columns):,}")

    rounds = build_rounds(features, horizons, args.tie_policy)
    matrix_raw = build_snapshots(features, rounds, horizons)
    matrix, manifest = finalize(matrix_raw)

    feature_path = out_dir / "binance_updown_features.parquet"
    rounds_path = out_dir / "binance_updown_rounds.parquet"
    manifest_path = out_dir / "binance_updown_feature_manifest.json"
    matrix.to_parquet(feature_path, index=False)
    rounds.to_parquet(rounds_path, index=False)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    summary = matrix.groupby("horizon_min").agg(
        rows=("round_id", "size"),
        rounds=("round_id", "nunique"),
        up_rate=("label_up_win", "mean"),
        hold_rate=("label_current_side_wins", "mean"),
        line_cross_rate=("label_line_cross_before_expiry", "mean"),
        big10_rate=("label_big_round_move_10bps", "mean"),
    )
    print(summary.to_string())
    print(f"[write] {feature_path} rows={len(matrix):,} features={manifest['n_features']}")
    print(f"[write] {rounds_path} rows={len(rounds):,}")
    print(f"[write] {manifest_path}")
    return feature_path, rounds_path, manifest_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 15, 30])
    parser.add_argument("--out", default="data/research")
    parser.add_argument("--tie-policy", choices=["down", "up", "neutral"], default="down")
    parser.add_argument("--refresh-cache", action="store_true")
    args = parser.parse_args()
    build_dataset(args)


if __name__ == "__main__":
    main()
