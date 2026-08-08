"""Fail-closed capital-preservation policy for Binance paper portfolios."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Iterable

from .config import StrategyRiskConfig
from .schemas import DataQuality, MarketSnapshot


class GovernorMode(str, Enum):
    NORMAL = "NORMAL"
    REDUCED_SIZE = "REDUCED_SIZE"
    NO_NEW_ENTRIES = "NO_NEW_ENTRIES"
    CLOSE_ONLY = "CLOSE_ONLY"
    EMERGENCY_FLATTEN = "EMERGENCY_FLATTEN"


@dataclass(frozen=True)
class GovernorAccountState:
    strategy_id: str
    starting_cash_usd: float
    equity_usd: float
    peak_equity_usd: float
    daily_net_pnl_usd: float
    weekly_net_pnl_usd: float
    risk: StrategyRiskConfig


@dataclass(frozen=True)
class GovernorDecision:
    mode: GovernorMode
    can_open: bool
    size_multiplier: float
    must_flatten: bool
    reason_codes: tuple[str, ...]
    evaluated_at_ms: int
    portfolio_drawdown_fraction: float | None
    daily_loss_limit_fraction: float | None
    weekly_loss_limit_fraction: float | None
    oldest_pending_age_ms: int | None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["mode"] = self.mode.value
        return value


class CapitalPreservationGovernor:
    """Aggregate safety layer above each strategy's independent risk gate."""

    def __init__(self, *, latency_ms: int, quote_stale_ms: int):
        self.pending_unknown_ms = max(10_000, latency_ms * 4 + quote_stale_ms)

    @staticmethod
    def _loss_fraction(loss_usd: float, limit_usd: float) -> float:
        return max(0.0, -loss_usd) / max(1e-9, limit_usd)

    def evaluate(
        self,
        *,
        snapshot: MarketSnapshot | None,
        accounts: Iterable[GovernorAccountState],
        pending_ages_ms: Iterable[int] = (),
        integrity_error: str | None = None,
        now_ms: int,
    ) -> GovernorDecision:
        states = tuple(accounts)
        pending_ages = tuple(max(0, int(value)) for value in pending_ages_ms)
        oldest_pending = max(pending_ages, default=None)
        reasons: list[str] = []

        numeric_values = [
            value
            for state in states
            for value in (
                state.starting_cash_usd,
                state.equity_usd,
                state.peak_equity_usd,
                state.daily_net_pnl_usd,
                state.weekly_net_pnl_usd,
                state.risk.maximum_daily_loss_usd,
                state.risk.maximum_weekly_loss_usd,
            )
        ]
        invalid_account_state = (
            not states
            or not all(math.isfinite(float(value)) for value in numeric_values)
            or any(
                state.starting_cash_usd <= 0
                or state.peak_equity_usd <= 0
                or state.risk.maximum_daily_loss_usd <= 0
                or state.risk.maximum_weekly_loss_usd <= 0
                for state in states
            )
        )

        # CAPITAL EXHAUSTION IS A TERMINAL STATE, AND IT HAD NO NAME.
        #
        # Nothing in this engine stopped a strategy whose money was gone. The nearest thing
        # was `peak_equity_usd <= 0` inside `invalid_or_missing_account_state`, which tests
        # the PEAK - a strategy that started at 250, peaked at 300 and fell to 0 has a
        # perfectly valid peak and would have kept trading on an empty account.
        #
        # The stake is fixed and never topped up, so reaching zero is the run ENDING, not an
        # error. It is reported under its own name so the outcome is unambiguous: the
        # strategy is ruined, and that is the answer rather than a missing one.
        #
        # `capital_below_minimum_position` is the softer sibling: still solvent, but with too
        # little left to open the smallest position the risk config permits, so it cannot
        # produce further evidence either.
        ruined = [st for st in states if float(st.equity_usd) <= 0.0]
        starved = [
            st for st in states
            if float(st.equity_usd) > 0.0
            and float(st.equity_usd) < float(st.risk.max_position_notional_usd)
        ]

        portfolio_drawdown: float | None = None
        daily_fraction: float | None = None
        weekly_fraction: float | None = None
        if not invalid_account_state:
            total_peak = sum(state.peak_equity_usd for state in states)
            total_equity = sum(state.equity_usd for state in states)
            portfolio_drawdown = max(0.0, total_peak - total_equity) / total_peak
            total_daily_limit = sum(
                state.risk.maximum_daily_loss_usd for state in states
            )
            total_weekly_limit = sum(
                state.risk.maximum_weekly_loss_usd for state in states
            )
            daily_fraction = self._loss_fraction(
                sum(state.daily_net_pnl_usd for state in states),
                total_daily_limit,
            )
            weekly_fraction = self._loss_fraction(
                sum(state.weekly_net_pnl_usd for state in states),
                total_weekly_limit,
            )
            weighted_drawdown_limit = sum(
                state.starting_cash_usd * state.risk.maximum_drawdown_fraction
                for state in states
            ) / sum(state.starting_cash_usd for state in states)
            drawdown_fraction_of_limit = portfolio_drawdown / max(
                1e-9, weighted_drawdown_limit
            )
        else:
            drawdown_fraction_of_limit = float("inf")

        if invalid_account_state:
            reasons.append("invalid_or_missing_account_state")
        if integrity_error:
            reasons.append("persistence_integrity_unknown")
        if snapshot is None:
            reasons.append("market_snapshot_missing")
        elif (
            snapshot.feed_health is not DataQuality.HEALTHY
            or snapshot.feed_age_ms > self.pending_unknown_ms
        ):
            reasons.append("market_feed_not_healthy")
        if oldest_pending is not None and oldest_pending > self.pending_unknown_ms:
            reasons.append("pending_order_state_overdue")

        loss_severity = max(
            drawdown_fraction_of_limit,
            daily_fraction or 0.0,
            weekly_fraction or 0.0,
        )
        if ruined:
            # Checked FIRST: an account at zero is not a drawdown to be sized down, and
            # naming it "severely breached" would file the end of the run under a limit.
            mode = GovernorMode.EMERGENCY_FLATTEN
            reasons.append("capital_exhausted")
        elif invalid_account_state or loss_severity >= 1.5:
            mode = GovernorMode.EMERGENCY_FLATTEN
            reasons.append("capital_limit_severely_breached")
        elif loss_severity >= 1.0:
            mode = GovernorMode.CLOSE_ONLY
            reasons.append("capital_limit_breached")
        elif integrity_error or "pending_order_state_overdue" in reasons:
            mode = GovernorMode.NO_NEW_ENTRIES
        elif "market_snapshot_missing" in reasons or "market_feed_not_healthy" in reasons:
            mode = GovernorMode.NO_NEW_ENTRIES
        elif starved:
            mode = GovernorMode.CLOSE_ONLY
            reasons.append("capital_below_minimum_position")
        elif loss_severity >= 0.5:
            mode = GovernorMode.REDUCED_SIZE
            reasons.append("capital_limit_half_consumed")
        else:
            mode = GovernorMode.NORMAL

        return GovernorDecision(
            mode=mode,
            can_open=mode in (GovernorMode.NORMAL, GovernorMode.REDUCED_SIZE),
            size_multiplier=0.5 if mode is GovernorMode.REDUCED_SIZE else (
                1.0 if mode is GovernorMode.NORMAL else 0.0
            ),
            must_flatten=mode is GovernorMode.EMERGENCY_FLATTEN,
            reason_codes=tuple(dict.fromkeys(reasons)),
            evaluated_at_ms=int(now_ms),
            portfolio_drawdown_fraction=portfolio_drawdown,
            daily_loss_limit_fraction=daily_fraction,
            weekly_loss_limit_fraction=weekly_fraction,
            oldest_pending_age_ms=oldest_pending,
        )
