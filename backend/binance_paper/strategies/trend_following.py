"""Transparent causal EMA/momentum baseline for Binance perpetual paper trading."""
from __future__ import annotations

import math
import statistics

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase


def _ema(values: tuple[float, ...], period: int) -> float:
    alpha = 2.0 / (period + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


class TrendFollowingStrategy(StrategyBase):
    strategy_id = "trend_following"
    strategy_name = "Trend Following"
    strategy_version = "phase1-v1"
    timeframe = "1s samples / max 5m hold"
    required_inputs = ("perpetual_book", "perpetual_mid_history")

    fast_period = 8
    slow_period = 21
    momentum_lookback = 5
    minimum_strength_bps = 1.5
    maximum_spread_bps = 5.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300
    stop_sigma = 1.0
    reward_risk_ratio = 1.5
    minimum_stop_bps = 8.0
    maximum_stop_bps = 60.0

    @property
    def parameters(self):
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "momentum_lookback": self.momentum_lookback,
            "minimum_strength_bps": self.minimum_strength_bps,
            "maximum_spread_bps": self.maximum_spread_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
            "stop_sigma": self.stop_sigma,
            "reward_risk_ratio": self.reward_risk_ratio,
            "minimum_stop_bps": self.minimum_stop_bps,
            "maximum_stop_bps": self.maximum_stop_bps,
        }

    def position_exit_reason(self, position: dict, snapshot: MarketSnapshot) -> str | None:
        """Close when the EMA alignment that justified the entry has flipped against it.

        The entry condition is `alignment_bps >= +minimum_strength` for a long. Waiting for a
        price stop after the alignment has gone negative is holding a position this strategy
        would not open - the exit is the entry rule read backwards, so it introduces no new
        threshold. A merely WEAKENING trend is not an exit: only a sign flip past the same
        strength bar counts, otherwise noise around zero would churn the position.
        """
        history = tuple(float(value) for value in snapshot.mid_history)
        if snapshot.feed_health is not DataQuality.HEALTHY or len(history) < self.slow_period:
            return None                      # cannot judge the thesis; leave the stops in charge
        window = history[-self.slow_period:]
        fast = _ema(window[-self.fast_period:], self.fast_period)
        slow = _ema(window, self.slow_period)
        alignment_bps = (fast / slow - 1.0) * 10_000.0
        side = str(position.get("side", "")).upper()
        if side.endswith("LONG") and alignment_bps <= -self.minimum_strength_bps:
            return "THESIS_INVALIDATED"
        if side.endswith("SHORT") and alignment_bps >= self.minimum_strength_bps:
            return "THESIS_INVALIDATED"
        return None

    def decide(self, snapshot: MarketSnapshot):
        history = tuple(float(value) for value in snapshot.mid_history)
        base_features = {
            "mark_price": snapshot.mark_price,
            "spread_bps": snapshot.spread_bps,
            "history_count": len(history),
        }
        missing = []
        if snapshot.feed_health is not DataQuality.HEALTHY:
            missing.append("perpetual_book")
        if len(history) < self.slow_period:
            missing.append("perpetual_mid_history")
        if missing:
            return self.no_data(
                snapshot, tuple(missing), base_features, "trend_inputs_unavailable"
            )

        window = history[-self.slow_period :]
        fast = _ema(window[-self.fast_period :], self.fast_period)
        slow = _ema(window, self.slow_period)
        momentum_base = history[-1 - self.momentum_lookback]
        momentum_bps = (history[-1] / momentum_base - 1.0) * 10_000.0
        alignment_bps = (fast / slow - 1.0) * 10_000.0
        returns = [
            math.log(window[index] / window[index - 1])
            for index in range(1, len(window))
            if window[index] > 0 and window[index - 1] > 0
        ]
        volatility_bps = (
            statistics.pstdev(returns) * 10_000.0 if len(returns) >= 2 else 0.0
        )
        features = {
            **base_features,
            "ema_fast": fast,
            "ema_slow": slow,
            "alignment_bps": alignment_bps,
            "momentum_bps": momentum_bps,
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

        positive = (
            alignment_bps >= self.minimum_strength_bps
            and momentum_bps >= self.minimum_strength_bps
        )
        negative = (
            alignment_bps <= -self.minimum_strength_bps
            and momentum_bps <= -self.minimum_strength_bps
        )
        if not positive and not negative:
            return self._decision(
                snapshot,
                action=Action.NO_EDGE,
                side=None,
                score=max(-1.0, min(1.0, alignment_bps / 10.0)),
                confidence=min(0.49, abs(alignment_bps) / 20.0),
                requested_notional_usd=0.0,
                stop_price=None,
                take_profit_price=None,
                maximum_holding_seconds=self.maximum_holding_seconds,
                features=features,
                reason_codes=("weak_or_mixed_trend",),
            )

        side = PositionSide.LONG if positive else PositionSide.SHORT
        sign = 1.0 if side is PositionSide.LONG else -1.0
        # volatility_bps is the standard deviation of ONE 1-second sample. Scaling it by a bare
        # 2.0 sized the stop for a 2-second event while the position is held up to 300 seconds.
        # BTC's 1-second sd is ~0.81 bps, so `max(4.0, 0.81*2)` always collapsed to the 4.0 bps
        # floor and the target to 6.0 bps - below the 12.0 bps round trip. Scale by sqrt(time)
        # to the actual horizon instead, then floor the target above cost.
        horizon_scale = math.sqrt(max(1.0, float(self.maximum_holding_seconds)))
        stop_bps = max(
            self.minimum_stop_bps,
            min(self.maximum_stop_bps, volatility_bps * horizon_scale * self.stop_sigma),
        )
        target_bps = max(stop_bps * self.reward_risk_ratio, self.minimum_take_profit_bps)
        stop_distance = snapshot.mark_price * stop_bps / 10_000.0
        target_distance = snapshot.mark_price * target_bps / 10_000.0
        features["stop_bps"] = stop_bps
        features["target_bps"] = target_bps
        strength = min(1.0, (abs(alignment_bps) + abs(momentum_bps)) / 30.0)
        return self._decision(
            snapshot,
            action=Action.OPEN_LONG if positive else Action.OPEN_SHORT,
            side=side,
            score=sign * strength,
            confidence=min(0.85, 0.50 + strength * 0.35),
            requested_notional_usd=self.requested_notional_usd,
            stop_price=snapshot.mark_price - sign * stop_distance,
            take_profit_price=snapshot.mark_price + sign * target_distance,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=("ema_momentum_alignment",),
        )
