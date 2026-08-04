"""Canonical strategy interface and deterministic decision construction."""
from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
import math
from typing import Any

from .config import StrategyRiskConfig
from .schemas import (
    Action,
    DataQuality,
    MarketSnapshot,
    PositionSide,
    StrategyDecision,
)


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StrategyBase(ABC):
    strategy_id: str
    strategy_name: str
    strategy_version: str
    timeframe: str
    required_inputs: tuple[str, ...]
    candidate_validity_ms = 2_500
    maximum_entry_drift_bps = 2.0

    # A take-profit closer than the round trip means every perfect winner still loses. Both
    # Phase-1 strategies shipped with a 6.0 bps target against a 12.0 bps round trip - the
    # 1-second sample volatility (0.81 bps) was multiplied by 2 and floored at 4.0 bps, so the
    # floor always bound, and the target was 1.5x that floor. The strategies were not unlikely
    # to profit; they were arithmetically incapable of it, and nothing downstream noticed
    # because the accounting was correct and only the intent was wrong.
    #
    # assumed_round_trip_bps must be >= the engine's configured 2 x (fee + slippage).
    # test_paper_strategy_economics.py asserts both halves of that.
    assumed_round_trip_bps = 12.0
    minimum_target_cost_multiple = 1.5

    @property
    def minimum_take_profit_bps(self) -> float:
        return float(self.assumed_round_trip_bps) * float(self.minimum_target_cost_multiple)

    def __init__(self, risk: StrategyRiskConfig | None = None):
        self.risk = (risk or StrategyRiskConfig()).clamped()

    @property
    def parameters(self) -> dict[str, Any]:
        return {}

    @property
    def config_hash(self) -> str:
        return canonical_hash(
            {
                "strategy_id": self.strategy_id,
                "strategy_version": self.strategy_version,
                "parameters": self.parameters,
                "risk": self.risk.to_dict(),
                "candidate_validity_ms": self.candidate_validity_ms,
                "maximum_entry_drift_bps": self.maximum_entry_drift_bps,
            }
        )

    @property
    def feature_schema_hash(self) -> str:
        return canonical_hash(list(self.required_inputs))

    def _decision(
        self,
        snapshot: MarketSnapshot,
        *,
        action: Action,
        side: PositionSide | None,
        score: float,
        confidence: float,
        requested_notional_usd: float,
        stop_price: float | None,
        take_profit_price: float | None,
        maximum_holding_seconds: int,
        features: dict[str, Any],
        missing_inputs: tuple[str, ...] = (),
        data_quality_status: DataQuality | None = None,
        reason_codes: tuple[str, ...] = (),
        probability_calibrated: bool = False,
        uncertainty_status: str = "UNMEASURED",
        expected_net_pnl_usd: float | None = None,
        expected_net_pnl_lower_bound_usd: float | None = None,
    ) -> StrategyDecision:
        available = tuple(
            name
            for name in self.required_inputs
            if name not in set(missing_inputs)
        )
        feature_values_hash = canonical_hash(features)
        identity = {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "timestamp_ms": snapshot.received_at_ms,
            "action": action.value,
            "side": side.value if side else None,
            "feature_values_hash": feature_values_hash,
        }
        if not all(
            math.isfinite(float(value))
            for value in (score, confidence, requested_notional_usd)
        ):
            raise ValueError("strategy score, confidence and notional must be finite")
        if stop_price is not None and not math.isfinite(float(stop_price)):
            raise ValueError("strategy stop must be finite")
        if take_profit_price is not None and not math.isfinite(float(take_profit_price)):
            raise ValueError("strategy target must be finite")
        # Refuse a target that cannot clear the round trip. This is a programming error in the
        # strategy, not a market condition, so it raises like the finiteness checks above rather
        # than degrading to NO_EDGE - a strategy that would open a position it cannot win must
        # fail loudly at its first decision, in test, not bleed quietly in paper.
        if side is not None and take_profit_price is not None and snapshot.mark_price > 0:
            target_bps = (
                abs(float(take_profit_price) - snapshot.mark_price)
                / snapshot.mark_price * 10_000.0
            )
            if target_bps < self.assumed_round_trip_bps:
                raise ValueError(
                    f"{self.strategy_id}: take-profit is {target_bps:.2f} bps from mark but the "
                    f"round trip costs {self.assumed_round_trip_bps:.2f} bps - every winner "
                    f"would still lose {self.assumed_round_trip_bps - target_bps:.2f} bps"
                )
        drift_fraction = max(0.0, float(self.maximum_entry_drift_bps)) / 10_000.0
        maximum_entry_price = (
            snapshot.best_ask * (1.0 + drift_fraction)
            if side is PositionSide.LONG
            else None
        )
        minimum_entry_price = (
            snapshot.best_bid * (1.0 - drift_fraction)
            if side is PositionSide.SHORT
            else None
        )
        # Recorded for EVERY strategy, so post-fill geometry can measure how far the
        # actual entry drifted from the price this decision was priced against.
        return StrategyDecision(
            decision_mark_price=snapshot.mark_price,
            signal_id=canonical_hash(identity),
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            strategy_config_hash=self.config_hash,
            feature_schema_hash=self.feature_schema_hash,
            feature_values_hash=feature_values_hash,
            timestamp_ms=snapshot.received_at_ms,
            symbol=snapshot.symbol,
            timeframe=self.timeframe,
            action=action,
            side=side,
            score=min(1.0, max(-1.0, float(score))),
            confidence=min(1.0, max(0.0, float(confidence))),
            requested_notional_usd=max(0.0, float(requested_notional_usd)),
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            maximum_holding_seconds=max(1, int(maximum_holding_seconds)),
            features=features,
            required_inputs=self.required_inputs,
            available_inputs=available,
            missing_inputs=tuple(missing_inputs),
            data_quality_status=data_quality_status or snapshot.feed_health,
            reason_codes=tuple(reason_codes),
            valid_until_ms=(
                snapshot.received_at_ms + max(1, int(self.candidate_validity_ms))
                if side is not None
                else None
            ),
            maximum_entry_price=maximum_entry_price,
            minimum_entry_price=minimum_entry_price,
            probability_calibrated=bool(probability_calibrated),
            uncertainty_status=str(uncertainty_status or "UNMEASURED"),
            expected_net_pnl_usd=expected_net_pnl_usd,
            expected_net_pnl_lower_bound_usd=expected_net_pnl_lower_bound_usd,
        )

    def no_data(
        self,
        snapshot: MarketSnapshot,
        missing_inputs: tuple[str, ...],
        features: dict[str, Any],
        *reason_codes: str,
    ) -> StrategyDecision:
        return self._decision(
            snapshot,
            action=Action.NO_DATA,
            side=None,
            score=0.0,
            confidence=0.0,
            requested_notional_usd=0.0,
            stop_price=None,
            take_profit_price=None,
            maximum_holding_seconds=1,
            features=features,
            missing_inputs=missing_inputs,
            data_quality_status=(
                DataQuality.STALE
                if snapshot.feed_health is DataQuality.STALE
                else DataQuality.MISSING
            ),
            reason_codes=tuple(reason_codes) or ("required_input_missing",),
        )

    @abstractmethod
    def decide(self, snapshot: MarketSnapshot) -> StrategyDecision:
        raise NotImplementedError

    # ---- dynamic exit -------------------------------------------------------------------
    def position_exit_reason(
        self, position: dict[str, Any], snapshot: MarketSnapshot
    ) -> str | None:
        """Should this OPEN position be closed because its thesis is gone?

        Binance exits were entirely static - stop price, take-profit price and MAX_HOLD, all
        three fixed at entry. A position whose reason for existing had already evaporated still
        sat there until price happened to touch a level chosen minutes earlier.

        The principle here is the same one the Polymarket module uses, and it deliberately adds
        no new tunable: EXIT WHEN THE STRATEGY WOULD NO LONGER ENTER. Entry and exit become one
        rule read in both directions, so a strategy cannot hold a position it would not open.

        Return an exit reason string to close, or None to leave the static levels in charge.
        The default is None, so a strategy that expresses no thesis keeps exactly its old
        behaviour - and `random_control` MUST keep that default, because a control that reacted
        to a thesis would no longer be zero-information and would stop being a valid benchmark.
        """
        return None
