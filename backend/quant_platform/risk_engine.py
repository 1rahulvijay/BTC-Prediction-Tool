"""Venue-neutral risk checks that return explicit, auditable blocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math


class RiskAction(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_leverage: float = 2.0
    max_notional: float = 1_000.0
    max_correlated_exposure: float = 2_000.0
    max_daily_loss: float = 100.0
    max_weekly_loss: float = 300.0
    max_feed_age_ms: float = 2_000.0


@dataclass(frozen=True, slots=True)
class RiskState:
    kill_switch: bool = False
    position_known: bool = True
    model_available: bool = True
    feed_age_ms: float = math.inf
    sequence_healthy: bool = False
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    open_notional: float = 0.0
    correlated_exposure: float = 0.0


@dataclass(frozen=True, slots=True)
class OrderIntent:
    venue: str
    instrument: str
    strategy_id: str
    notional: float
    leverage: float
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class RiskDecision:
    action: RiskAction
    reasons: tuple[str, ...] = field(default_factory=tuple)


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def evaluate(self, intent: OrderIntent, state: RiskState) -> RiskDecision:
        reasons: list[str] = []
        if state.kill_switch:
            reasons.append("kill_switch")
        if not state.position_known:
            reasons.append("unknown_position")
        if not state.model_available and not intent.reduce_only:
            reasons.append("model_unavailable")
        if not state.sequence_healthy:
            reasons.append("sequence_unhealthy")
        if not math.isfinite(state.feed_age_ms):
            reasons.append("feed_age_unknown")
        elif state.feed_age_ms > self.limits.max_feed_age_ms:
            reasons.append("stale_feed")
        if intent.notional <= 0 or not math.isfinite(intent.notional):
            reasons.append("invalid_notional")
        if intent.leverage <= 0 or intent.leverage > self.limits.max_leverage:
            reasons.append("leverage_limit")
        if not intent.reduce_only:
            if state.open_notional + intent.notional > self.limits.max_notional:
                reasons.append("notional_limit")
            if (
                state.correlated_exposure + intent.notional
                > self.limits.max_correlated_exposure
            ):
                reasons.append("correlated_exposure_limit")
            if state.daily_pnl <= -self.limits.max_daily_loss:
                reasons.append("daily_loss_limit")
            if state.weekly_pnl <= -self.limits.max_weekly_loss:
                reasons.append("weekly_loss_limit")
        return RiskDecision(
            RiskAction.BLOCK if reasons else RiskAction.ALLOW,
            tuple(reasons),
        )
