"""Independent per-strategy one-way paper portfolios."""
from __future__ import annotations

import uuid

from .schemas import MarketSnapshot, PositionSide, StrategyDecision, FillResult


class BinancePaperPortfolio:
    def __init__(self, persistence):
        self.persistence = persistence

    def position_for(self, strategy_id: str) -> dict | None:
        rows = self.persistence.open_positions(strategy_id)
        return rows[0] if rows else None

    def mark(self, snapshot: MarketSnapshot) -> None:
        self.persistence.mark_positions(snapshot)

    def open(
        self,
        decision: StrategyDecision,
        fill: FillResult,
        leverage: float,
        *,
        connection=None,
    ) -> dict:
        return self.persistence.open_position(
            position_id=str(uuid.uuid4()),
            decision=decision,
            fill=fill,
            leverage=leverage,
            connection=connection,
        )

    def close(
        self,
        position_id: str,
        fill: FillResult,
        exit_reason: str,
        strategy_version: str,
        *,
        connection=None,
    ) -> dict:
        return self.persistence.close_position(
            position_id=position_id,
            fill=fill,
            exit_reason=exit_reason,
            strategy_version=strategy_version,
            connection=connection,
        )

    @staticmethod
    def exit_reason(position: dict, snapshot: MarketSnapshot) -> str | None:
        side = PositionSide(position["side"])
        age_seconds = max(
            0.0, (snapshot.received_at_ms - int(position["opened_at_ms"])) / 1000.0
        )
        if side is PositionSide.LONG:
            if snapshot.best_bid <= float(position["stop_price"]):
                return "STOP"
            if snapshot.best_bid >= float(position["take_profit_price"]):
                return "TAKE_PROFIT"
        else:
            if snapshot.best_ask >= float(position["stop_price"]):
                return "STOP"
            if snapshot.best_ask <= float(position["take_profit_price"]):
                return "TAKE_PROFIT"
        if age_seconds >= int(position["maximum_holding_seconds"]):
            return "MAX_HOLD"
        return None
