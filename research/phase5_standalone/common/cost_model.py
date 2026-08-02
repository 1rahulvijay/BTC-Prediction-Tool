"""Venue-specific executable cost calculations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BinanceCost:
    fee_bps_each_side: float = 4.0
    spread_bps_round_trip: float = 1.0
    slippage_bps_round_trip: float = 0.0

    def round_trip_bps(self, multiplier: float = 1.0) -> float:
        base = 2.0 * self.fee_bps_each_side + self.spread_bps_round_trip
        return float(multiplier) * (base + self.slippage_bps_round_trip)


def polymarket_fee_per_share(price: float, fee_rate: float = 0.07) -> float:
    p = max(0.0, min(1.0, float(price)))
    return round(max(0.0, fee_rate) * p * (1.0 - p), 5)


def polymarket_settlement_pnl(
    *, ask: float, won: bool, cost_multiplier: float = 1.0, size: float = 1.0,
) -> tuple[float, float]:
    fee = polymarket_fee_per_share(ask) * float(cost_multiplier)
    gross = (1.0 if won else 0.0) - float(ask)
    net = gross - fee
    return gross * float(size), net * float(size)


def selftest() -> None:
    assert BinanceCost().round_trip_bps() == 9.0
    assert polymarket_fee_per_share(0.5) == 0.0175
    gross, net = polymarket_settlement_pnl(ask=0.4, won=True)
    assert gross == 0.6 and net < gross

