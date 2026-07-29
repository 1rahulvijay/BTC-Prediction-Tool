"""Venue-neutral risk checks that return explicit, auditable blocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math


class RiskAction(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    # A reduce-only order permitted THROUGH a fault that blocks new exposure. Distinct from
    # ALLOW so the log records that the order was admitted under degraded conditions.
    ALLOW_REDUCE_ONLY = "ALLOW_REDUCE_ONLY"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_leverage: float = 2.0
    max_notional: float = 1_000.0
    max_correlated_exposure: float = 2_000.0
    max_daily_loss: float = 100.0
    max_weekly_loss: float = 300.0
    max_feed_age_ms: float = 2_000.0

    def __post_init__(self) -> None:
        values = (
            self.max_leverage,
            self.max_notional,
            self.max_correlated_exposure,
            self.max_daily_loss,
            self.max_weekly_loss,
            self.max_feed_age_ms,
        )
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("all risk limits must be finite and positive")


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
    # Faults that WOULD have blocked new exposure but were waived for a reduce-only order.
    # Never silently dropped: a flatten executed during a kill switch must be auditable.
    advisories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.action in (RiskAction.ALLOW, RiskAction.ALLOW_REDUCE_ONLY)


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def evaluate(self, intent: OrderIntent, state: RiskState) -> RiskDecision:
        """Gate an order. REDUCING risk is not the same act as TAKING it.

        A safety fault must stop new exposure. It must NOT trap an existing position: the kill
        switch, an unhealthy sequence and a stale feed were all appended unconditionally, so
        during exactly the fault that fires the kill switch a reduce-only flatten was blocked
        too. Being unable to close is the more dangerous failure.

        Waivable for reduce-only (recorded as advisories, never dropped):
            kill_switch, sequence_unhealthy, stale_feed, feed_age_unknown, model_unavailable

        NEVER waivable, because they make "reduce" unverifiable rather than merely degraded:
            unknown_position   - the order could open or flip instead of reducing
            invalid_notional   - a malformed order is not a flatten
            leverage_limit     - a reduce-only order must not raise leverage
        """
        reasons: list[str] = []
        advisories: list[str] = []
        reduce_only = bool(intent.reduce_only)

        def fault(name: str) -> None:
            """Blocks new exposure; waived-but-recorded for a reduce-only order."""
            (advisories if reduce_only else reasons).append(name)

        if state.kill_switch:
            fault("kill_switch")
        if not state.sequence_healthy:
            fault("sequence_unhealthy")
        if not math.isfinite(state.feed_age_ms):
            fault("feed_age_unknown")
        elif state.feed_age_ms > self.limits.max_feed_age_ms:
            fault("stale_feed")
        if not state.model_available:
            fault("model_unavailable")

        # Hard blocks - these apply to reduce-only orders as well.
        if not state.position_known:
            reasons.append("unknown_position")
        if intent.notional <= 0 or not math.isfinite(intent.notional):
            reasons.append("invalid_notional")
        if intent.leverage <= 0 or intent.leverage > self.limits.max_leverage:
            reasons.append("leverage_limit")

        risk_values = (
            state.daily_pnl,
            state.weekly_pnl,
            state.open_notional,
            state.correlated_exposure,
        )
        risk_state_valid = all(math.isfinite(value) for value in risk_values)
        if not risk_state_valid and not reduce_only:
            reasons.append("invalid_risk_state")

        # Exposure and loss limits restrain NEW risk only. A reduce-only order lowers all of
        # them by construction, so applying them to a flatten would block the remedy.
        if not reduce_only:
            if (
                risk_state_valid
                and state.open_notional + intent.notional > self.limits.max_notional
            ):
                reasons.append("notional_limit")
            if (
                risk_state_valid
                and
                state.correlated_exposure + intent.notional
                > self.limits.max_correlated_exposure
            ):
                reasons.append("correlated_exposure_limit")
            if risk_state_valid and state.daily_pnl <= -self.limits.max_daily_loss:
                reasons.append("daily_loss_limit")
            if risk_state_valid and state.weekly_pnl <= -self.limits.max_weekly_loss:
                reasons.append("weekly_loss_limit")

        if reasons:
            return RiskDecision(RiskAction.BLOCK, tuple(reasons), tuple(advisories))
        action = RiskAction.ALLOW_REDUCE_ONLY if (reduce_only and advisories) else RiskAction.ALLOW
        return RiskDecision(action, (), tuple(advisories))

