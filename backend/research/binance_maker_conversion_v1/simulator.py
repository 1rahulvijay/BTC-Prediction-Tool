"""Causal, research-only maker/taker route simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from binance_maker_conversion_v1.order_book import ConservativeQueue, LocalOrderBook

POLICIES = (
    "A_TAKER_TAKER",
    "B_MAKER_TAKER",
    "C_MAKER_TTL_FALLBACK_TAKER",
    "D_MAKER_MAKER",
    "E_TOXICITY_GATED_MAKER_TAKER",
)


@dataclass
class Route:
    candidate_id: str
    policy: str
    side: str
    horizon_seconds: int
    decision_ts_ms: int
    exit_due_ts_ms: int
    quantity: float
    entry_status: str = "PENDING"
    entry_price: float | None = None
    entry_ts_ms: int | None = None
    entry_liquidity: str | None = None
    entry_filled_quantity: float = 0.0
    entry_maker_quantity: float = 0.0
    entry_taker_quantity: float = 0.0
    entry_maker_notional: float = 0.0
    entry_taker_notional: float = 0.0
    exit_status: str = "PENDING"
    exit_price: float | None = None
    exit_ts_ms: int | None = None
    exit_liquidity: str | None = None
    exit_filled_quantity: float = 0.0
    exit_maker_quantity: float = 0.0
    exit_taker_quantity: float = 0.0
    exit_maker_notional: float = 0.0
    exit_taker_notional: float = 0.0
    entry_queue: ConservativeQueue | None = None
    exit_queue: ConservativeQueue | None = None
    status: str = "ACTIVE"
    reason: str | None = None
    marks: dict[int, float] = field(default_factory=dict)

    @property
    def long(self) -> bool:
        return self.side == "LONG"

    @property
    def entry_fill_fraction(self) -> float:
        return self.entry_filled_quantity / max(self.quantity, 1e-12)


class ExecutionSimulator:
    """Compare frozen routes without filtering the original candidate set."""

    def __init__(self, protocol: dict[str, Any]):
        execution = protocol["execution"]
        self.maker_ttl_ms = int(execution["primary_maker_ttl_ms"])
        self.exit_maker_ttl_ms = int(execution["exit_maker_ttl_ms"])
        self.maker_fee_bps = float(execution["maker_fee_bps"])
        self.taker_fee_bps = float(execution["taker_fee_bps"])

    def create_routes(
        self,
        *,
        candidate_id: str,
        side: str,
        horizon_seconds: int,
        decision_ts_ms: int,
        quantity: float,
        book: LocalOrderBook,
    ) -> list[Route]:
        routes = [
            Route(
                candidate_id=candidate_id,
                policy=policy,
                side=side,
                horizon_seconds=horizon_seconds,
                decision_ts_ms=decision_ts_ms,
                exit_due_ts_ms=decision_ts_ms + horizon_seconds * 1000,
                quantity=quantity,
            )
            for policy in POLICIES
        ]
        for route in routes:
            if route.policy == "E_TOXICITY_GATED_MAKER_TAKER":
                route.status = "SKIPPED"
                route.entry_status = "SKIPPED"
                route.exit_status = "SKIPPED"
                route.reason = "labels_required_fail_closed"
            elif route.policy == "A_TAKER_TAKER":
                self._taker_entry(route, book, decision_ts_ms)
            else:
                self._maker_entry(route, book, decision_ts_ms)
        return routes

    @staticmethod
    def _buy_for_entry(route: Route) -> bool:
        return route.long

    def _taker_entry(
        self, route: Route, book: LocalOrderBook, timestamp_ms: int
    ) -> None:
        prior_filled = route.entry_filled_quantity
        remaining = max(0.0, route.quantity - prior_filled)
        price, filled = book.walk(self._buy_for_entry(route), remaining)
        total_filled = prior_filled + filled
        route.entry_filled_quantity = total_filled
        route.entry_taker_quantity += filled
        route.entry_taker_notional += (price or 0.0) * filled
        route.entry_price = (
            (route.entry_maker_notional + route.entry_taker_notional)
            / total_filled
            if total_filled > 0.0
            else None
        )
        if filled > 0.0 and route.entry_ts_ms is None:
            route.entry_ts_ms = timestamp_ms
        route.entry_liquidity = (
            "MIXED"
            if prior_filled > 0.0 and filled > 0.0
            else "MAKER"
            if prior_filled > 0.0
            else "TAKER"
            if filled > 0.0
            else None
        )
        route.entry_status = (
            "FILLED"
            if total_filled + 1e-12 >= route.quantity
            else "PARTIAL"
            if total_filled > 0.0
            else "MISSED"
        )
        if total_filled <= 0.0:
            route.status = "MISSED"
            route.exit_status = "NOT_APPLICABLE"
            route.reason = "no_visible_entry_liquidity"

    def _maker_entry(
        self, route: Route, book: LocalOrderBook, timestamp_ms: int
    ) -> None:
        top = book.top()
        if top is None:
            route.status = "MISSED"
            route.entry_status = "MISSED"
            route.exit_status = "NOT_APPLICABLE"
            route.reason = "book_not_ready"
            return
        buy = self._buy_for_entry(route)
        price = top.best_bid if buy else top.best_ask
        queue_ahead = top.bid_quantity if buy else top.ask_quantity
        route.entry_queue = ConservativeQueue(
            buy_order=buy,
            price=price,
            quantity=route.quantity,
            queue_ahead=queue_ahead,
            placed_ts_ms=timestamp_ms,
        )
        route.entry_price = price
        route.entry_ts_ms = None
        route.entry_liquidity = "MAKER"

    def on_trade(
        self,
        route: Route,
        *,
        price: float,
        quantity: float,
        buyer_is_maker: bool,
        trade_ts_ms: int,
    ) -> None:
        if route.status != "ACTIVE":
            return
        if route.entry_queue is not None and route.entry_status == "PENDING":
            newly_filled = route.entry_queue.apply_trade(
                trade_price=price,
                trade_quantity=quantity,
                buyer_is_maker=buyer_is_maker,
                trade_ts_ms=trade_ts_ms,
            )
            route.entry_filled_quantity = route.entry_queue.filled_quantity
            route.entry_maker_quantity = route.entry_queue.filled_quantity
            route.entry_maker_notional += newly_filled * route.entry_queue.price
            if newly_filled > 0.0 and route.entry_ts_ms is None:
                route.entry_ts_ms = trade_ts_ms
            if route.entry_queue.fill_fraction >= 1.0 - 1e-12:
                route.entry_status = "FILLED"
                route.entry_ts_ms = trade_ts_ms
        if route.exit_queue is not None and route.exit_status == "PENDING":
            newly_filled = route.exit_queue.apply_trade(
                trade_price=price,
                trade_quantity=quantity,
                buyer_is_maker=buyer_is_maker,
                trade_ts_ms=trade_ts_ms,
            )
            route.exit_filled_quantity = route.exit_queue.filled_quantity
            route.exit_maker_quantity = route.exit_queue.filled_quantity
            route.exit_maker_notional += newly_filled * route.exit_queue.price
            if newly_filled > 0.0 and route.exit_ts_ms is None:
                route.exit_ts_ms = trade_ts_ms
            if route.exit_queue.fill_fraction >= 1.0 - 1e-12:
                route.exit_status = "FILLED"
                route.exit_ts_ms = trade_ts_ms
                route.status = "RESOLVED"

    def on_clock(
        self, route: Route, book: LocalOrderBook, timestamp_ms: int
    ) -> None:
        if route.status != "ACTIVE":
            return
        if route.entry_status == "PENDING" and (
            timestamp_ms >= route.decision_ts_ms + self.maker_ttl_ms
        ):
            route.entry_filled_quantity = (
                route.entry_queue.filled_quantity if route.entry_queue else 0.0
            )
            route.entry_maker_quantity = route.entry_filled_quantity
            if route.policy == "C_MAKER_TTL_FALLBACK_TAKER":
                self._taker_entry(route, book, timestamp_ms)
            elif route.entry_filled_quantity > 0.0:
                route.entry_status = "PARTIAL"
            else:
                route.entry_status = "MISSED"
                route.exit_status = "NOT_APPLICABLE"
                route.status = "MISSED"
                route.reason = "maker_entry_not_filled_before_ttl"
                return
        if timestamp_ms < route.exit_due_ts_ms:
            return
        if route.entry_filled_quantity <= 0.0:
            route.status = "MISSED"
            route.exit_status = "NOT_APPLICABLE"
            return
        if route.exit_status == "PENDING" and route.exit_queue is None:
            if route.policy == "D_MAKER_MAKER":
                self._place_maker_exit(route, book, timestamp_ms)
            else:
                self._taker_exit(route, book, timestamp_ms)
        elif (
            route.exit_queue is not None
            and timestamp_ms >= route.exit_due_ts_ms + self.exit_maker_ttl_ms
        ):
            route.exit_filled_quantity = route.exit_queue.filled_quantity
            route.exit_maker_quantity = route.exit_queue.filled_quantity
            route.exit_status = (
                "PARTIAL" if route.exit_filled_quantity > 0.0 else "MISSED"
            )
            route.status = "UNWOUND_INCOMPLETE"
            route.reason = "maker_exit_not_filled_before_ttl"

    def _taker_exit(
        self, route: Route, book: LocalOrderBook, timestamp_ms: int
    ) -> None:
        quantity = route.entry_filled_quantity
        price, filled = book.walk(not route.long, quantity)
        route.exit_price = price
        route.exit_filled_quantity = filled
        route.exit_taker_quantity = filled
        route.exit_taker_notional = (price or 0.0) * filled
        route.exit_ts_ms = timestamp_ms if filled > 0.0 else None
        route.exit_liquidity = "TAKER" if filled > 0.0 else None
        route.exit_status = (
            "FILLED" if filled + 1e-12 >= quantity else "PARTIAL"
            if filled > 0.0 else "MISSED"
        )
        route.status = "RESOLVED" if route.exit_status == "FILLED" else "UNWOUND_INCOMPLETE"

    def _place_maker_exit(
        self, route: Route, book: LocalOrderBook, timestamp_ms: int
    ) -> None:
        top = book.top()
        if top is None:
            route.exit_status = "MISSED"
            route.status = "UNWOUND_INCOMPLETE"
            route.reason = "book_not_ready_at_exit"
            return
        buy = not route.long
        price = top.best_bid if buy else top.best_ask
        queue_ahead = top.bid_quantity if buy else top.ask_quantity
        route.exit_price = price
        route.exit_ts_ms = None
        route.exit_liquidity = "MAKER"
        route.exit_queue = ConservativeQueue(
            buy_order=buy,
            price=price,
            quantity=route.entry_filled_quantity,
            queue_ahead=queue_ahead,
            placed_ts_ms=timestamp_ms,
        )

    def economics(self, route: Route) -> dict[str, float | None]:
        if (
            route.status != "RESOLVED"
            or route.entry_price is None
            or route.exit_price is None
            or route.entry_filled_quantity <= 0.0
        ):
            return {
                "gross_bps": None,
                "fee_bps": None,
                "net_bps": None,
                "candidate_weighted_net_bps": None,
            }
        direction = 1.0 if route.long else -1.0
        gross = direction * (
            route.exit_price / route.entry_price - 1.0
        ) * 10_000.0
        entry_notional = max(
            route.entry_maker_notional + route.entry_taker_notional, 1e-12
        )
        fee_usd = (
            route.entry_maker_notional * self.maker_fee_bps
            + route.entry_taker_notional * self.taker_fee_bps
            + route.exit_maker_notional * self.maker_fee_bps
            + route.exit_taker_notional * self.taker_fee_bps
        ) / 10_000.0
        fees = fee_usd / entry_notional * 10_000.0
        net = gross - fees
        return {
            "gross_bps": gross,
            "fee_bps": fees,
            "net_bps": net,
            "candidate_weighted_net_bps": net * route.entry_fill_fraction,
        }
