"""Causal prior-window breakout baseline with mandatory perp trade activity."""
from __future__ import annotations

import math
import statistics

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase


class BreakoutStrategy(StrategyBase):
    strategy_id = "breakout"
    strategy_name = "Breakout"
    strategy_version = "phase1-v1"
    timeframe = "1s samples / max 5m hold"
    required_inputs = (
        "perpetual_book",
        "perpetual_mid_history",
        "perpetual_trade_intensity",
    )

    lookback_samples = 30
    breakout_buffer_bps = 0.5
    minimum_trade_count_60s = 20
    maximum_spread_bps = 5.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300

    @property
    def parameters(self):
        return {
            "lookback_samples": self.lookback_samples,
            "breakout_buffer_bps": self.breakout_buffer_bps,
            "minimum_trade_count_60s": self.minimum_trade_count_60s,
            "maximum_spread_bps": self.maximum_spread_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
        }

    def decide(self, snapshot: MarketSnapshot):
        history = tuple(float(value) for value in snapshot.mid_history)
        base_features = {
            "mark_price": snapshot.mark_price,
            "spread_bps": snapshot.spread_bps,
            "history_count": len(history),
            "agg_trade_age_ms": snapshot.agg_trade_age_ms,
            "agg_trade_count_60s": snapshot.agg_trade_count_60s,
        }
        missing = []
        if snapshot.feed_health is not DataQuality.HEALTHY:
            missing.append("perpetual_book")
        if len(history) < self.lookback_samples + 1:
            missing.append("perpetual_mid_history")
        if not snapshot.feature_availability.get("perpetual_trade_intensity", False):
            missing.append("perpetual_trade_intensity")
        if missing:
            return self.no_data(
                snapshot, tuple(missing), base_features, "breakout_inputs_unavailable"
            )

        # Exclude the current sample. The threshold is formed only from observations
        # that existed before this decision, so a later active-window extreme cannot leak.
        prior = history[-self.lookback_samples - 1 : -1]
        prior_high = max(prior)
        prior_low = min(prior)
        buffer = snapshot.mark_price * self.breakout_buffer_bps / 10_000.0
        trade_count = int(snapshot.agg_trade_count_60s or 0)
        returns = [
            math.log(prior[index] / prior[index - 1])
            for index in range(1, len(prior))
            if prior[index] > 0 and prior[index - 1] > 0
        ]
        volatility_bps = (
            statistics.pstdev(returns) * 10_000.0 if len(returns) >= 2 else 0.0
        )
        features = {
            **base_features,
            "previous_high": prior_high,
            "previous_low": prior_low,
            "breakout_buffer_usd": buffer,
            "volatility_bps": volatility_bps,
        }
        if snapshot.spread_bps > self.maximum_spread_bps:
            return self._decision(
                snapshot,
                action=Action.NO_EDGE,
                side=None,
                score=0.0,
                confidence=0.0,
                requested_notional_usd=0.0,
                stop_price=None,
                take_profit_price=None,
                maximum_holding_seconds=self.maximum_holding_seconds,
                features=features,
                reason_codes=("spread_too_wide",),
            )
        if trade_count < self.minimum_trade_count_60s:
            return self._decision(
                snapshot,
                action=Action.NO_EDGE,
                side=None,
                score=0.0,
                confidence=0.0,
                requested_notional_usd=0.0,
                stop_price=None,
                take_profit_price=None,
                maximum_holding_seconds=self.maximum_holding_seconds,
                features=features,
                reason_codes=("trade_intensity_below_confirmation",),
            )

        upside = snapshot.best_ask > prior_high + buffer
        downside = snapshot.best_bid < prior_low - buffer
        if upside == downside:
            return self._decision(
                snapshot,
                action=Action.NO_EDGE,
                side=None,
                score=0.0,
                confidence=0.0,
                requested_notional_usd=0.0,
                stop_price=None,
                take_profit_price=None,
                maximum_holding_seconds=self.maximum_holding_seconds,
                features=features,
                reason_codes=("no_confirmed_breakout",),
            )

        side = PositionSide.LONG if upside else PositionSide.SHORT
        sign = 1.0 if side is PositionSide.LONG else -1.0
        breached_by = (
            snapshot.best_ask - prior_high if upside else prior_low - snapshot.best_bid
        )
        breach_bps = breached_by / snapshot.mark_price * 10_000.0
        strength = min(
            1.0,
            breach_bps / 5.0
            + min(0.5, trade_count / max(1, self.minimum_trade_count_60s) / 4.0),
        )
        stop_bps = max(5.0, min(35.0, volatility_bps * 2.0))
        stop_distance = snapshot.mark_price * stop_bps / 10_000.0
        target_distance = stop_distance * 1.5
        return self._decision(
            snapshot,
            action=Action.OPEN_LONG if upside else Action.OPEN_SHORT,
            side=side,
            score=sign * strength,
            confidence=min(0.85, 0.50 + strength * 0.35),
            requested_notional_usd=self.requested_notional_usd,
            stop_price=snapshot.mark_price - sign * stop_distance,
            take_profit_price=snapshot.mark_price + sign * target_distance,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=("prior_window_breakout", "perp_trade_intensity_confirmed"),
        )
