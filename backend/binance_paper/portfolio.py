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

    def apply_funding(self, snapshot: MarketSnapshot) -> list[dict]:
        return self.persistence.apply_observed_funding(snapshot)

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
    def exit_reason(position: dict, snapshot: MarketSnapshot,
                    strategy=None) -> str | None:
        """Static levels first, then the strategy's own thesis check.

        STOP and TAKE_PROFIT are evaluated BEFORE the dynamic reassessment on purpose: those are
        prices the book has actually reached, while a thesis check is an opinion. A position that
        has genuinely been stopped out must be recorded as STOP, not relabelled by a strategy
        that also happens to want out.

        MAX_HOLD is evaluated AFTER the thesis check, because a dead thesis at second 299 should
        close as a dead thesis rather than be reported as a position that simply ran out of time.

        `strategy` is optional, so every existing caller keeps working and a strategy expressing
        no thesis behaves exactly as before.
        """
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
        if strategy is not None:
            try:
                dynamic = strategy.position_exit_reason(position, snapshot)
            except Exception:
                # A thesis check must never be able to strand an open position. Falling back to
                # the static levels is the safe direction: it holds, it does not force a trade.
                dynamic = None
            if dynamic:
                return str(dynamic)
        if age_seconds >= int(position["maximum_holding_seconds"]):
            return "MAX_HOLD"
        return None
