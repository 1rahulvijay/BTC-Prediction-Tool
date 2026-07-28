"""Causal feature construction from normalized received-time market data."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


FEATURE_COLUMNS = (
    "ret_5s_bps",
    "ret_15s_bps",
    "ret_30s_bps",
    "ret_60s_bps",
    "ret_180s_bps",
    "rv_30s_bps",
    "rv_60s_bps",
    "rv_180s_bps",
    "vol_acceleration",
    "spread_bps",
    "spread_z_60s",
    "top_imbalance",
    "depth_imbalance_20",
    "imbalance_change_15s",
    "trade_count_5s",
    "trade_count_30s",
    "trade_count_60s",
    "trade_notional_5s",
    "trade_notional_30s",
    "signed_flow_5s",
    "signed_flow_30s",
    "signed_flow_60s",
    "flow_imbalance_5s",
    "flow_imbalance_30s",
    "quote_interval_ms",
    "exchange_receive_lag_ms",
    "hour_sin",
    "hour_cos",
)


def _lagged_asof(
    timestamps: np.ndarray,
    values: np.ndarray,
    seconds: int,
) -> np.ndarray:
    targets = timestamps - int(seconds * 1_000_000_000)
    indices = np.searchsorted(timestamps, targets, side="right") - 1
    result = np.full(len(values), np.nan, dtype=float)
    valid = indices >= 0
    result[valid] = values[indices[valid]]
    return result


def _session_ids(timestamps: np.ndarray, maximum_gap_ms: int) -> np.ndarray:
    if len(timestamps) == 0:
        return np.asarray([], dtype=np.int64)
    gaps_ms = np.diff(timestamps) / 1_000_000.0
    return np.concatenate(
        (
            np.asarray([0], dtype=np.int64),
            np.cumsum(gaps_ms > float(maximum_gap_ms), dtype=np.int64),
        )
    )


def _session_lagged_asof(
    timestamps: np.ndarray,
    values: np.ndarray,
    session_ids: np.ndarray,
    seconds: int,
) -> np.ndarray:
    result = np.full(len(values), np.nan, dtype=float)
    for session_id in np.unique(session_ids):
        indices = np.flatnonzero(session_ids == session_id)
        result[indices] = _lagged_asof(
            timestamps[indices],
            values[indices],
            seconds,
        )
    return result


def _rolling_time_sum(
    target_ts: np.ndarray,
    source_ts: np.ndarray,
    cumulative: np.ndarray,
    seconds: int,
) -> np.ndarray:
    right = np.searchsorted(source_ts, target_ts, side="right")
    left = np.searchsorted(
        source_ts, target_ts - int(seconds * 1_000_000_000), side="right"
    )
    prefix = np.concatenate(([0.0], cumulative))
    return prefix[right] - prefix[left]


def build_causal_features(
    books: pd.DataFrame,
    trade_flow: pd.DataFrame,
    *,
    maximum_gap_ms: int = 10_000,
) -> pd.DataFrame:
    frame = books.sort_values("receive_ts_ns").reset_index(drop=True).copy()
    ts = frame["receive_ts_ns"].to_numpy(np.int64)
    mid = frame["mid"].to_numpy(float)
    session_ids = _session_ids(ts, maximum_gap_ms)
    frame["receive_session_id"] = session_ids
    for seconds in (5, 15, 30, 60, 180):
        prior = _session_lagged_asof(
            ts,
            mid,
            session_ids,
            seconds,
        )
        frame[f"ret_{seconds}s_bps"] = np.where(
            prior > 0, (mid / prior - 1.0) * 10_000.0, np.nan
        )

    median_interval = max(
        1.0, float(np.nanmedian(np.diff(ts) / 1_000_000_000.0))
    )
    log_return = np.full(len(frame), np.nan, dtype=float)
    for session_id in np.unique(session_ids):
        indices = np.flatnonzero(session_ids == session_id)
        log_return[indices] = (
            np.diff(np.log(mid[indices]), prepend=np.nan) * 10_000.0
        )
    for seconds in (30, 60, 180):
        periods = max(2, int(round(seconds / median_interval)))
        output = np.full(len(frame), np.nan, dtype=float)
        for session_id in np.unique(session_ids):
            indices = np.flatnonzero(session_ids == session_id)
            output[indices] = (
                pd.Series(log_return[indices])
                .rolling(periods, min_periods=max(2, periods // 2))
                .std(ddof=0)
                .to_numpy()
                * math.sqrt(periods)
            )
        frame[f"rv_{seconds}s_bps"] = output
    frame["vol_acceleration"] = (
        frame["rv_30s_bps"] - frame["rv_180s_bps"]
    )
    spread = frame["spread_bps"].astype(float)
    spread_window = max(3, int(round(60 / median_interval)))
    spread_z = np.full(len(frame), np.nan, dtype=float)
    for session_id in np.unique(session_ids):
        indices = np.flatnonzero(session_ids == session_id)
        values = spread.iloc[indices].reset_index(drop=True)
        spread_mean = values.rolling(spread_window, min_periods=3).mean()
        spread_std = values.rolling(
            spread_window, min_periods=3
        ).std(ddof=0)
        spread_z[indices] = (
            (values - spread_mean) / spread_std.replace(0, np.nan)
        ).to_numpy()
    frame["spread_z_60s"] = spread_z
    prior_imbalance = _session_lagged_asof(
        ts,
        frame["depth_imbalance_20"].to_numpy(float),
        session_ids,
        15,
    )
    frame["imbalance_change_15s"] = (
        frame["depth_imbalance_20"].to_numpy(float) - prior_imbalance
    )
    quote_interval_ms = (
        pd.Series(ts).diff().to_numpy(float) / 1_000_000.0
    )
    quote_interval_ms[
        np.concatenate(([True], np.diff(session_ids) != 0))
    ] = np.nan
    frame["quote_interval_ms"] = quote_interval_ms
    frame["exchange_receive_lag_ms"] = (
        frame["receive_ts_ns"].to_numpy(np.int64)
        - frame["exchange_ts_ns"].to_numpy(np.int64)
    ) / 1_000_000.0

    flow = trade_flow.sort_values("receive_ts_ns").reset_index(drop=True)
    flow_ts = flow["receive_ts_ns"].to_numpy(np.int64)
    for seconds in (5, 30, 60):
        frame[f"trade_count_{seconds}s"] = _rolling_time_sum(
            ts,
            flow_ts,
            flow["trade_count"].to_numpy(float),
            seconds,
        )
        frame[f"trade_notional_{seconds}s"] = _rolling_time_sum(
            ts,
            flow_ts,
            flow["trade_notional"].to_numpy(float),
            seconds,
        )
        frame[f"signed_flow_{seconds}s"] = _rolling_time_sum(
            ts,
            flow_ts,
            flow["signed_notional"].to_numpy(float),
            seconds,
        )
        frame[f"flow_imbalance_{seconds}s"] = np.divide(
            frame[f"signed_flow_{seconds}s"],
            frame[f"trade_notional_{seconds}s"],
            out=np.zeros(len(frame), dtype=float),
            where=frame[f"trade_notional_{seconds}s"].to_numpy(float) > 0,
        )

    utc = pd.to_datetime(ts, unit="ns", utc=True)
    seconds_of_day = utc.hour * 3600 + utc.minute * 60 + utc.second
    angle = 2.0 * np.pi * seconds_of_day / 86_400.0
    frame["hour_sin"] = np.sin(angle)
    frame["hour_cos"] = np.cos(angle)
    numeric_columns = frame.select_dtypes(include=[np.number]).columns
    frame.loc[:, numeric_columns] = frame[numeric_columns].replace(
        [np.inf, -np.inf], np.nan
    )
    stale = frame["exchange_receive_lag_ms"] > float(maximum_gap_ms)
    frame.loc[stale, list(FEATURE_COLUMNS)] = np.nan
    return frame


def decision_rows(
    features: pd.DataFrame,
    *,
    interval_seconds: int,
    maximum_horizon_seconds: int,
) -> pd.DataFrame:
    ordered = features.sort_values("receive_ts_ns").reset_index(drop=True)
    timestamps = ordered["receive_ts_ns"].to_numpy(np.int64)
    start = int(timestamps[0])
    end = int(timestamps[-1] - maximum_horizon_seconds * 1_000_000_000)
    next_boundary = (
        (start // (interval_seconds * 1_000_000_000)) + 1
    ) * interval_seconds * 1_000_000_000
    selected: list[int] = []
    while next_boundary <= end:
        index = int(np.searchsorted(timestamps, next_boundary, side="left"))
        if index < len(ordered):
            if not selected or index != selected[-1]:
                selected.append(index)
        next_boundary += interval_seconds * 1_000_000_000
    result = ordered.iloc[selected].copy()
    result["book_index"] = selected
    result = result.dropna(subset=list(FEATURE_COLUMNS)).reset_index(drop=True)
    return result
