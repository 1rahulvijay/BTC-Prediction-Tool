"""Single mandatory risk gate for every Binance paper entry."""
from __future__ import annotations

import time

from .config import EngineConfig, StrategyRiskConfig
from .fill_simulator import binance_fee
from .schemas import Action, DataQuality, MarketSnapshot, PositionSide, RiskResult, StrategyDecision


class BinancePaperRiskEngine:
    def __init__(self, engine_config: EngineConfig):
        self.engine_config = engine_config

    def evaluate_entry(
        self,
        *,
        decision: StrategyDecision,
        snapshot: MarketSnapshot,
        account: dict,
        open_position: dict | None,
        risk: StrategyRiskConfig,
        persistence,
        runtime_active: bool,
        strategy_enabled: bool,
        signal_already_seen: bool,
        now_ms: int | None = None,
    ) -> RiskResult:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        risk = risk.clamped()
        reasons: list[str] = []
        if not self.engine_config.hard_enabled:
            reasons.append("engine_environment_gate_disabled")
        if not runtime_active:
            reasons.append("engine_paused")
        if not strategy_enabled or not risk.enabled:
            reasons.append("strategy_disabled")
        if decision.action not in (Action.OPEN_LONG, Action.OPEN_SHORT):
            reasons.append("not_an_entry_signal")
        if decision.side is None:
            reasons.append("entry_side_missing")
        if signal_already_seen:
            reasons.append("duplicate_signal")
        if decision.missing_inputs:
            reasons.append("required_input_missing")
        if decision.data_quality_status is not DataQuality.HEALTHY:
            reasons.append("strategy_data_not_healthy")
        if snapshot.feed_health is not DataQuality.HEALTHY:
            reasons.append("stale_market_data")
        if snapshot.feed_age_ms > self.engine_config.quote_stale_ms:
            reasons.append("stale_market_data")
        if snapshot.spread_bps > risk.maximum_spread_bps:
            reasons.append("spread_too_wide")
        if risk.stop_required and decision.stop_price is None:
            reasons.append("stop_required")
        if decision.take_profit_price is None:
            reasons.append("take_profit_required")
        if decision.side is PositionSide.LONG:
            if (
                decision.stop_price is not None
                and decision.stop_price >= snapshot.mark_price
            ):
                reasons.append("invalid_long_stop")
            if (
                decision.take_profit_price is not None
                and decision.take_profit_price <= snapshot.mark_price
            ):
                reasons.append("invalid_long_target")
        if decision.side is PositionSide.SHORT:
            if (
                decision.stop_price is not None
                and decision.stop_price <= snapshot.mark_price
            ):
                reasons.append("invalid_short_stop")
            if (
                decision.take_profit_price is not None
                and decision.take_profit_price >= snapshot.mark_price
            ):
                reasons.append("invalid_short_target")
        if decision.side is PositionSide.LONG and not risk.allow_long:
            reasons.append("long_disabled")
        if decision.side is PositionSide.SHORT and not risk.allow_short:
            reasons.append("short_disabled")
        if open_position is not None:
            reasons.append("existing_position_conflict")
        if risk.leverage > risk.max_leverage:
            reasons.append("leverage_limit")
        drawdown_fraction = float(account["maximum_drawdown_usd"]) / max(
            1e-9, float(account["starting_cash_usd"])
        )
        if drawdown_fraction >= risk.maximum_drawdown_fraction:
            reasons.append("maximum_drawdown_reached")
        day_start_ms = (now // 86_400_000) * 86_400_000
        if persistence.daily_net_pnl(decision.strategy_id, day_start_ms) <= -risk.maximum_daily_loss_usd:
            reasons.append("maximum_daily_loss_reached")
        if (
            persistence.recent_trade_count(decision.strategy_id, now - 3_600_000)
            >= risk.maximum_trades_per_hour
        ):
            reasons.append("maximum_trades_per_hour_reached")
        last_exit = persistence.last_exit_time_ms(decision.strategy_id)
        if (
            last_exit is not None
            and now - last_exit < risk.cooldown_seconds * 1000
        ):
            reasons.append("cooldown_active")

        top_price = (
            snapshot.best_ask
            if decision.side is PositionSide.LONG
            else snapshot.best_bid
        )
        slippage_fraction = self.engine_config.slippage_bps / 10_000.0
        reference_price = (
            top_price * (1.0 + slippage_fraction)
            if decision.side is PositionSide.LONG
            else top_price * (1.0 - slippage_fraction)
        )
        requested = min(
            max(0.0, decision.requested_notional_usd),
            risk.max_position_notional_usd,
            risk.max_account_exposure_usd,
        )
        if requested <= 0 or reference_price <= 0:
            reasons.append("non_positive_order")
            approved_notional = 0.0
        else:
            stop_distance = (
                abs(reference_price - float(decision.stop_price))
                if decision.stop_price is not None
                else 0.0
            )
            if stop_distance <= 0:
                reasons.append("invalid_stop_distance")
                risk_notional = 0.0
            else:
                stop_fraction = stop_distance / reference_price
                risk_budget = float(account["equity_usd"]) * risk.risk_per_trade_fraction
                risk_notional = risk_budget / max(1e-9, stop_fraction)
            approved_notional = min(requested, risk_notional)
        quantity = approved_notional / reference_price if reference_price > 0 else 0.0
        visible_size = (
            snapshot.ask_size
            if decision.side is PositionSide.LONG
            else snapshot.bid_size
        )
        fill_fraction = min(1.0, visible_size / quantity) if quantity > 0 else 0.0
        if fill_fraction + 1e-12 < risk.minimum_fill_fraction:
            reasons.append("insufficient_visible_liquidity")
        margin = approved_notional / risk.leverage if risk.leverage > 0 else float("inf")
        estimated_fee = binance_fee(approved_notional, self.engine_config.fee_rate_bps)
        if margin + estimated_fee > float(account["available_cash_usd"]) + 1e-9:
            reasons.append("insufficient_paper_cash")

        deduped = tuple(dict.fromkeys(reasons))
        if deduped:
            return RiskResult(False, deduped, 0.0, 0.0)
        return RiskResult(True, (), approved_notional, quantity)
