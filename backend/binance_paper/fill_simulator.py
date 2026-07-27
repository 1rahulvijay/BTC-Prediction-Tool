"""Conservative top-of-book Binance perpetual paper fills."""
from __future__ import annotations

import uuid

from .config import EngineConfig
from .schemas import FillResult, MarketSnapshot, PositionSide


def binance_fee(filled_notional_usd: float, fee_rate_bps: float) -> float:
    return max(0.0, float(filled_notional_usd)) * max(
        0.0, float(fee_rate_bps)
    ) / 10_000.0


class BinancePaperFillSimulator:
    """Use executable bid/ask, visible top size, latency and explicit slippage."""

    def __init__(self, config: EngineConfig):
        self.config = config

    def simulate(
        self,
        *,
        signal_id: str,
        order_id: str,
        strategy_id: str,
        side: PositionSide,
        operation: str,
        requested_quantity: float,
        decision_ts_ms: int,
        snapshot: MarketSnapshot,
        require_full: bool = False,
    ) -> FillResult:
        requested = max(0.0, float(requested_quantity))
        arrival = int(decision_ts_ms) + self.config.latency_ms
        operation = str(operation).upper()
        if operation not in ("ENTRY", "EXIT"):
            raise ValueError(f"unsupported fill operation: {operation}")
        buy = (operation == "ENTRY" and side is PositionSide.LONG) or (
            operation == "EXIT" and side is PositionSide.SHORT
        )
        quote_age = max(0, snapshot.received_at_ms - snapshot.event_ts_ms)
        rejection = None
        if requested <= 0:
            rejection = "non_positive_quantity"
        elif snapshot.received_at_ms < arrival:
            rejection = "latency_not_reached"
        elif snapshot.feed_age_ms > self.config.quote_stale_ms:
            rejection = "stale_quote"

        if rejection:
            return FillResult(
                signal_id=signal_id,
                order_id=order_id,
                fill_id=str(uuid.uuid4()),
                strategy_id=strategy_id,
                side=side,
                operation=operation,
                requested_quantity=requested,
                filled_quantity=0.0,
                unfilled_quantity=requested,
                decision_ts_ms=int(decision_ts_ms),
                simulated_send_ts_ms=int(decision_ts_ms),
                simulated_arrival_ts_ms=arrival,
                market_ts_ms=snapshot.event_ts_ms,
                received_at_ms=snapshot.received_at_ms,
                quote_age_ms=quote_age,
                executable_price_source="binance_futures_bookTicker",
                average_fill_price=None,
                spread_cost_usd=0.0,
                slippage_cost_usd=0.0,
                fee_usd=0.0,
                fee_rate_bps=self.config.fee_rate_bps,
                latency_assumption_ms=self.config.latency_ms,
                fill_quality_status="REJECTED",
                rejection_reason=rejection,
            )

        top_price = snapshot.best_ask if buy else snapshot.best_bid
        visible_size = snapshot.ask_size if buy else snapshot.bid_size
        filled = min(requested, max(0.0, visible_size))
        unfilled = requested - filled
        if require_full and unfilled > 1e-12:
            filled = 0.0
            unfilled = requested
        slippage_fraction = self.config.slippage_bps / 10_000.0
        average_price = (
            top_price * (1.0 + slippage_fraction)
            if buy
            else top_price * (1.0 - slippage_fraction)
        )
        midpoint = snapshot.mark_price
        spread_cost = abs(top_price - midpoint) * filled
        slippage_cost = abs(average_price - top_price) * filled
        fee = binance_fee(average_price * filled, self.config.fee_rate_bps)
        if filled <= 0:
            status = "REJECTED"
            rejection = (
                "fill_or_kill_liquidity"
                if require_full and visible_size > 0
                else "no_visible_liquidity"
            )
            average_price_value = None
        else:
            status = (
                "DEGRADED_TOP_OF_BOOK"
                if unfilled <= 1e-12
                else "PARTIAL_TOP_OF_BOOK"
            )
            rejection = None if unfilled <= 1e-12 else "insufficient_visible_liquidity"
            average_price_value = average_price
        return FillResult(
            signal_id=signal_id,
            order_id=order_id,
            fill_id=str(uuid.uuid4()),
            strategy_id=strategy_id,
            side=side,
            operation=operation,
            requested_quantity=requested,
            filled_quantity=filled,
            unfilled_quantity=max(0.0, unfilled),
            decision_ts_ms=int(decision_ts_ms),
            simulated_send_ts_ms=int(decision_ts_ms),
            simulated_arrival_ts_ms=arrival,
            market_ts_ms=snapshot.event_ts_ms,
            received_at_ms=snapshot.received_at_ms,
            quote_age_ms=quote_age,
            executable_price_source="binance_futures_bookTicker",
            average_fill_price=average_price_value,
            spread_cost_usd=spread_cost,
            slippage_cost_usd=slippage_cost,
            fee_usd=fee,
            fee_rate_bps=self.config.fee_rate_bps,
            latency_assumption_ms=self.config.latency_ms,
            fill_quality_status=status,
            rejection_reason=rejection,
        )
