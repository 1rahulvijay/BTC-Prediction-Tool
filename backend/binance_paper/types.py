"""Strong types for Binance paper execution and accounting."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import math


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class FillStatus(StrEnum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.price)
            or not math.isfinite(self.quantity)
            or self.price <= 0
            or self.quantity <= 0
        ):
            raise ValueError("book level price and quantity must be finite and positive")


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    instrument: str
    exchange_ts_ns: int
    receive_ts_ns: int
    sequence_id: str
    source_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    sequence_healthy: bool = True

    def __post_init__(self) -> None:
        if not self.instrument or not self.sequence_id or not self.source_id:
            raise ValueError("book identity fields must be non-empty")
        if self.exchange_ts_ns <= 0 or self.receive_ts_ns < self.exchange_ts_ns:
            raise ValueError("invalid book timestamps")
        if not self.bids or not self.asks:
            raise ValueError("both bid and ask ladders are required")
        if max(level.price for level in self.bids) >= min(
            level.price for level in self.asks
        ):
            raise ValueError("crossed or locked book is not executable")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    order_id: str
    decision_ts_ns: int
    instrument: str
    strategy_id: str
    side: OrderSide
    quantity: float
    leverage: float = 1.0
    reduce_only: bool = False
    model_available: bool = True

    def __post_init__(self) -> None:
        if not self.order_id or not self.instrument or not self.strategy_id:
            raise ValueError("order identity fields must be non-empty")
        if self.decision_ts_ns <= 0:
            raise ValueError("decision_ts_ns must be positive")
        if (
            not math.isfinite(self.quantity)
            or not math.isfinite(self.leverage)
            or self.quantity <= 0
            or self.leverage <= 0
        ):
            raise ValueError("quantity and leverage must be finite and positive")

    @property
    def request_sha256(self) -> str:
        raw = json.dumps(
            {
                **asdict(self),
                "side": self.side.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    order_id: str
    request_sha256: str
    status: FillStatus
    requested_quantity: float
    filled_quantity: float
    average_price: float | None
    filled_notional: float
    fee: float
    fill_ts_ns: int
    reason_codes: tuple[str, ...]


@dataclass(slots=True)
class PositionState:
    instrument: str
    quantity: float = 0.0
    average_entry: float = 0.0
    realized_pnl_gross: float = 0.0
    fees_paid: float = 0.0
    funding_pnl: float = 0.0
    cash_balance: float = 0.0
    updated_at_ns: int = 0

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "LONG"
        if self.quantity < 0:
            return "SHORT"
        return "FLAT"
