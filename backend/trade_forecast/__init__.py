"""Shadow-only complete-trade forecasting lane.

Nothing in this package may place an order or modify the production Champion.
The package turns immutable BTC/Polymarket observations into executable,
fee-aware research labels and conservative shadow forecasts.
"""

from .trade_schema import CONFIG_VERSION, MODE

__all__ = ["CONFIG_VERSION", "MODE"]
