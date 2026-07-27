"""Venue-neutral infrastructure for the BTC quantitative platform.

This package owns transport, identity, health, contracts, governance, and risk
plumbing. Venue economics belong in their domain packages.
"""

from .events import EventHealth, MarketEvent

__all__ = ["EventHealth", "MarketEvent"]
