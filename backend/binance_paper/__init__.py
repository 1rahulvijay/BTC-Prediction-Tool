"""Isolated Binance USD-M futures paper-trading domain.

This package never submits authenticated exchange orders.
"""

from .engine import BinancePaperEngine
from .types import BookLevel, BookSnapshot, OrderRequest, OrderSide

__all__ = [
    "BinancePaperEngine",
    "BookLevel",
    "BookSnapshot",
    "OrderRequest",
    "OrderSide",
]
