"""Sequenced Binance USD-M local order book and conservative queue mechanics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class BookSequenceGap(RuntimeError):
    """Raised when a diff-depth sequence cannot extend the local book."""


@dataclass(frozen=True)
class BookTop:
    event_ts_ms: int
    received_ts_ms: int
    update_id: int
    best_bid: float
    bid_quantity: float
    best_ask: float
    ask_quantity: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.best_ask - self.best_bid) / self.mid * 10_000.0


class LocalOrderBook:
    """Apply absolute-quantity diff events using Binance's sequence rules."""

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id = 0
        self.last_event_ts_ms = 0
        self.last_received_ts_ms = 0
        self.ready = False
        self._first_event = True

    def initialize(self, snapshot: dict[str, Any], received_ts_ms: int) -> None:
        self.bids = self._levels(snapshot.get("bids") or [])
        self.asks = self._levels(snapshot.get("asks") or [])
        self.last_update_id = int(snapshot["lastUpdateId"])
        self.last_event_ts_ms = int(snapshot.get("E") or 0)
        self.last_received_ts_ms = int(received_ts_ms)
        self.ready = bool(self.bids and self.asks)
        self._first_event = True
        if not self.ready:
            raise ValueError("depth snapshot has no two-sided liquidity")

    @staticmethod
    def _levels(rows: Iterable[Iterable[Any]]) -> dict[float, float]:
        output: dict[float, float] = {}
        for price_raw, quantity_raw, *_ in rows:
            price = float(price_raw)
            quantity = float(quantity_raw)
            if price > 0.0 and quantity > 0.0:
                output[price] = quantity
        return output

    def apply(self, event: dict[str, Any], received_ts_ms: int) -> bool:
        if not self.ready:
            raise BookSequenceGap("book must be initialized before applying diffs")
        first = int(event["U"])
        final = int(event["u"])
        previous = int(event.get("pu") or 0)
        if final < self.last_update_id:
            return False
        if self._first_event:
            if not (first <= self.last_update_id <= final):
                if first > self.last_update_id:
                    raise BookSequenceGap(
                        f"first diff [{first},{final}] misses snapshot "
                        f"{self.last_update_id}"
                    )
                return False
            self._first_event = False
        elif previous != self.last_update_id:
            raise BookSequenceGap(
                f"diff pu={previous} does not extend u={self.last_update_id}"
            )
        for side, key in ((self.bids, "b"), (self.asks, "a")):
            for price_raw, quantity_raw, *_ in event.get(key) or []:
                price = float(price_raw)
                quantity = float(quantity_raw)
                if quantity == 0.0:
                    side.pop(price, None)
                elif price > 0.0 and quantity > 0.0:
                    side[price] = quantity
        self.last_update_id = final
        self.last_event_ts_ms = int(event.get("E") or event.get("T") or 0)
        self.last_received_ts_ms = int(received_ts_ms)
        if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
            raise BookSequenceGap("local order book became crossed or one-sided")
        return True

    def top(self) -> BookTop | None:
        if not self.ready or not self.bids or not self.asks:
            return None
        bid = max(self.bids)
        ask = min(self.asks)
        return BookTop(
            event_ts_ms=self.last_event_ts_ms,
            received_ts_ms=self.last_received_ts_ms,
            update_id=self.last_update_id,
            best_bid=bid,
            bid_quantity=self.bids[bid],
            best_ask=ask,
            ask_quantity=self.asks[ask],
        )

    def quantity_at(self, side: str, price: float) -> float:
        levels = self.bids if side.upper() == "BID" else self.asks
        return float(levels.get(float(price), 0.0))

    def walk(self, buy: bool, quantity: float) -> tuple[float | None, float]:
        """Return exact visible-depth VWAP and filled quantity."""
        requested = max(0.0, float(quantity))
        if requested == 0.0:
            return None, 0.0
        levels = sorted(self.asks.items()) if buy else sorted(
            self.bids.items(), reverse=True
        )
        remaining = requested
        notional = 0.0
        filled = 0.0
        for price, available in levels:
            take = min(remaining, available)
            notional += price * take
            filled += take
            remaining -= take
            if remaining <= 1e-12:
                break
        return (notional / filled if filled > 0.0 else None), filled


@dataclass
class ConservativeQueue:
    """Displayed queue ahead consumed only by matching public aggressor trades."""

    buy_order: bool
    price: float
    quantity: float
    queue_ahead: float
    placed_ts_ms: int
    filled_quantity: float = 0.0
    traded_through_quantity: float = 0.0

    def apply_trade(
        self,
        *,
        trade_price: float,
        trade_quantity: float,
        buyer_is_maker: bool,
        trade_ts_ms: int,
    ) -> float:
        if trade_ts_ms < self.placed_ts_ms or self.filled_quantity >= self.quantity:
            return 0.0
        aggressive_sell = bool(buyer_is_maker)
        eligible = (
            self.buy_order
            and aggressive_sell
            and trade_price <= self.price
        ) or (
            not self.buy_order
            and not aggressive_sell
            and trade_price >= self.price
        )
        if not eligible:
            return 0.0
        available = max(0.0, float(trade_quantity))
        self.traded_through_quantity += available
        queue_used = min(self.queue_ahead, available)
        self.queue_ahead -= queue_used
        available -= queue_used
        fill = min(self.quantity - self.filled_quantity, available)
        self.filled_quantity += fill
        return fill

    @property
    def fill_fraction(self) -> float:
        return min(1.0, self.filled_quantity / max(self.quantity, 1e-12))
