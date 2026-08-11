"""Isolated BTCUSDT USD-M perpetual paper-trading infrastructure.

This package contains no authenticated exchange client and cannot submit a
real Binance order.
"""

from .config import EngineConfig, StrategyRiskConfig
from .service import BinancePaperService

__all__ = [
    "BinancePaperEngine",
    "BinancePaperService",
    "BookLevel",
    "BookSnapshot",
    "EngineConfig",
    "OrderRequest",
    "OrderSide",
    "StrategyRiskConfig",
]


def __getattr__(name):
    """Load the standalone accounting harness only when explicitly requested.

    The production server runs with ``backend`` on ``sys.path`` and needs only
    ``BinancePaperService``. Eagerly importing the separate harness also pulled
    in the repository-root ``backend.quant_platform`` package and made otherwise
    valid direct server imports depend on an extra path entry.
    """
    if name == "BinancePaperEngine":
        from .engine import BinancePaperEngine

        return BinancePaperEngine
    if name in {"BookLevel", "BookSnapshot", "OrderRequest", "OrderSide"}:
        from .paper_types import BookLevel, BookSnapshot, OrderRequest, OrderSide

        return {
            "BookLevel": BookLevel,
            "BookSnapshot": BookSnapshot,
            "OrderRequest": OrderRequest,
            "OrderSide": OrderSide,
        }[name]
    raise AttributeError(name)
