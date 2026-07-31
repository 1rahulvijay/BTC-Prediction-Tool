"""Binance paper strategy implementations.

Phase 1 shipped two CONTINUATION strategies and no benchmark, so their paper P&L could not be
distinguished from BTC path structure or from the cost of trading at all. Phase 2 adds the
missing denominator (random_control) and the one directional species the lane had never run
(mean_reversion).
"""

from .breakout import BreakoutStrategy
from .mean_reversion import MeanReversionStrategy
from .random_control import RandomControlStrategy
from .trend_following import TrendFollowingStrategy

__all__ = [
    "BreakoutStrategy",
    "MeanReversionStrategy",
    "RandomControlStrategy",
    "TrendFollowingStrategy",
]
