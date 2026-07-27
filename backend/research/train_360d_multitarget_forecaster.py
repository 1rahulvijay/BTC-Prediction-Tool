#!/usr/bin/env python
"""
Research-only 360-day BTC multi-target forecaster.

This script is intentionally separate from the live BTC/Polymarket app. It downloads
or reuses cached Binance 1m data, builds leak-safe features, trains multiple model
families for 5m/15m forecasting targets, and writes metrics/predictions under
data/research plus research-only model artifacts under data/saved_models.

It does not modify production models, DuckDB state, live app decision logic, or the
Polymarket recorder.
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import pickle
import time
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = ROOT / "data" / "research"
CACHE_DIR = RESEARCH_DIR / "forecast_360d_cache"
MODEL_DIR = ROOT / "data" / "saved_models" / "research_360d_forecaster"
LOG_DIR = ROOT / "data" / "logs"

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

REGRESSION_TARGET_KINDS = ["return", "price", "high", "low", "range", "log_volume"]
CLASSIFICATION_TARGET_KINDS = ["direction", "big_move"]
QUANTILE_TARGET_KINDS = ["return", "high", "low", "range"]


@dataclass
class RunConfig:
    symbol: str
    days: int
    horizons: list[int]
    models: list[str]
    output_prefix: str
    start: str | None
    end: str | None
    smoke: bool
    rebuild_cache: bool
    max_features: int
    max_train_rows: int | None
    n_jobs: int
    device: str
    save_models: bool
    skip_regression: bool
    skip_classification: bool
    skip_quantile: bool
    skip_sequence: bool
    quantile_backends: list[str]
    include_sequence: bool
    sequence_targets: str
    seq_len: int
    seq_max_features: int
    seq_max_rows: int
    seq_epochs: int
    seq_batch_size: int


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def output_path(config: RunConfig, suffix: str) -> Path:
    return RESEARCH_DIR / f"{config.output_prefix}_{suffix}"


def utc_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def now_utc_floor_minute() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def parse_dt(s: str) -> datetime:
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def safe_div(a: Any, b: Any, default: float = 0.0) -> Any:
    return np.where(np.abs(b) > 1e-12, a / b, default)


def retry_get(url: str, params: dict[str, Any], timeout: int = 20, tries: int = 5) -> Any:
    last_err = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (418, 429):
                wait = 1.0 + 0.5 * (2 ** i)
                log(f"[rate-limit] {r.status_code} {url}; sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_err = exc
            wait = 0.4 * (2 ** i)
            log(f"[retry] {i + 1}/{tries} {url} failed: {exc}; sleep {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {tries} tries: {url} params={params} err={last_err}")


def read_frame(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, parse_dates=["timestamp"])


def write_frame(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception as exc:
        csv_path = path.with_suffix(".csv")
        log(f"[warn] parquet write failed for {path.name}: {exc}; writing {csv_path.name}")
        df.to_csv(csv_path, index=False)
        return csv_path


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int, market: str) -> pd.DataFrame:
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

    rows: list[list[Any]] = []
    cursor = start_ms
    batch_count = 0
    while cursor < end_ms:
        batch = retry_get(
            url,
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        if not batch:
            break
        rows.extend(batch)
        batch_count += 1
        cursor_next = int(batch[-1][0]) + 60_000
        if batch_count == 1 or batch_count % 25 == 0:
            current_ts = datetime.fromtimestamp(int(batch[-1][0]) / 1000, tz=timezone.utc)
            log(f"[download] {market} progress batches={batch_count} rows={len(rows):,} through={current_ts.isoformat()}")
        if cursor_next <= cursor:
            break
        cursor = cursor_next
        if len(batch) < 1000:
            break
        time.sleep(0.05)

    if not rows:
        return pd.DataFrame()

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
    df = pd.DataFrame(rows, columns=cols[: len(rows[0])])
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "n_trades" in df.columns:
        df["n_trades"] = pd.to_numeric(df["n_trades"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["open_time_ms"].astype("int64"), unit="ms", utc=True)
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_funding_rates(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cursor = start_ms
    url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
    batch_count = 0
    while cursor < end_ms:
        batch = retry_get(url, {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
        if not batch:
            break
        rows.extend(batch)
        batch_count += 1
        log(f"[download] funding progress batches={batch_count} rows={len(rows):,}")
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


def cached_fetch_klines(symbol: str, start: datetime, end: datetime, market: str, rebuild: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{symbol}_{market}_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.parquet"
    path = CACHE_DIR / key
    if path.exists() and not rebuild:
        log(f"[cache] using {path.name}")
        return read_frame(path)
    log(f"[download] {symbol} {market} 1m {start.isoformat()} -> {end.isoformat()}")
    df = fetch_klines(symbol, "1m", utc_ms(start), utc_ms(end), market)
    if df.empty:
        log(f"[warn] no {market} rows downloaded")
        return df
    write_frame(df, path)
    return df


def cached_fetch_funding(symbol: str, start: datetime, end: datetime, rebuild: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}_funding_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.parquet"
    if path.exists() and not rebuild:
        log(f"[cache] using {path.name}")
        return read_frame(path)
    log(f"[download] {symbol} funding {start.isoformat()} -> {end.isoformat()}")
    try:
        df = fetch_funding_rates(symbol, utc_ms(start), utc_ms(end))
    except Exception as exc:
        log(f"[warn] funding download failed: {exc}")
        df = pd.DataFrame(columns=["timestamp", "funding_rate"])
    write_frame(df, path)
    return df


def merge_optional_market(base: pd.DataFrame, other: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if other.empty:
        return base
    keep = ["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "n_trades", "taker_buy_base"]
    cols = [c for c in keep if c in other.columns]
    renamed = other[cols].rename(columns={c: f"{prefix}_{c}" for c in cols if c != "timestamp"})
    return base.merge(renamed, on="timestamp", how="left")


def build_market_frame(config: RunConfig) -> pd.DataFrame:
    end = parse_dt(config.end) if config.end else now_utc_floor_minute()
    start = parse_dt(config.start) if config.start else end - timedelta(days=config.days)
    warm_start = start - timedelta(days=3)

    spot = cached_fetch_klines(config.symbol, warm_start, end, "spot", config.rebuild_cache)
    if spot.empty:
        raise RuntimeError("No Binance spot klines downloaded. Cannot build research frame.")
    fut = cached_fetch_klines(config.symbol, warm_start, end, "futures", config.rebuild_cache)
    mark = cached_fetch_klines(config.symbol, warm_start, end, "mark", config.rebuild_cache)
    premium = cached_fetch_klines(config.symbol, warm_start, end, "premium", config.rebuild_cache)
    funding = cached_fetch_funding(config.symbol, warm_start, end, config.rebuild_cache)

    base_cols = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "n_trades",
        "taker_buy_base",
        "taker_buy_quote",
    ]
    df = spot[[c for c in base_cols if c in spot.columns]].copy()
    df = merge_optional_market(df, fut, "futures")
    df = merge_optional_market(df, mark, "mark")
    df = merge_optional_market(df, premium, "premium")
    if not funding.empty:
        df = pd.merge_asof(
            df.sort_values("timestamp"),
            funding.sort_values("timestamp"),
            on="timestamp",
            direction="backward",
            tolerance=pd.Timedelta(hours=12),
        )
    else:
        df["funding_rate"] = np.nan

    df = df[(df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] < pd.Timestamp(end))].copy()
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    log(f"[data] market frame rows={len(df):,} cols={len(df.columns):,}")
    return df


def rsi(series: pd.Series, window: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def add_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    out = df.copy()
    out["log_close"] = np.log(out["close"])
    out["ret_1m_bps"] = out["log_close"].diff() * 10_000.0
    out["abs_ret_1m_bps"] = out["ret_1m_bps"].abs()
    out["hl_range_bps"] = (out["high"] / out["low"] - 1.0) * 10_000.0
    out["oc_body_bps"] = (out["close"] / out["open"] - 1.0) * 10_000.0
    out["upper_wick_bps"] = (out["high"] / np.maximum(out["open"], out["close"]) - 1.0) * 10_000.0
    out["lower_wick_bps"] = (np.minimum(out["open"], out["close"]) / out["low"] - 1.0) * 10_000.0
    out["close_pos_in_bar"] = safe_div(out["close"] - out["low"], out["high"] - out["low"], 0.5)
    out["volume_log"] = np.log1p(out["volume"].clip(lower=0))
    out["quote_volume_log"] = np.log1p(out["quote_volume"].clip(lower=0))
    out["trades_log"] = np.log1p(out["n_trades"].clip(lower=0))
    out["taker_buy_ratio"] = safe_div(out["taker_buy_base"], out["volume"], 0.5)
    out["taker_delta_base"] = 2 * out["taker_buy_base"].fillna(0) - out["volume"].fillna(0)
    out["taker_delta_ratio"] = safe_div(out["taker_delta_base"], out["volume"], 0.0)
    out["cvd_base"] = out["taker_delta_base"].fillna(0).cumsum()

    hour = out["timestamp"].dt.hour + out["timestamp"].dt.minute / 60.0
    dow = out["timestamp"].dt.dayofweek
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    out["is_weekend"] = (dow >= 5).astype(int)
    out["session_asia"] = ((hour >= 0) & (hour < 8)).astype(int)
    out["session_europe"] = ((hour >= 7) & (hour < 16)).astype(int)
    out["session_us"] = ((hour >= 13) & (hour < 22)).astype(int)

    if "futures_close" in out.columns:
        out["futures_basis_bps"] = (out["futures_close"] / out["close"] - 1.0) * 10_000.0
        out["futures_ret_spread_bps"] = np.log(out["futures_close"]).diff() * 10_000.0 - out["ret_1m_bps"]
        out["futures_taker_buy_ratio"] = safe_div(out.get("futures_taker_buy_base", 0), out.get("futures_volume", 0), 0.5)
    if "mark_close" in out.columns:
        out["mark_basis_bps"] = (out["mark_close"] / out["close"] - 1.0) * 10_000.0
    if "premium_close" in out.columns:
        out["premium_index"] = out["premium_close"]
    out["funding_rate"] = out["funding_rate"].ffill().fillna(0.0)
    out["funding_rate_change"] = out["funding_rate"].diff().fillna(0.0)
    minutes_to_funding = (480 - ((out["timestamp"].dt.hour * 60 + out["timestamp"].dt.minute) % 480)) % 480
    out["hours_to_funding"] = minutes_to_funding / 60.0
    out["funding_clock_sin"] = np.sin(2 * np.pi * minutes_to_funding / 480.0)
    out["funding_clock_cos"] = np.cos(2 * np.pi * minutes_to_funding / 480.0)

    for w in [3, 5, 10, 15, 30, 60, 120, 240]:
        mp = max(2, min(w, w // 3))
        ret = out["ret_1m_bps"]
        out[f"ret_sum_{w}m_bps"] = ret.rolling(w, min_periods=mp).sum()
        out[f"ret_mean_{w}m_bps"] = ret.rolling(w, min_periods=mp).mean()
        out[f"realized_vol_{w}m_bps"] = ret.rolling(w, min_periods=mp).std()
        high = out["high"].rolling(w, min_periods=mp).max()
        low = out["low"].rolling(w, min_periods=mp).min()
        out[f"range_{w}m_bps"] = (high / low - 1.0) * 10_000.0
        out[f"pos_in_range_{w}m"] = safe_div(out["close"] - low, high - low, 0.5)
        out[f"volume_sum_{w}m"] = out["volume"].rolling(w, min_periods=mp).sum()
        out[f"volume_z_{w}m"] = safe_div(
            out["volume"] - out["volume"].rolling(w, min_periods=mp).mean(),
            out["volume"].rolling(w, min_periods=mp).std(),
            0.0,
        )
        out[f"quote_volume_z_{w}m"] = safe_div(
            out["quote_volume"] - out["quote_volume"].rolling(w, min_periods=mp).mean(),
            out["quote_volume"].rolling(w, min_periods=mp).std(),
            0.0,
        )
        out[f"trade_count_z_{w}m"] = safe_div(
            out["n_trades"] - out["n_trades"].rolling(w, min_periods=mp).mean(),
            out["n_trades"].rolling(w, min_periods=mp).std(),
            0.0,
        )
        out[f"taker_delta_sum_{w}m"] = out["taker_delta_base"].rolling(w, min_periods=mp).sum()
        out[f"taker_delta_ratio_{w}m"] = safe_div(
            out["taker_delta_base"].rolling(w, min_periods=mp).sum(),
            out["volume"].rolling(w, min_periods=mp).sum(),
            0.0,
        )
        out[f"cvd_change_{w}m"] = out["cvd_base"].diff(w)
        path = ret.abs().rolling(w, min_periods=mp).sum()
        out[f"path_efficiency_{w}m"] = safe_div(out[f"ret_sum_{w}m_bps"].abs(), path, 0.0)
        buy = out["taker_buy_base"].rolling(w, min_periods=mp).sum()
        sell = (out["volume"] - out["taker_buy_base"]).rolling(w, min_periods=mp).sum()
        out[f"vpin_proxy_{w}m"] = safe_div((buy - sell).abs(), buy + sell, 0.0)
        if "futures_basis_bps" in out.columns:
            out[f"basis_z_{w}m"] = safe_div(
                out["futures_basis_bps"] - out["futures_basis_bps"].rolling(w, min_periods=mp).mean(),
                out["futures_basis_bps"].rolling(w, min_periods=mp).std(),
                0.0,
            )

    for span in [5, 10, 20, 50, 100, 200]:
        ema = out["close"].ewm(span=span, adjust=False).mean()
        out[f"ema_dist_{span}_bps"] = (out["close"] / ema - 1.0) * 10_000.0
        out[f"ema_slope_{span}_bps"] = np.log(ema).diff() * 10_000.0

    ema12 = out["close"].ewm(span=12, adjust=False).mean()
    ema26 = out["close"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["macd_bps"] = macd / out["close"] * 10_000.0
    out["macd_signal_bps"] = macd.ewm(span=9, adjust=False).mean() / out["close"] * 10_000.0
    out["macd_hist_bps"] = out["macd_bps"] - out["macd_signal_bps"]

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    for w in [7, 14, 30, 60]:
        out[f"atr_{w}_bps"] = tr.rolling(w, min_periods=max(2, w // 3)).mean() / out["close"] * 10_000.0
    for w in [7, 14, 21]:
        out[f"rsi_{w}"] = rsi(out["close"], w)
    for w in [20, 50, 100]:
        mid = out["close"].rolling(w, min_periods=max(5, w // 2)).mean()
        sd = out["close"].rolling(w, min_periods=max(5, w // 2)).std()
        out[f"bollinger_width_{w}_bps"] = safe_div(4 * sd, mid, 0.0) * 10_000.0
        high = out["high"].rolling(w, min_periods=max(5, w // 2)).max()
        low = out["low"].rolling(w, min_periods=max(5, w // 2)).min()
        out[f"donchian_width_{w}_bps"] = (high / low - 1.0) * 10_000.0

    out["rv_ratio_5_60"] = safe_div(out["realized_vol_5m_bps"], out["realized_vol_60m_bps"], 1.0)
    out["rv_ratio_15_120"] = safe_div(out["realized_vol_15m_bps"], out["realized_vol_120m_bps"], 1.0)
    out["volatility_shock"] = safe_div(out["realized_vol_15m_bps"], out["realized_vol_240m_bps"], 1.0)
    out["range_compression"] = safe_div(out["range_15m_bps"], out["range_120m_bps"], 1.0)

    id_cols = {
        "timestamp",
        "open_time_ms",
        "close_time_ms",
        "ignore",
    }
    feature_cols = [
        c
        for c in out.columns
        if c not in id_cols and pd.api.types.is_numeric_dtype(out[c]) and not c.startswith("target_")
    ]
    out[feature_cols] = out[feature_cols].replace([np.inf, -np.inf], np.nan)
    return out, feature_cols


def add_targets(df: pd.DataFrame, horizons: list[int]) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    out = df.copy()
    targets: dict[str, list[str]] = {"regression": [], "classification": [], "quantile": []}
    for h in horizons:
        future_close = out["close"].shift(-h)
        future_high = out["high"].shift(-1).rolling(h, min_periods=h).max().shift(-(h - 1))
        future_low = out["low"].shift(-1).rolling(h, min_periods=h).min().shift(-(h - 1))
        future_vol = out["volume"].shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1))
        out[f"target_return_{h}m_bps"] = np.log(future_close / out["close"]) * 10_000.0
        out[f"target_price_{h}m"] = future_close
        out[f"target_high_{h}m_bps"] = (future_high / out["close"] - 1.0) * 10_000.0
        out[f"target_low_{h}m_bps"] = (future_low / out["close"] - 1.0) * 10_000.0
        out[f"target_range_{h}m_bps"] = out[f"target_high_{h}m_bps"] - out[f"target_low_{h}m_bps"]
        out[f"target_log_volume_{h}m"] = np.log1p(future_vol.clip(lower=0))
        dyn_thr = out[f"target_return_{h}m_bps"].abs().rolling(1440, min_periods=240).median()
        floor = 10.0 if h <= 5 else 15.0
        big_move_thr = np.maximum(floor, dyn_thr.fillna(floor))
        out[f"target_direction_{h}m"] = (out[f"target_return_{h}m_bps"] > 0).astype(float)
        out[f"target_big_move_{h}m"] = (out[f"target_return_{h}m_bps"].abs() >= big_move_thr).astype(float)
        targets["regression"].extend(
            [
                f"target_return_{h}m_bps",
                f"target_price_{h}m",
                f"target_high_{h}m_bps",
                f"target_low_{h}m_bps",
                f"target_range_{h}m_bps",
                f"target_log_volume_{h}m",
            ]
        )
        targets["classification"].extend([f"target_direction_{h}m", f"target_big_move_{h}m"])
        targets["quantile"].extend(
            [
                f"target_return_{h}m_bps",
                f"target_high_{h}m_bps",
                f"target_low_{h}m_bps",
                f"target_range_{h}m_bps",
            ]
        )
    return out, targets


def chronological_splits(n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dev_end = int(n * 0.80)
    train_end = int(dev_end * 0.80)
    idx = np.arange(n)
    return idx[:train_end], idx[train_end:dev_end], idx[dev_end:]


def select_features(df: pd.DataFrame, feature_cols: list[str], train_idx: np.ndarray, max_features: int) -> list[str]:
    clean = []
    for c in feature_cols:
        s = df.iloc[train_idx][c]
        if s.notna().sum() > 100 and s.nunique(dropna=True) > 1:
            clean.append(c)
    if max_features and len(clean) > max_features:
        var = df.iloc[train_idx][clean].replace([np.inf, -np.inf], np.nan).fillna(0.0).var().sort_values(ascending=False)
        clean = var.head(max_features).index.tolist()
    return clean


def prepare_xy(
    df: pd.DataFrame,
    feature_cols: list[str],
    target: str,
    train_idx: np.ndarray,
    cal_idx: np.ndarray,
    test_idx: np.ndarray,
    max_train_rows: int | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]:
    use_cols = ["timestamp", target] + feature_cols
    work = df.iloc[np.concatenate([train_idx, cal_idx, test_idx])][use_cols].copy()
    work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=[target]).reset_index()
    boundaries = {
        "train": set(train_idx.tolist()),
        "cal": set(cal_idx.tolist()),
        "test": set(test_idx.tolist()),
    }
    work["split"] = np.where(
        work["index"].isin(boundaries["train"]),
        "train",
        np.where(work["index"].isin(boundaries["cal"]), "cal", "test"),
    )
    med = work.loc[work["split"] == "train", feature_cols].median(numeric_only=True)
    work[feature_cols] = work[feature_cols].fillna(med).fillna(0.0)

    tr = work[work["split"] == "train"]
    if max_train_rows and len(tr) > max_train_rows:
        tr = tr.tail(max_train_rows)
    cal = work[work["split"] == "cal"]
    te = work[work["split"] == "test"]
    return (
        tr[feature_cols].values.astype(np.float32),
        tr[target].values,
        cal[feature_cols].values.astype(np.float32),
        cal[target].values,
        te[feature_cols].values.astype(np.float32),
        te[target].values,
        te[["timestamp", target]].rename(columns={target: "y_true"}).reset_index(drop=True),
    )


def parse_models(model_arg: str) -> list[str]:
    if model_arg.lower() in {"all", "core"}:
        return ["ridge", "elasticnet", "histgb", "rf", "extra_trees", "logistic", "lightgbm", "xgboost", "catboost"]
    return [m.strip().lower() for m in model_arg.split(",") if m.strip()]


def regression_models(selected: list[str], n_jobs: int, device: str) -> dict[str, Any]:
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import ElasticNet, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    models: dict[str, Any] = {}
    if "ridge" in selected:
        models["ridge"] = Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=2.0))])
    if "elasticnet" in selected:
        models["elasticnet"] = Pipeline(
            [("scaler", StandardScaler()), ("model", ElasticNet(alpha=0.0005, l1_ratio=0.15, max_iter=3000))]
        )
    if "histgb" in selected:
        models["histgb"] = HistGradientBoostingRegressor(max_iter=220, max_leaf_nodes=31, learning_rate=0.05, l2_regularization=0.1)
    if "rf" in selected:
        models["rf"] = RandomForestRegressor(n_estimators=160, max_depth=10, min_samples_leaf=20, n_jobs=n_jobs, random_state=42)
    if "extra_trees" in selected or "extratrees" in selected:
        models["extra_trees"] = ExtraTreesRegressor(n_estimators=220, max_depth=12, min_samples_leaf=20, n_jobs=n_jobs, random_state=42)
    if "lightgbm" in selected:
        try:
            from lightgbm import LGBMRegressor

            params = {"n_estimators": 350, "max_depth": 6, "learning_rate": 0.035, "n_jobs": n_jobs, "verbose": -1}
            if tree_gpu_enabled(device):
                params.update({"device_type": "gpu"})
            models["lightgbm"] = LGBMRegressor(**params)
        except Exception as exc:
            log(f"[skip] LightGBM regressor unavailable: {exc}")
    if "xgboost" in selected or "xgb" in selected:
        try:
            from xgboost import XGBRegressor

            params = {
                "n_estimators": 350,
                "max_depth": 5,
                "learning_rate": 0.035,
                "subsample": 0.85,
                "colsample_bytree": 0.85,
                "tree_method": "hist",
                "n_jobs": n_jobs,
                "objective": "reg:squarederror",
                "verbosity": 0,
            }
            if tree_gpu_enabled(device):
                params["device"] = "cuda"
            models["xgboost"] = XGBRegressor(**params)
        except Exception as exc:
            log(f"[skip] XGBoost regressor unavailable: {exc}")
    if "catboost" in selected or "cat" in selected:
        try:
            from catboost import CatBoostRegressor

            params = {"iterations": 350, "depth": 6, "learning_rate": 0.04, "loss_function": "RMSE", "verbose": False}
            if tree_gpu_enabled(device):
                params.update({"task_type": "GPU", "devices": "0"})
            models["catboost"] = CatBoostRegressor(**params)
        except Exception as exc:
            log(f"[skip] CatBoost regressor unavailable: {exc}")
    return models


def classification_models(selected: list[str], n_jobs: int, device: str) -> dict[str, Any]:
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    models: dict[str, Any] = {}
    if "logistic" in selected:
        models["logistic"] = Pipeline(
            [("scaler", StandardScaler()), ("model", LogisticRegression(max_iter=1500, class_weight="balanced"))]
        )
    if "histgb" in selected:
        models["histgb"] = HistGradientBoostingClassifier(max_iter=220, max_leaf_nodes=31, learning_rate=0.05, l2_regularization=0.1)
    if "rf" in selected:
        models["rf"] = RandomForestClassifier(
            n_estimators=160,
            max_depth=10,
            min_samples_leaf=20,
            class_weight="balanced_subsample",
            n_jobs=n_jobs,
            random_state=42,
        )
    if "extra_trees" in selected or "extratrees" in selected:
        models["extra_trees"] = ExtraTreesClassifier(
            n_estimators=220,
            max_depth=12,
            min_samples_leaf=20,
            class_weight="balanced",
            n_jobs=n_jobs,
            random_state=42,
        )
    if "lightgbm" in selected:
        try:
            from lightgbm import LGBMClassifier

            params = {"n_estimators": 350, "max_depth": 6, "learning_rate": 0.035, "n_jobs": n_jobs, "verbose": -1}
            if tree_gpu_enabled(device):
                params.update({"device_type": "gpu"})
            models["lightgbm"] = LGBMClassifier(**params)
        except Exception as exc:
            log(f"[skip] LightGBM classifier unavailable: {exc}")
    if "xgboost" in selected or "xgb" in selected:
        try:
            from xgboost import XGBClassifier

            params = {
                "n_estimators": 350,
                "max_depth": 5,
                "learning_rate": 0.035,
                "subsample": 0.85,
                "colsample_bytree": 0.85,
                "tree_method": "hist",
                "n_jobs": n_jobs,
                "eval_metric": "logloss",
                "verbosity": 0,
            }
            if tree_gpu_enabled(device):
                params["device"] = "cuda"
            models["xgboost"] = XGBClassifier(**params)
        except Exception as exc:
            log(f"[skip] XGBoost classifier unavailable: {exc}")
    if "catboost" in selected or "cat" in selected:
        try:
            from catboost import CatBoostClassifier

            params = {"iterations": 350, "depth": 6, "learning_rate": 0.04, "loss_function": "Logloss", "verbose": False}
            if tree_gpu_enabled(device):
                params.update({"task_type": "GPU", "devices": "0"})
            models["catboost"] = CatBoostClassifier(**params)
        except Exception as exc:
            log(f"[skip] CatBoost classifier unavailable: {exc}")
    return models


def quantile_models(selected: list[str], quantile: float, n_jobs: int, device: str, backends: list[str]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    if "gbr" in backends:
        from sklearn.ensemble import GradientBoostingRegressor

        models[f"gbr_q{int(quantile * 100)}"] = GradientBoostingRegressor(
            loss="quantile",
            alpha=quantile,
            n_estimators=220,
            max_depth=4,
            learning_rate=0.045,
            random_state=42,
        )
    if "lightgbm" in selected and "lightgbm" in backends:
        try:
            from lightgbm import LGBMRegressor

            params = {
                "objective": "quantile",
                "alpha": quantile,
                "n_estimators": 260,
                "max_depth": 5,
                "learning_rate": 0.04,
                "n_jobs": n_jobs,
                "verbose": -1,
            }
            if tree_gpu_enabled(device):
                params.update({"device_type": "gpu"})
            models[f"lightgbm_q{int(quantile * 100)}"] = LGBMRegressor(**params)
        except Exception:
            pass
    return models


def pinball(y_true: np.ndarray, pred: np.ndarray, q: float) -> float:
    diff = y_true - pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def safe_corr(y_true: np.ndarray, pred: np.ndarray, method: str) -> float:
    if len(y_true) < 3 or np.nanstd(y_true) < 1e-12 or np.nanstd(pred) < 1e-12:
        return float("nan")
    if method == "pearson":
        return float(np.corrcoef(y_true, pred)[0, 1])
    return float(pd.Series(y_true).corr(pd.Series(pred), method="spearman"))


def top_conf_accuracy(y_true: np.ndarray, pred: np.ndarray, pct: float) -> float:
    n = len(y_true)
    if n == 0:
        return float("nan")
    k = max(1, int(n * pct))
    order = np.argsort(np.abs(pred))[::-1][:k]
    return float((np.sign(pred[order]) == np.sign(y_true[order])).mean())


def regression_metric_row(model: str, target: str, y_true: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    err = pred - y_true
    row = {
        "model_name": model,
        "target_name": target,
        "horizon": extract_horizon(target),
        "n": len(y_true),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err * err))),
        "pearson": safe_corr(y_true, pred, "pearson"),
        "spearman": safe_corr(y_true, pred, "spearman"),
        "sign_accuracy": float("nan"),
        "top_1_conf_accuracy": float("nan"),
        "top_5_conf_accuracy": float("nan"),
        "top_10_conf_accuracy": float("nan"),
    }
    if "return" in target:
        row["sign_accuracy"] = float((np.sign(pred) == np.sign(y_true)).mean())
        row["top_1_conf_accuracy"] = top_conf_accuracy(y_true, pred, 0.01)
        row["top_5_conf_accuracy"] = top_conf_accuracy(y_true, pred, 0.05)
        row["top_10_conf_accuracy"] = top_conf_accuracy(y_true, pred, 0.10)
    return row


def classification_metric_row(model: str, target: str, y_true: np.ndarray, prob: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, log_loss, precision_score, recall_score, roc_auc_score

    y = y_true.astype(int)
    p = np.clip(prob.astype(float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    conf = np.abs(p - 0.5)

    def top_precision(pct: float) -> float:
        if len(y) == 0:
            return float("nan")
        k = max(1, int(len(y) * pct))
        idx = np.argsort(conf)[::-1][:k]
        return float(precision_score(y[idx], pred[idx], zero_division=0))

    return {
        "model_name": model,
        "target_name": target,
        "horizon": extract_horizon(target),
        "n": len(y),
        "base_rate": float(np.mean(y)),
        "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "logloss": float(log_loss(y, p, labels=[0, 1])),
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "top_1_conf_precision": top_precision(0.01),
        "top_5_conf_precision": top_precision(0.05),
        "top_10_conf_precision": top_precision(0.10),
        "ece_10bucket": expected_calibration_error(y, p, 10),
    }


def expected_calibration_error(y_true: np.ndarray, prob: np.ndarray, buckets: int) -> float:
    y = y_true.astype(float)
    p = np.clip(prob.astype(float), 0, 1)
    edges = np.linspace(0, 1, buckets + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if mask.any():
            ece += float(mask.mean() * abs(y[mask].mean() - p[mask].mean()))
    return ece


def extract_horizon(target: str) -> int:
    for part in target.split("_"):
        if part.endswith("m") and part[:-1].isdigit():
            return int(part[:-1])
    return 0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def append_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


def free_memory(*objs: Any) -> None:
    for obj in objs:
        del obj
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def tree_gpu_enabled(device: str) -> bool:
    return device.lower() in {"gpu", "cuda"}


def sequence_device(requested: str) -> str:
    try:
        import torch

        if requested.lower() in {"gpu", "cuda", "auto"} and torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class PredictionSink:
    fieldnames = [
        "timestamp",
        "split",
        "target_name",
        "horizon",
        "model_name",
        "y_true",
        "y_pred",
        "y_prob",
        "q10",
        "q50",
        "q90",
        "q10_cqr",
        "q90_cqr",
    ]

    def __init__(self, csv_path: Path, parquet_path: Path, reset: bool = True) -> None:
        self.csv_path = csv_path
        self.parquet_path = parquet_path
        self.rows_written = 0
        if reset:
            for path in (self.csv_path, self.parquet_path):
                if path.exists():
                    path.unlink()

    def write(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        append_csv(self.csv_path, rows, self.fieldnames)
        self.rows_written += len(rows)
        rows.clear()

    def finalize(self) -> Path:
        if not self.csv_path.exists():
            pd.DataFrame(columns=self.fieldnames).to_csv(self.csv_path, index=False)
            return self.csv_path
        try:
            df = pd.read_csv(self.csv_path)
            write_frame(df, self.parquet_path)
            return self.parquet_path
        except Exception as exc:
            log(f"[warn] could not create predictions parquet: {exc}; keeping CSV")
            return self.csv_path


def maybe_save_model(model: Any, path: Path, enabled: bool) -> None:
    if not enabled:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(model, f)


def baseline_regression_predictions(target: str, train_y: np.ndarray, test_meta: pd.DataFrame, full_df: pd.DataFrame) -> dict[str, np.ndarray]:
    n = len(test_meta)
    out = {"baseline_train_median": np.full(n, float(np.median(train_y)))}
    if "return" in target:
        out["baseline_zero_return"] = np.zeros(n)
        out["baseline_last_return"] = full_df.set_index("timestamp").reindex(test_meta["timestamp"])["ret_1m_bps"].fillna(0).values
    elif "price" in target:
        out["baseline_current_price"] = full_df.set_index("timestamp").reindex(test_meta["timestamp"])["close"].values
    return out


def train_regression(
    df: pd.DataFrame,
    feature_cols: list[str],
    targets: list[str],
    train_idx: np.ndarray,
    cal_idx: np.ndarray,
    test_idx: np.ndarray,
    config: RunConfig,
    pred_sink: PredictionSink,
    inventory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    models = regression_models(config.models, config.n_jobs, config.device)
    for target in targets:
        log(f"[reg] target={target}")
        Xtr, ytr, _, _, Xte, yte, meta = prepare_xy(
            df, feature_cols, target, train_idx, cal_idx, test_idx, config.max_train_rows
        )
        if len(ytr) < 100 or len(yte) < 50:
            log(f"[skip] {target}: not enough rows")
            continue
        for name, pred in baseline_regression_predictions(target, ytr, meta, df).items():
            row = regression_metric_row(name, target, yte, pred)
            metrics.append(row)
            summary.append({"model_name": name, "target_name": target, "horizon": row["horizon"], "metric_main": "mae", "metric_value": row["mae"], "notes": "baseline"})
            rows: list[dict[str, Any]] = []
            add_prediction_rows(rows, meta, target, name, yte, y_pred=pred)
            pred_sink.write(rows)
            inventory.append({"family": "regression", "target_name": target, "model_name": name, "status": "ok", "fit_seconds": 0.0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "baseline", "error": ""})
        for name, model in models.items():
            t0 = time.time()
            try:
                model.fit(Xtr, ytr)
                pred = np.asarray(model.predict(Xte), dtype=float)
                row = regression_metric_row(name, target, yte, pred)
                metrics.append(row)
                summary.append({"model_name": name, "target_name": target, "horizon": row["horizon"], "metric_main": "mae", "metric_value": row["mae"], "notes": "regression"})
                rows = []
                add_prediction_rows(rows, meta, target, name, yte, y_pred=pred)
                pred_sink.write(rows)
                maybe_save_model(model, MODEL_DIR / "regression" / target / f"{name}.pkl", config.save_models)
                fit_seconds = time.time() - t0
                inventory.append({"family": "regression", "target_name": target, "model_name": name, "status": "ok", "fit_seconds": fit_seconds, "train_rows": len(ytr), "test_rows": len(yte), "notes": "", "error": ""})
                log(f"[reg] {target}/{name} mae={row['mae']:.4f} rmse={row['rmse']:.4f} in {fit_seconds:.1f}s")
            except Exception as exc:
                inventory.append({"family": "regression", "target_name": target, "model_name": name, "status": "error", "fit_seconds": time.time() - t0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "", "error": str(exc)[:500]})
                log(f"[skip] regression {target}/{name}: {exc}")
            finally:
                free_memory(model)
        free_memory(Xtr, ytr, Xte, yte, meta)
    return metrics, summary


def train_classification(
    df: pd.DataFrame,
    feature_cols: list[str],
    targets: list[str],
    train_idx: np.ndarray,
    cal_idx: np.ndarray,
    test_idx: np.ndarray,
    config: RunConfig,
    pred_sink: PredictionSink,
    inventory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    models = classification_models(config.models, config.n_jobs, config.device)
    for target in targets:
        log(f"[cls] target={target}")
        Xtr, ytr, _, _, Xte, yte, meta = prepare_xy(
            df, feature_cols, target, train_idx, cal_idx, test_idx, config.max_train_rows
        )
        ytr = ytr.astype(int)
        yte = yte.astype(int)
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            log(f"[skip] {target}: single-class split")
            continue
        base = np.full(len(yte), float(ytr.mean()))
        base_row = classification_metric_row("baseline_train_rate", target, yte, base)
        metrics.append(base_row)
        summary.append({"model_name": "baseline_train_rate", "target_name": target, "horizon": base_row["horizon"], "metric_main": "brier", "metric_value": base_row["brier"], "notes": "baseline"})
        rows: list[dict[str, Any]] = []
        add_prediction_rows(rows, meta, target, "baseline_train_rate", yte, y_prob=base)
        pred_sink.write(rows)
        inventory.append({"family": "classification", "target_name": target, "model_name": "baseline_train_rate", "status": "ok", "fit_seconds": 0.0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "baseline", "error": ""})
        if "direction" in target:
            prev = (df.set_index("timestamp").reindex(meta["timestamp"])["ret_1m_bps"].fillna(0).values > 0).astype(float)
            prev_row = classification_metric_row("baseline_prev_return_sign", target, yte, prev)
            metrics.append(prev_row)
            summary.append({"model_name": "baseline_prev_return_sign", "target_name": target, "horizon": prev_row["horizon"], "metric_main": "brier", "metric_value": prev_row["brier"], "notes": "baseline"})
            rows = []
            add_prediction_rows(rows, meta, target, "baseline_prev_return_sign", yte, y_prob=prev)
            pred_sink.write(rows)
            inventory.append({"family": "classification", "target_name": target, "model_name": "baseline_prev_return_sign", "status": "ok", "fit_seconds": 0.0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "baseline", "error": ""})
        for name, model in models.items():
            t0 = time.time()
            try:
                model.fit(Xtr, ytr)
                if hasattr(model, "predict_proba"):
                    prob = np.asarray(model.predict_proba(Xte)[:, 1], dtype=float)
                else:
                    prob = np.asarray(model.predict(Xte), dtype=float)
                row = classification_metric_row(name, target, yte, prob)
                metrics.append(row)
                summary.append({"model_name": name, "target_name": target, "horizon": row["horizon"], "metric_main": "brier", "metric_value": row["brier"], "notes": "classification"})
                rows = []
                add_prediction_rows(rows, meta, target, name, yte, y_prob=prob)
                pred_sink.write(rows)
                maybe_save_model(model, MODEL_DIR / "classification" / target / f"{name}.pkl", config.save_models)
                fit_seconds = time.time() - t0
                inventory.append({"family": "classification", "target_name": target, "model_name": name, "status": "ok", "fit_seconds": fit_seconds, "train_rows": len(ytr), "test_rows": len(yte), "notes": "", "error": ""})
                log(f"[cls] {target}/{name} auc={row['auc']:.4f} brier={row['brier']:.4f} in {fit_seconds:.1f}s")
            except Exception as exc:
                inventory.append({"family": "classification", "target_name": target, "model_name": name, "status": "error", "fit_seconds": time.time() - t0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "", "error": str(exc)[:500]})
                log(f"[skip] classification {target}/{name}: {exc}")
            finally:
                free_memory(model)
        free_memory(Xtr, ytr, Xte, yte, meta)
    return metrics, summary


def train_quantiles(
    df: pd.DataFrame,
    feature_cols: list[str],
    targets: list[str],
    train_idx: np.ndarray,
    cal_idx: np.ndarray,
    test_idx: np.ndarray,
    config: RunConfig,
    pred_sink: PredictionSink,
    inventory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metrics: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for target in targets:
        log(f"[qtl] target={target}")
        Xtr, ytr, Xcal, ycal, Xte, yte, meta = prepare_xy(
            df, feature_cols, target, train_idx, cal_idx, test_idx, config.max_train_rows
        )
        q_models_by_family: dict[str, dict[float, Any]] = {}
        for q in [0.10, 0.50, 0.90]:
            for name, model in quantile_models(config.models, q, config.n_jobs, config.device, config.quantile_backends).items():
                family = name.rsplit("_q", 1)[0]
                q_models_by_family.setdefault(family, {})[q] = model
        for family, qmodels in q_models_by_family.items():
            if set(qmodels) != {0.10, 0.50, 0.90}:
                continue
            t0 = time.time()
            try:
                qpred_cal: dict[float, np.ndarray] = {}
                qpred_test: dict[float, np.ndarray] = {}
                for q, model in qmodels.items():
                    model.fit(Xtr, ytr)
                    qpred_cal[q] = np.asarray(model.predict(Xcal), dtype=float)
                    qpred_test[q] = np.asarray(model.predict(Xte), dtype=float)
                    maybe_save_model(model, MODEL_DIR / "quantile" / target / f"{family}_q{int(q * 100)}.pkl", config.save_models)
                q10 = np.minimum(qpred_test[0.10], qpred_test[0.90])
                q90 = np.maximum(qpred_test[0.10], qpred_test[0.90])
                q50 = qpred_test[0.50]
                cal_low = np.minimum(qpred_cal[0.10], qpred_cal[0.90])
                cal_high = np.maximum(qpred_cal[0.10], qpred_cal[0.90])
                scores = np.maximum.reduce([cal_low - ycal, ycal - cal_high, np.zeros_like(ycal)])
                cqr_adjust = float(np.quantile(scores, 0.80)) if len(scores) else 0.0
                q10_cqr = q10 - cqr_adjust
                q90_cqr = q90 + cqr_adjust
                raw_cov = float(((yte >= q10) & (yte <= q90)).mean())
                cqr_cov = float(((yte >= q10_cqr) & (yte <= q90_cqr)).mean())
                width = float(np.mean(q90 - q10))
                row = {
                    "model_name": family,
                    "target_name": target,
                    "horizon": extract_horizon(target),
                    "n": len(yte),
                    "pinball_q10": pinball(yte, q10, 0.10),
                    "pinball_q50": pinball(yte, q50, 0.50),
                    "pinball_q90": pinball(yte, q90, 0.90),
                    "raw_80_coverage": raw_cov,
                    "cqr_80_coverage": cqr_cov,
                    "average_band_width": width,
                    "cqr_average_band_width": float(np.mean(q90_cqr - q10_cqr)),
                    "cqr_adjust": cqr_adjust,
                }
                metrics.append(row)
                summary.append({"model_name": family, "target_name": target, "horizon": row["horizon"], "metric_main": "pinball_q50", "metric_value": row["pinball_q50"], "notes": "quantile"})
                rows: list[dict[str, Any]] = []
                add_prediction_rows(rows, meta, target, family, yte, y_pred=q50, q10=q10, q50=q50, q90=q90, q10_cqr=q10_cqr, q90_cqr=q90_cqr)
                pred_sink.write(rows)
                fit_seconds = time.time() - t0
                inventory.append({"family": "quantile", "target_name": target, "model_name": family, "status": "ok", "fit_seconds": fit_seconds, "train_rows": len(ytr), "test_rows": len(yte), "notes": "q10/q50/q90 plus conformal", "error": ""})
                log(f"[qtl] {target}/{family} cov={raw_cov:.3f} cqr={cqr_cov:.3f} width={width:.3f} in {fit_seconds:.1f}s")
            except Exception as exc:
                inventory.append({"family": "quantile", "target_name": target, "model_name": family, "status": "error", "fit_seconds": time.time() - t0, "train_rows": len(ytr), "test_rows": len(yte), "notes": "q10/q50/q90 plus conformal", "error": str(exc)[:500]})
                log(f"[skip] quantile {target}/{family}: {exc}")
            finally:
                free_memory(qmodels)
        free_memory(Xtr, ytr, Xcal, ycal, Xte, yte, meta)
    return metrics, summary


def add_prediction_rows(
    rows: list[dict[str, Any]],
    meta: pd.DataFrame,
    target: str,
    model: str,
    y_true: np.ndarray,
    y_pred: np.ndarray | None = None,
    y_prob: np.ndarray | None = None,
    q10: np.ndarray | None = None,
    q50: np.ndarray | None = None,
    q90: np.ndarray | None = None,
    q10_cqr: np.ndarray | None = None,
    q90_cqr: np.ndarray | None = None,
    limit: int | None = None,
) -> None:
    n = min(len(meta), len(y_true)) if limit is None else min(len(meta), len(y_true), limit)
    for i in range(n):
        rows.append(
            {
                "timestamp": meta["timestamp"].iloc[i],
                "split": "test",
                "target_name": target,
                "horizon": extract_horizon(target),
                "model_name": model,
                "y_true": float(y_true[i]),
                "y_pred": float(y_pred[i]) if y_pred is not None else np.nan,
                "y_prob": float(y_prob[i]) if y_prob is not None else np.nan,
                "q10": float(q10[i]) if q10 is not None else np.nan,
                "q50": float(q50[i]) if q50 is not None else np.nan,
                "q90": float(q90[i]) if q90 is not None else np.nan,
                "q10_cqr": float(q10_cqr[i]) if q10_cqr is not None else np.nan,
                "q90_cqr": float(q90_cqr[i]) if q90_cqr is not None else np.nan,
            }
        )


def train_sequence_models(
    df: pd.DataFrame,
    feature_cols: list[str],
    targets: dict[str, list[str]],
    train_idx: np.ndarray,
    cal_idx: np.ndarray,
    test_idx: np.ndarray,
    config: RunConfig,
    pred_sink: PredictionSink,
    inventory: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seq_names = [m for m in config.models if m in {"lstm", "gru", "tcn", "transformer"}]
    if not config.include_sequence and not seq_names:
        return [], []
    if not seq_names:
        seq_names = ["lstm", "gru", "tcn"]
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, Dataset
        from sklearn.preprocessing import StandardScaler
    except Exception as exc:
        log(f"[skip] sequence models unavailable: {exc}")
        return [], []

    metrics: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    seq_features = select_features(df, feature_cols, train_idx, config.seq_max_features)
    reg_targets = [t for t in targets["regression"] if ("return" in t or config.sequence_targets == "all")]
    cls_targets = [t for t in targets["classification"] if ("direction" in t or "big_move" in t or config.sequence_targets == "all")]
    selected_targets = reg_targets + cls_targets
    device = sequence_device(config.device)
    log(f"[seq] device={device} models={seq_names} features={len(seq_features)} targets={len(selected_targets)}")

    class SeqDataset(Dataset):
        def __init__(self, x: np.ndarray, y: np.ndarray, indices: np.ndarray, seq_len: int, cls: bool):
            self.x = x
            self.y = y
            self.indices = indices[indices >= seq_len - 1]
            self.seq_len = seq_len
            self.cls = cls

        def __len__(self) -> int:
            return len(self.indices)

        def __getitem__(self, i: int) -> tuple[Any, Any]:
            end = int(self.indices[i])
            start = end - self.seq_len + 1
            yy = self.y[end]
            return torch.tensor(self.x[start : end + 1], dtype=torch.float32), torch.tensor(yy, dtype=torch.float32)

    class SeqNet(nn.Module):
        def __init__(self, kind: str, n_features: int):
            super().__init__()
            self.kind = kind
            hidden = 64
            if kind == "lstm":
                self.core = nn.LSTM(n_features, hidden, batch_first=True, num_layers=1)
                self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, 1))
            elif kind == "gru":
                self.core = nn.GRU(n_features, hidden, batch_first=True, num_layers=1)
                self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, 1))
            elif kind == "tcn":
                self.core = nn.Sequential(
                    nn.Conv1d(n_features, hidden, kernel_size=3, padding=2, dilation=1),
                    nn.ReLU(),
                    nn.Conv1d(hidden, hidden, kernel_size=3, padding=4, dilation=2),
                    nn.ReLU(),
                    nn.AdaptiveAvgPool1d(1),
                )
                self.head = nn.Linear(hidden, 1)
            else:
                layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, dim_feedforward=128, batch_first=True)
                self.proj = nn.Linear(n_features, 64)
                self.core = nn.TransformerEncoder(layer, num_layers=2)
                self.head = nn.Sequential(nn.LayerNorm(64), nn.Linear(64, 1))

        def forward(self, x):
            if self.kind in {"lstm", "gru"}:
                out, _ = self.core(x)
                return self.head(out[:, -1]).squeeze(-1)
            if self.kind == "tcn":
                out = self.core(x.transpose(1, 2)).squeeze(-1)
                return self.head(out).squeeze(-1)
            out = self.core(self.proj(x))
            return self.head(out[:, -1]).squeeze(-1)

    all_idx = np.arange(len(df))
    train_all = np.concatenate([train_idx, cal_idx])
    if len(train_all) > config.seq_max_rows:
        train_all = train_all[-config.seq_max_rows :]
    if len(test_idx) > max(1000, config.seq_max_rows // 3):
        test_use = test_idx[-max(1000, config.seq_max_rows // 3) :]
    else:
        test_use = test_idx

    for target in selected_targets:
        is_cls = target in targets["classification"]
        work_cols = seq_features + [target, "timestamp"]
        work = df[work_cols].replace([np.inf, -np.inf], np.nan).copy()
        med = work.iloc[train_idx][seq_features].median(numeric_only=True)
        work[seq_features] = work[seq_features].fillna(med).fillna(0.0)
        y = work[target].values.astype(np.float32)
        valid = np.where(~np.isnan(y))[0]
        valid_set = set(valid.tolist())
        train_use = np.array([i for i in train_all if i in valid_set], dtype=int)
        test_valid = np.array([i for i in test_use if i in valid_set], dtype=int)
        if len(train_use) < 1000 or len(test_valid) < 100:
            continue
        scaler = StandardScaler()
        x = work[seq_features].values.astype(np.float32)
        scaler.fit(x[train_use])
        x = scaler.transform(x).astype(np.float32)
        for name in seq_names:
            t0 = time.time()
            try:
                model = SeqNet(name, len(seq_features)).to(device)
                loss_fn = nn.BCEWithLogitsLoss() if is_cls else nn.MSELoss()
                opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-4)
                train_ds = SeqDataset(x, y, train_use, config.seq_len, is_cls)
                test_ds = SeqDataset(x, y, test_valid, config.seq_len, is_cls)
                train_dl = DataLoader(train_ds, batch_size=config.seq_batch_size, shuffle=True)
                test_dl = DataLoader(test_ds, batch_size=config.seq_batch_size, shuffle=False)
                for _ in range(config.seq_epochs):
                    model.train()
                    for xb, yb in train_dl:
                        xb = xb.to(device)
                        yb = yb.to(device)
                        opt.zero_grad()
                        loss = loss_fn(model(xb), yb)
                        loss.backward()
                        opt.step()
                model.eval()
                yhats: list[float] = []
                yvals: list[float] = []
                with torch.no_grad():
                    for xb, yb in test_dl:
                        out = model(xb.to(device)).detach().cpu().numpy()
                        if is_cls:
                            out = 1.0 / (1.0 + np.exp(-out))
                        yhats.extend(out.tolist())
                        yvals.extend(yb.numpy().tolist())
                yh = np.asarray(yhats)
                yt = np.asarray(yvals)
                ts = work.iloc[test_ds.indices]["timestamp"].reset_index(drop=True)
                meta = pd.DataFrame({"timestamp": ts, "y_true": yt})
                if is_cls:
                    row = classification_metric_row(name, target, yt.astype(int), yh)
                    metrics.append(row)
                    summary.append({"model_name": name, "target_name": target, "horizon": row["horizon"], "metric_main": "brier", "metric_value": row["brier"], "notes": "sequence"})
                    rows: list[dict[str, Any]] = []
                    add_prediction_rows(rows, meta, target, name, yt, y_prob=yh)
                    pred_sink.write(rows)
                else:
                    row = regression_metric_row(name, target, yt, yh)
                    metrics.append(row)
                    summary.append({"model_name": name, "target_name": target, "horizon": row["horizon"], "metric_main": "mae", "metric_value": row["mae"], "notes": "sequence"})
                    rows = []
                    add_prediction_rows(rows, meta, target, name, yt, y_pred=yh)
                    pred_sink.write(rows)
                if config.save_models:
                    path = MODEL_DIR / "sequence" / target / f"{name}.pt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    torch.save({"state_dict": model.state_dict(), "features": seq_features, "config": asdict(config)}, path)
                fit_seconds = time.time() - t0
                inventory.append({"family": "sequence_classification" if is_cls else "sequence_regression", "target_name": target, "model_name": name, "status": "ok", "fit_seconds": fit_seconds, "train_rows": len(train_ds), "test_rows": len(test_ds), "notes": f"device={device}", "error": ""})
                log(f"[seq] {target}/{name} done in {fit_seconds:.1f}s")
            except Exception as exc:
                inventory.append({"family": "sequence_classification" if is_cls else "sequence_regression", "target_name": target, "model_name": name, "status": "error", "fit_seconds": time.time() - t0, "train_rows": len(train_use), "test_rows": len(test_valid), "notes": f"device={device}", "error": str(exc)[:500]})
                log(f"[skip] sequence {target}/{name}: {exc}")
            finally:
                free_memory(model if "model" in locals() else None)
        free_memory(x, y, train_use, test_valid)
    return metrics, summary


def rank_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    df = pd.DataFrame(rows)
    ranked = []
    for (_, target), g in df.groupby(["metric_main", "target_name"], dropna=False):
        gg = g.sort_values("metric_value", ascending=True).copy()
        if gg["metric_main"].iloc[0] in {"auc", "accuracy", "precision"}:
            gg = g.sort_values("metric_value", ascending=False).copy()
        gg["rank"] = np.arange(1, len(gg) + 1)
        ranked.append(gg)
    return pd.concat(ranked, ignore_index=True).to_dict("records")


def run(config: RunConfig) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "run_config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    log(f"[start] 360d research lane config={json.dumps(asdict(config), sort_keys=True)}")
    df = build_market_frame(config)
    df, feature_cols = add_features(df)
    df, target_map = add_targets(df, config.horizons)

    max_h = max(config.horizons)
    df = df.iloc[240 : len(df) - max_h].copy().reset_index(drop=True)
    df = df.dropna(subset=target_map["regression"] + target_map["classification"], how="any").reset_index(drop=True)
    train_idx, cal_idx, test_idx = chronological_splits(len(df))
    selected_features = select_features(df, feature_cols, train_idx, config.max_features)

    manifest = {
        "config": asdict(config),
        "rows": len(df),
        "split_rows": {"train": len(train_idx), "calibration": len(cal_idx), "test": len(test_idx)},
        "feature_cols": selected_features,
        "n_features": len(selected_features),
        "targets": target_map,
        "leakage_rules": [
            "features use current/past data only",
            "targets use future bars only",
            "chronological 64/16/20 split",
            "scalers/medians fit on train only",
            "research-only artifacts do not affect live app",
        ],
    }
    (MODEL_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    log(f"[features] selected={len(selected_features)} rows={len(df):,} split=train {len(train_idx):,}, cal {len(cal_idx):,}, test {len(test_idx):,}")

    all_metrics_reg: list[dict[str, Any]] = []
    all_metrics_cls: list[dict[str, Any]] = []
    all_metrics_q: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    pred_sink = PredictionSink(
        output_path(config, "predictions.csv"),
        output_path(config, "predictions.parquet"),
        reset=True,
    )

    if not config.skip_regression:
        reg_metrics, reg_summary = train_regression(
            df, selected_features, target_map["regression"], train_idx, cal_idx, test_idx, config, pred_sink, inventory
        )
        all_metrics_reg.extend(reg_metrics)
        summary.extend(reg_summary)
        write_csv(output_path(config, "regression_metrics.csv"), all_metrics_reg)
        write_csv(output_path(config, "model_inventory.csv"), inventory)
    else:
        log("[skip] regression phase disabled by --skip-regression")

    if not config.skip_classification:
        cls_metrics, cls_summary = train_classification(
            df, selected_features, target_map["classification"], train_idx, cal_idx, test_idx, config, pred_sink, inventory
        )
        all_metrics_cls.extend(cls_metrics)
        summary.extend(cls_summary)
        write_csv(output_path(config, "classification_metrics.csv"), all_metrics_cls)
        write_csv(output_path(config, "model_inventory.csv"), inventory)
    else:
        log("[skip] classification phase disabled by --skip-classification")

    if not config.skip_quantile:
        q_metrics, q_summary = train_quantiles(
            df, selected_features, target_map["quantile"], train_idx, cal_idx, test_idx, config, pred_sink, inventory
        )
        all_metrics_q.extend(q_metrics)
        summary.extend(q_summary)
        write_csv(output_path(config, "quantile_metrics.csv"), all_metrics_q)
        write_csv(output_path(config, "model_inventory.csv"), inventory)
    else:
        log("[skip] quantile phase disabled by --skip-quantile")

    if not config.skip_sequence:
        seq_reg_or_cls_metrics, seq_summary = train_sequence_models(
            df, selected_features, target_map, train_idx, cal_idx, test_idx, config, pred_sink, inventory
        )
        for row in seq_reg_or_cls_metrics:
            if "auc" in row:
                all_metrics_cls.append(row)
            else:
                all_metrics_reg.append(row)
        summary.extend(seq_summary)
    else:
        log("[skip] sequence phase disabled by --skip-sequence")

    if not config.skip_regression:
        write_csv(output_path(config, "regression_metrics.csv"), all_metrics_reg)
    if not config.skip_classification:
        write_csv(output_path(config, "classification_metrics.csv"), all_metrics_cls)
    if not config.skip_quantile:
        write_csv(output_path(config, "quantile_metrics.csv"), all_metrics_q)
    write_csv(output_path(config, "summary.csv"), rank_summary(summary))
    write_csv(output_path(config, "model_inventory.csv"), inventory)
    pred_path = pred_sink.finalize()
    log(f"[done] wrote metrics, inventory, predictions={pred_path} rows={pred_sink.rows_written:,}")


def main() -> None:
    p = argparse.ArgumentParser(description="Research-only BTC 360-day multi-target forecaster.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=360)
    p.add_argument("--output-prefix", default="forecast_360d", help="Output file prefix under data/research.")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    p.add_argument("--models", default="core", help="Comma list, e.g. ridge,histgb,lstm, or core/all.")
    p.add_argument("--smoke", action="store_true", help="Fast smoke mode: small features, rows, sequence epochs.")
    p.add_argument("--rebuild-cache", action="store_true")
    p.add_argument("--max-features", type=int, default=180)
    p.add_argument("--max-train-rows", type=int, default=0, help="0 means use all training rows.")
    p.add_argument("--n-jobs", type=int, default=max(1, min(4, (os.cpu_count() or 4) - 1)))
    p.add_argument("--device", choices=["auto", "cpu", "gpu", "cuda"], default="auto", help="Use gpu/cuda for supported tree and sequence models. auto keeps tree models on CPU but uses CUDA for PyTorch if available.")
    p.add_argument("--gpu", action="store_true", help="Shortcut for --device gpu.")
    p.add_argument("--no-save-models", action="store_true")
    p.add_argument("--skip-regression", action="store_true")
    p.add_argument("--skip-classification", action="store_true")
    p.add_argument("--skip-quantile", action="store_true")
    p.add_argument("--skip-sequence", action="store_true")
    p.add_argument("--quantile-backends", default="gbr,lightgbm", help="Comma list: gbr,lightgbm. Use lightgbm for fast GPU quantiles.")
    p.add_argument("--include-sequence", action="store_true")
    p.add_argument("--sequence-targets", choices=["core", "all"], default="core")
    p.add_argument("--seq-len", type=int, default=60)
    p.add_argument("--seq-max-features", type=int, default=48)
    p.add_argument("--seq-max-rows", type=int, default=120000)
    p.add_argument("--seq-epochs", type=int, default=8)
    p.add_argument("--seq-batch-size", type=int, default=512)
    args = p.parse_args()

    models = parse_models(args.models)
    if args.gpu:
        args.device = "gpu"
    include_sequence = args.include_sequence or any(m in {"lstm", "gru", "tcn", "transformer"} for m in models)
    if args.smoke:
        args.max_features = min(args.max_features, 60)
        args.max_train_rows = args.max_train_rows or 15000
        args.seq_max_rows = min(args.seq_max_rows, 12000)
        args.seq_epochs = min(args.seq_epochs, 2)
    config = RunConfig(
        symbol=args.symbol,
        days=args.days,
        horizons=sorted(set(args.horizons)),
        models=models,
        output_prefix=args.output_prefix,
        start=args.start,
        end=args.end,
        smoke=args.smoke,
        rebuild_cache=args.rebuild_cache,
        max_features=args.max_features,
        max_train_rows=args.max_train_rows or None,
        n_jobs=args.n_jobs,
        device=args.device,
        save_models=not args.no_save_models,
        skip_regression=args.skip_regression,
        skip_classification=args.skip_classification,
        skip_quantile=args.skip_quantile,
        skip_sequence=args.skip_sequence,
        quantile_backends=[q.strip().lower() for q in args.quantile_backends.split(",") if q.strip()],
        include_sequence=include_sequence,
        sequence_targets=args.sequence_targets,
        seq_len=args.seq_len,
        seq_max_features=args.seq_max_features,
        seq_max_rows=args.seq_max_rows,
        seq_epochs=args.seq_epochs,
        seq_batch_size=args.seq_batch_size,
    )
    run(config)


if __name__ == "__main__":
    main()
