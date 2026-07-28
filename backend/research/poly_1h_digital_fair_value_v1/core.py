"""Pure probability, path-state and book mathematics for the 1h campaign."""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SECONDS_PER_YEAR = 365.25 * 24.0 * 3600.0


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def annualized_to_per_second(volatility: float) -> float:
    return max(0.0, float(volatility)) / math.sqrt(SECONDS_PER_YEAR)


def digital_up_probability(
    current_price: float,
    open_price: float,
    seconds_left: float,
    annualized_volatility: float,
    *,
    drift_per_second: float = 0.0,
    probability_clip: float = 0.001,
) -> float:
    """Probability final close is >= open under a conditional log diffusion."""
    current = float(current_price)
    anchor = float(open_price)
    remaining = max(0.0, float(seconds_left))
    if current <= 0.0 or anchor <= 0.0:
        raise ValueError("prices must be positive")
    if remaining <= 0.0:
        return 1.0 if current >= anchor else 0.0
    sigma = annualized_to_per_second(max(float(annualized_volatility), 1e-9))
    denominator = sigma * math.sqrt(remaining)
    if denominator <= 0.0:
        return 1.0 if current >= anchor else 0.0
    log_distance = math.log(current / anchor)
    z_score = (log_distance + float(drift_per_second) * remaining) / denominator
    return clamp(normal_cdf(z_score), probability_clip, 1.0 - probability_clip)


def mixture_probability(
    current_price: float,
    open_price: float,
    seconds_left: float,
    volatilities: Iterable[float],
    weights: Iterable[float],
    *,
    probability_clip: float = 0.001,
) -> float:
    vols = [float(value) for value in volatilities]
    raw_weights = [max(0.0, float(value)) for value in weights]
    if not vols or len(vols) != len(raw_weights) or sum(raw_weights) <= 0.0:
        raise ValueError("volatilities and positive weights must have equal length")
    total = sum(raw_weights)
    probability = sum(
        weight
        / total
        * digital_up_probability(
            current_price,
            open_price,
            seconds_left,
            volatility,
            probability_clip=probability_clip,
        )
        for volatility, weight in zip(vols, raw_weights)
    )
    return clamp(probability, probability_clip, 1.0 - probability_clip)


def realized_annualized_volatility(
    close_prices: Iterable[float],
    *,
    interval_seconds: float = 60.0,
    minimum: float = 0.15,
    maximum: float = 2.50,
) -> float:
    values = [float(value) for value in close_prices if float(value) > 0.0]
    if len(values) < 3:
        return float(minimum)
    returns = [
        math.log(values[index] / values[index - 1])
        for index in range(1, len(values))
        if values[index - 1] > 0.0
    ]
    if len(returns) < 2:
        return float(minimum)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    annualized = math.sqrt(max(variance, 0.0)) * math.sqrt(
        SECONDS_PER_YEAR / max(float(interval_seconds), 1e-9)
    )
    return clamp(annualized, minimum, maximum)


def normalized_market_probability(up_mid: float, down_mid: float) -> float:
    up = float(up_mid)
    down = float(down_mid)
    denominator = up + down
    if up <= 0.0 or down <= 0.0 or denominator <= 0.0:
        raise ValueError("both market midpoints must be positive")
    return clamp(up / denominator, 0.001, 0.999)


def fee_per_share(price: float, base_fee_bps: float) -> float:
    rate = max(0.0, float(base_fee_bps)) / 10_000.0
    probability = clamp(float(price), 0.0, 1.0)
    return round(rate * probability * (1.0 - probability), 5)


@dataclass(frozen=True)
class BookSide:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    spread: float
    midpoint: float
    exchange_timestamp_ms: int
    receive_timestamp_ms: int
    receive_latency_ms: float
    book_hash: str
    minimum_order_size: float
    tick_size: float
    neg_risk: bool
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


def parse_book(payload: dict[str, Any], received_ms: int, latency_ms: float) -> BookSide:
    bids = sorted(
        (
            (float(level["price"]), float(level["size"]))
            for level in payload.get("bids", [])
            if float(level.get("size", 0.0)) > 0.0
        ),
        reverse=True,
    )
    asks = sorted(
        (float(level["price"]), float(level["size"]))
        for level in payload.get("asks", [])
        if float(level.get("size", 0.0)) > 0.0
    )
    if not bids or not asks:
        raise ValueError("book is not two-sided")
    bid = bids[0][0]
    ask = asks[0][0]
    if bid <= 0.0 or ask >= 1.0 or bid >= ask:
        raise ValueError(f"invalid binary book bid={bid} ask={ask}")
    raw_timestamp = float(payload.get("timestamp") or 0.0)
    exchange_ms = int(raw_timestamp * 1000.0 if 0 < raw_timestamp < 1e11 else raw_timestamp)
    return BookSide(
        bid=bid,
        ask=ask,
        bid_size=bids[0][1],
        ask_size=asks[0][1],
        spread=ask - bid,
        midpoint=(bid + ask) / 2.0,
        exchange_timestamp_ms=exchange_ms,
        receive_timestamp_ms=int(received_ms),
        receive_latency_ms=float(latency_ms),
        book_hash=str(payload.get("hash") or ""),
        minimum_order_size=float(payload.get("min_order_size") or 0.0),
        tick_size=float(payload.get("tick_size") or 0.0),
        neg_risk=bool(payload.get("neg_risk")),
        bids=tuple(bids),
        asks=tuple(asks),
    )


def vwap(levels: Iterable[tuple[float, float]], quantity: float) -> tuple[float | None, float]:
    requested = max(0.0, float(quantity))
    if requested <= 0.0:
        return None, 0.0
    filled = 0.0
    notional = 0.0
    for price, available in levels:
        take = min(max(0.0, float(available)), requested - filled)
        if take <= 0.0:
            continue
        notional += take * float(price)
        filled += take
        if filled + 1e-12 >= requested:
            break
    if filled + 1e-12 < requested:
        return None, filled
    return notional / filled, filled


def vwap_with_fee(
    levels: Iterable[tuple[float, float]],
    quantity: float,
    base_fee_bps: float,
) -> tuple[float | None, float, float | None]:
    """Return VWAP, filled quantity and exact level-weighted fee per share."""
    requested = max(0.0, float(quantity))
    if requested <= 0.0:
        return None, 0.0, None
    filled = 0.0
    notional = 0.0
    fees = 0.0
    for price, available in levels:
        take = min(max(0.0, float(available)), requested - filled)
        if take <= 0.0:
            continue
        notional += take * float(price)
        fees += take * fee_per_share(float(price), base_fee_bps)
        filled += take
        if filled + 1e-12 >= requested:
            break
    if filled + 1e-12 < requested:
        return None, filled, None
    return notional / filled, filled, fees / filled


def ladder_json(book: BookSide, levels: int = 50) -> str:
    return json.dumps(
        {
            "bids": list(book.bids[:levels]),
            "asks": list(book.asks[:levels]),
        },
        separators=(",", ":"),
    )


class RoundPathState:
    """Causal path features calculated only from observations seen so far."""

    def __init__(self, open_price: float):
        if float(open_price) <= 0.0:
            raise ValueError("open price must be positive")
        self.open_price = float(open_price)
        self.samples: deque[tuple[float, float, float, int]] = deque(maxlen=7200)
        self.crossings = 0
        self.last_side = 0
        self.last_cross_timestamp: float | None = None
        self.residence_started_at: float | None = None
        self.completed_residences: list[tuple[int, float]] = []
        self.maximum_above_bps = 0.0
        self.minimum_below_bps = 0.0

    @staticmethod
    def _side(distance_bps: float) -> int:
        if distance_bps > 1e-12:
            return 1
        if distance_bps < -1e-12:
            return -1
        return 0

    def update(self, timestamp: float, price: float) -> dict[str, float | int]:
        distance = (float(price) / self.open_price - 1.0) * 10_000.0
        side = self._side(distance)
        if not self.samples:
            self.residence_started_at = float(timestamp)
        elif side and self.last_side and side != self.last_side:
            self.crossings += 1
            if self.residence_started_at is not None:
                self.completed_residences.append(
                    (self.last_side, float(timestamp) - self.residence_started_at)
                )
            self.last_cross_timestamp = float(timestamp)
            self.residence_started_at = float(timestamp)
        if side:
            self.last_side = side
        self.maximum_above_bps = max(self.maximum_above_bps, distance)
        self.minimum_below_bps = min(self.minimum_below_bps, distance)
        self.samples.append((float(timestamp), float(price), distance, side))
        return self.features(float(timestamp))

    def _slope_bps_per_second(self, now: float, window: float) -> float:
        selected = [
            (timestamp, distance)
            for timestamp, _price, distance, _side in self.samples
            if timestamp >= now - window
        ]
        if len(selected) < 2:
            return 0.0
        times = [value[0] for value in selected]
        distances = [value[1] for value in selected]
        mean_time = sum(times) / len(times)
        mean_distance = sum(distances) / len(distances)
        denominator = sum((value - mean_time) ** 2 for value in times)
        if denominator <= 0.0:
            return 0.0
        return sum(
            (timestamp - mean_time) * (distance - mean_distance)
            for timestamp, distance in selected
        ) / denominator

    def features(self, now: float) -> dict[str, float | int]:
        if not self.samples:
            return {}
        elapsed = max(float(now) - self.samples[0][0], 0.0)
        above = sum(side > 0 for *_prefix, side in self.samples)
        below = sum(side < 0 for *_prefix, side in self.samples)
        count = max(len(self.samples), 1)
        current_distance = self.samples[-1][2]
        current_side = self._side(current_distance)
        completed_above = [duration for side, duration in self.completed_residences if side > 0]
        completed_below = [duration for side, duration in self.completed_residences if side < 0]
        current_residence = (
            max(0.0, float(now) - self.residence_started_at)
            if self.residence_started_at is not None
            else 0.0
        )
        above_residences = completed_above + ([current_residence] if current_side > 0 else [])
        below_residences = completed_below + ([current_residence] if current_side < 0 else [])
        side_maximum = self.maximum_above_bps if current_side >= 0 else self.minimum_below_bps
        drawdown = (
            side_maximum - current_distance
            if current_side >= 0
            else current_distance - side_maximum
        )
        return {
            "fraction_above": above / count,
            "fraction_below": below / count,
            "crossing_count": self.crossings,
            "crossing_rate_per_minute": self.crossings / max(elapsed / 60.0, 1.0),
            "seconds_since_crossing": (
                max(0.0, float(now) - self.last_cross_timestamp)
                if self.last_cross_timestamp is not None
                else elapsed
            ),
            "average_residence_above": (
                sum(above_residences) / len(above_residences) if above_residences else 0.0
            ),
            "average_residence_below": (
                sum(below_residences) / len(below_residences) if below_residences else 0.0
            ),
            "longest_residence_above": max(above_residences, default=0.0),
            "longest_residence_below": max(below_residences, default=0.0),
            "maximum_above_bps": self.maximum_above_bps,
            "minimum_below_bps": self.minimum_below_bps,
            "drawdown_from_side_extreme_bps": max(0.0, drawdown),
            "velocity_15s_bps_per_second": self._slope_bps_per_second(now, 15.0),
            "velocity_60s_bps_per_second": self._slope_bps_per_second(now, 60.0),
        }


def settled_side(open_price: float, close_price: float) -> str:
    return "UP" if float(close_price) >= float(open_price) else "DOWN"
