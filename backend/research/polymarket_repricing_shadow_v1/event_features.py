"""Causal live parity features for the 5s/15s event-time specialists."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

WINDOWS = (1, 3, 5, 10, 30, 60)
FEATURE_NAMES = [
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "basis_bps",
]
for lag in WINDOWS:
    FEATURE_NAMES.extend(
        [
            f"spot_ret_{lag}s_bps",
            f"perp_ret_{lag}s_bps",
            f"perp_lead_{lag}s_bps",
            f"basis_change_{lag}s_bps",
        ]
    )
for window in WINDOWS:
    FEATURE_NAMES.extend(
        [
            f"spot_flow_{window}s",
            f"perp_flow_{window}s",
            f"flow_divergence_{window}s",
            f"flow_agreement_{window}s",
            f"log_spot_volume_{window}s",
            f"log_perp_volume_{window}s",
            f"log_spot_intensity_{window}s",
            f"log_perp_intensity_{window}s",
        ]
    )
for window in (10, 30, 60):
    FEATURE_NAMES.extend(
        [
            f"spot_rms_{window}s_bps",
            f"perp_rms_{window}s_bps",
            f"spot_range_{window}s_bps",
        ]
    )


class EventFeatureBuffer:
    """Retain completed one-second spot/perpetual aggregate-trade bars."""

    def __init__(self, retention_seconds: int = 180):
        self.retention_seconds = max(90, int(retention_seconds))
        self._bars: dict[str, dict[int, dict[str, float]]] = {
            "spot": defaultdict(dict),
            "perp": defaultdict(dict),
        }

    def update(
        self,
        venue: str,
        timestamp_ms: int,
        price: float,
        quantity: float,
        buyer_is_maker: bool,
    ) -> None:
        if venue not in self._bars or price <= 0 or quantity < 0:
            return
        second = int(timestamp_ms) // 1_000
        bars = self._bars[venue]
        bar = bars.get(second)
        signed = -quantity if buyer_is_maker else quantity
        if bar is None:
            bars[second] = {
                "last": price,
                "high": price,
                "low": price,
                "volume": quantity,
                "signed": signed,
                "count": 1.0,
            }
        else:
            bar["last"] = price
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["volume"] += quantity
            bar["signed"] += signed
            bar["count"] += 1.0
        cutoff = second - self.retention_seconds
        for old in [value for value in bars if value < cutoff]:
            del bars[old]

    def feature_row(self, completed_second: int) -> pd.DataFrame | None:
        start = int(completed_second) - 60
        timestamps = np.arange(start, int(completed_second) + 1, dtype=np.int64)
        venue_values: dict[str, dict[str, np.ndarray]] = {}
        for venue in ("spot", "perp"):
            bars = self._bars[venue]
            last = np.full(len(timestamps), np.nan, dtype=float)
            high = np.full(len(timestamps), np.nan, dtype=float)
            low = np.full(len(timestamps), np.nan, dtype=float)
            volume = np.zeros(len(timestamps), dtype=float)
            signed = np.zeros(len(timestamps), dtype=float)
            count = np.zeros(len(timestamps), dtype=float)
            for index, second in enumerate(timestamps):
                bar = bars.get(int(second))
                if bar is None:
                    continue
                for name, target in (
                    ("last", last),
                    ("high", high),
                    ("low", low),
                    ("volume", volume),
                    ("signed", signed),
                    ("count", count),
                ):
                    target[index] = bar[name]
            last = pd.Series(last).ffill().to_numpy(float)
            if not np.isfinite(last).all():
                return None
            high = np.where(np.isfinite(high), high, last)
            low = np.where(np.isfinite(low), low, last)
            venue_values[venue] = {
                "last": last,
                "high": high,
                "low": low,
                "volume": volume,
                "signed": signed,
                "count": count,
            }
        values = build_feature_values(int(completed_second), venue_values)
        if list(values) != FEATURE_NAMES:
            raise AssertionError(
                "live event feature order does not match the frozen schema"
            )
        matrix = np.asarray(list(values.values()), dtype=float)
        if not np.isfinite(matrix).all():
            return None
        return pd.DataFrame([values], columns=FEATURE_NAMES, dtype=np.float32)


def _return_bps(values: np.ndarray, lag: int) -> float:
    return float((values[-1] / values[-1 - lag] - 1.0) * 10_000.0)


def build_feature_values(
    timestamp_s: int,
    venues: dict[str, dict[str, np.ndarray]],
) -> dict[str, float]:
    spot = venues["spot"]
    perp = venues["perp"]
    spot_last = spot["last"]
    perp_last = perp["last"]
    basis = (perp_last / spot_last - 1.0) * 10_000.0
    result: dict[str, float] = {}
    second_of_day = timestamp_s % 86_400
    result["hour_sin"] = math.sin(2.0 * math.pi * second_of_day / 86_400.0)
    result["hour_cos"] = math.cos(2.0 * math.pi * second_of_day / 86_400.0)
    day = (timestamp_s // 86_400) % 7
    result["weekday_sin"] = math.sin(2.0 * math.pi * day / 7.0)
    result["weekday_cos"] = math.cos(2.0 * math.pi * day / 7.0)
    result["basis_bps"] = float(basis[-1])

    spot_returns_1 = np.zeros(len(spot_last), dtype=float)
    perp_returns_1 = np.zeros(len(perp_last), dtype=float)
    spot_returns_1[1:] = (spot_last[1:] / spot_last[:-1] - 1.0) * 10_000.0
    perp_returns_1[1:] = (perp_last[1:] / perp_last[:-1] - 1.0) * 10_000.0
    for lag in WINDOWS:
        spot_return = _return_bps(spot_last, lag)
        perp_return = _return_bps(perp_last, lag)
        result[f"spot_ret_{lag}s_bps"] = spot_return
        result[f"perp_ret_{lag}s_bps"] = perp_return
        result[f"perp_lead_{lag}s_bps"] = perp_return - spot_return
        result[f"basis_change_{lag}s_bps"] = float(basis[-1] - basis[-1 - lag])

    for window in WINDOWS:
        window_slice = slice(-window, None)
        spot_volume = float(spot["volume"][window_slice].sum())
        perp_volume = float(perp["volume"][window_slice].sum())
        spot_signed = float(spot["signed"][window_slice].sum())
        perp_signed = float(perp["signed"][window_slice].sum())
        spot_flow = spot_signed / max(spot_volume, 1e-9)
        perp_flow = perp_signed / max(perp_volume, 1e-9)
        result[f"spot_flow_{window}s"] = spot_flow
        result[f"perp_flow_{window}s"] = perp_flow
        result[f"flow_divergence_{window}s"] = perp_flow - spot_flow
        result[f"flow_agreement_{window}s"] = perp_flow * spot_flow
        result[f"log_spot_volume_{window}s"] = math.log1p(spot_volume)
        result[f"log_perp_volume_{window}s"] = math.log1p(perp_volume)
        result[f"log_spot_intensity_{window}s"] = math.log1p(
            float(spot["count"][window_slice].sum()) / window
        )
        result[f"log_perp_intensity_{window}s"] = math.log1p(
            float(perp["count"][window_slice].sum()) / window
        )

    for window in (10, 30, 60):
        result[f"spot_rms_{window}s_bps"] = float(
            np.sqrt(np.mean(np.square(spot_returns_1[-window:])))
        )
        result[f"perp_rms_{window}s_bps"] = float(
            np.sqrt(np.mean(np.square(perp_returns_1[-window:])))
        )
        price_range = float(
            np.max(spot["high"][-window:]) - np.min(spot["low"][-window:])
        )
        result[f"spot_range_{window}s_bps"] = (
            price_range / max(float(spot_last[-1]), 1e-9) * 10_000.0
        )
    return result


def score_event_bundle(bundle: dict[str, Any], row: pd.DataFrame) -> dict[str, float]:
    if list(row.columns) != list(bundle["feature_names"]):
        raise ValueError("event feature schema mismatch")
    output: dict[str, float] = {}
    for horizon in bundle["horizons_seconds"]:
        for head in bundle["heads"]:
            members = bundle["models"][str(horizon)][head]
            probabilities = []
            for member in members:
                raw = member["model"].predict_proba(row.to_numpy(np.float32))[:, 1]
                probabilities.append(float(member["calibrator"].predict(raw)[0]))
            output[f"p_{head}_{horizon}"] = float(np.mean(probabilities))
    return output
