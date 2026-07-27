"""Typed contracts for the isolated Binance perpetual paper engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Action(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    HOLD = "HOLD"
    NO_DATA = "NO_DATA"
    NO_EDGE = "NO_EDGE"
    RISK_BLOCKED = "RISK_BLOCKED"
    COOLDOWN = "COOLDOWN"
    INVALID_SIGNAL = "INVALID_SIGNAL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class DataQuality(str, Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    event_ts_ms: int
    received_at_ms: int
    mark_price: float
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    spread: float
    spread_bps: float
    feed_age_ms: int
    feed_health: DataQuality
    update_id: int | None
    funding_rate: float | None
    funding_time_ms: int | None
    agg_trade_age_ms: int | None
    agg_trade_message_count: int
    agg_trade_count_60s: int | None
    last_completed_perp_cvd_bar_ts_ms: int | None
    mid_history: tuple[float, ...] = ()
    sample_ts_history: tuple[int, ...] = ()
    feature_availability: dict[str, bool] = field(default_factory=dict)
    source_identifiers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["feed_health"] = self.feed_health.value
        return value


@dataclass(frozen=True)
class StrategyDecision:
    signal_id: str
    strategy_id: str
    strategy_version: str
    strategy_config_hash: str
    feature_schema_hash: str
    feature_values_hash: str
    timestamp_ms: int
    symbol: str
    timeframe: str
    action: Action
    side: PositionSide | None
    score: float
    confidence: float
    requested_notional_usd: float
    stop_price: float | None
    take_profit_price: float | None
    maximum_holding_seconds: int
    features: dict[str, Any]
    required_inputs: tuple[str, ...]
    available_inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    data_quality_status: DataQuality
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["action"] = self.action.value
        value["side"] = self.side.value if self.side else None
        value["data_quality_status"] = self.data_quality_status.value
        return value


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason_codes: tuple[str, ...]
    approved_notional_usd: float
    approved_quantity: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FillResult:
    signal_id: str
    order_id: str
    fill_id: str
    strategy_id: str
    side: PositionSide
    operation: str
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    decision_ts_ms: int
    simulated_send_ts_ms: int
    simulated_arrival_ts_ms: int
    market_ts_ms: int
    received_at_ms: int
    quote_age_ms: int
    executable_price_source: str
    average_fill_price: float | None
    spread_cost_usd: float
    slippage_cost_usd: float
    fee_usd: float
    fee_rate_bps: float
    latency_assumption_ms: int
    fill_quality_status: str
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["side"] = self.side.value
        return value
