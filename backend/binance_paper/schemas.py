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


class OrderState(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    RISK_BLOCKED = "RISK_BLOCKED"
    CANCELLED = "CANCELLED"
    CANCELLED_RECOVERY = "CANCELLED_RECOVERY"


_ORDER_TRANSITIONS: dict[OrderState | None, frozenset[OrderState]] = {
    None: frozenset((OrderState.PENDING, OrderState.RISK_BLOCKED, OrderState.REJECTED)),
    OrderState.PENDING: frozenset(
        (
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.CANCELLED_RECOVERY,
        )
    ),
}


def validate_order_transition(previous: str | None, current: str) -> OrderState:
    try:
        previous_state = OrderState(previous) if previous is not None else None
        current_state = OrderState(current)
    except ValueError as exc:
        raise ValueError(f"unknown paper order state: {exc}") from exc
    allowed = _ORDER_TRANSITIONS.get(previous_state, frozenset())
    if current_state not in allowed:
        before = previous_state.value if previous_state is not None else "NONE"
        raise ValueError(f"invalid paper order transition: {before}->{current_state.value}")
    return current_state


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
    #: LOCAL age: how long since THIS PROCESS received the message.
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
    model_context: dict[str, Any] = field(default_factory=dict)
    #: Coverage facts behind the count above and behind mid_history. Without these a consumer
    #: cannot tell a genuine 60-second window from two samples a second apart, or a continuous
    #: history from one with a five-minute hole in it.
    #: SOURCE age: how long since the EXCHANGE stamped the event. `feed_age_ms` measures only
    #: how long ago THIS PROCESS received it, so a delayed old event received now scored ~0 and
    #: passed as fresh - then became the executable quote.
    source_age_ms: int = 0
    #: TRANSPORT lag: received_at_ms - event_ts_ms. The fill simulator computed this as
    #: `quote_age` and discarded it without ever testing it.
    transport_lag_ms: int = 0
    agg_trade_coverage_seconds: float = 0.0
    agg_trades_per_second: float | None = None
    agg_trade_window_complete: bool = False
    mid_history_coverage_ratio: float = 0.0
    mid_history_max_gap_ms: int | None = None
    mid_history_usable: bool = False
    mid_history_unusable_reason: str | None = None

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
    valid_until_ms: int | None = None
    maximum_entry_price: float | None = None
    minimum_entry_price: float | None = None
    probability_calibrated: bool = False
    uncertainty_status: str = "UNMEASURED"
    expected_net_pnl_usd: float | None = None
    #: RENAMED 2026-08-04. This holds `notional * ((2p' - 1) * move - costs)` where p' is the
    #: calibrated probability minus a FIXED 0.05 constant. That constant is not a confidence
    #: interval, a conformal bound, a bootstrap bound or any empirical estimate, so storing it
    #: under `..._lower_bound_usd` asserted a statistical property the arithmetic does not have.
    #: `lower_bound` is reserved for an interval with a declared coverage method.
    expected_net_pnl_heuristic_haircut_usd: float | None = None
    #: The mark price the stop/target/EV were computed against. Needed to measure how far the
    #: ACTUAL fill drifted from the decision, which is what determines whether the target still
    #: clears costs. Without it, post-fill geometry cannot be checked at all.
    decision_mark_price: float | None = None

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
