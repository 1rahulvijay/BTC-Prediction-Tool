"""Exact top-of-book ladder walking for Binance linear futures paper fills."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .paper_types import BookLevel, BookSnapshot, OrderSide


@dataclass(frozen=True, slots=True)
class DepthFill:
    requested_quantity: float
    filled_quantity: float
    average_price: float | None
    notional: float
    complete: bool


def walk_book(
    book: BookSnapshot,
    side: OrderSide,
    quantity: float,
    max_slippage_bps: float | None = None,
) -> DepthFill:
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("quantity must be finite and positive")
    if max_slippage_bps is not None and (
        not math.isfinite(max_slippage_bps) or max_slippage_bps < 0
    ):
        raise ValueError("max_slippage_bps must be finite and non-negative")

    levels: list[BookLevel]
    if side is OrderSide.BUY:
        levels = sorted(book.asks, key=lambda level: level.price)
    else:
        levels = sorted(book.bids, key=lambda level: level.price, reverse=True)
    best_price = levels[0].price
    remaining = quantity
    notional = 0.0
    filled = 0.0
    for level in levels:
        slippage_bps = (
            (level.price / best_price - 1.0) * 10_000.0
            if side is OrderSide.BUY
            else (1.0 - level.price / best_price) * 10_000.0
        )
        if max_slippage_bps is not None and slippage_bps > max_slippage_bps:
            break
        take = min(remaining, level.quantity)
        if take <= 0:
            continue
        notional += take * level.price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    return DepthFill(
        requested_quantity=quantity,
        filled_quantity=filled,
        average_price=(notional / filled) if filled > 0 else None,
        notional=notional,
        complete=remaining <= 1e-12,
    )
