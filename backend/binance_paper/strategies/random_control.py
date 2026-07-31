"""A deliberately zero-information strategy. The benchmark every other strategy is read against.

WHY A STRATEGY THAT IS DESIGNED NOT TO WORK IS THE MOST USEFUL ONE HERE

    Every research result in this repository that looked like an edge died on contact with a
    matched control:

      * the `+10/-20` first-passage structure showed a +5.8 edge for flow_imbalance, for
        flow_reversal, AND for a zero-information momentum baseline - it was a property of BTC's
        path distribution, not of any signal;
      * BREAKOUT_BRACKET_V1 lost in all nine configurations, and the control lost just as much,
        which is what proved the loss was structural rather than a signal failure.

    The paper engine had no such control. `trend_following` and `breakout` posted P&L against
    nothing, so a positive number could not be distinguished from BTC drift, and a negative one
    could not be distinguished from the cost of trading at all. This strategy supplies the
    missing denominator.

WHAT IT DOES

    Opens a position on a deterministic pseudo-random schedule, with a side chosen by the same
    generator, using the SAME notional, the SAME stop and target geometry and the SAME maximum
    holding period as the strategies it benchmarks. It reads no price feature. Its expected
    gross edge is exactly zero, so whatever it earns is path structure, and whatever it loses is
    the cost of participation.

    Any strategy that does not beat this one has demonstrated nothing.

DETERMINISM
    Deterministic by construction: the entry schedule and side are derived by hashing
    (strategy_id, version, seed, timestamp bucket). Identical inputs give identical decisions, so
    the control is reproducible across restarts and replays. It never calls a global RNG, which
    would make its own results unrepeatable and defeat the purpose.

    It still respects every guard the real strategies respect - feed health, spread ceiling and
    the take-profit-versus-cost floor enforced in StrategyBase.
"""
from __future__ import annotations

import hashlib
import math
import statistics

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase


class RandomControlStrategy(StrategyBase):
    strategy_id = "random_control"
    strategy_name = "Random Control (zero-information benchmark)"
    strategy_version = "control-v1"
    timeframe = "1s samples / max 5m hold"
    required_inputs = ("perpetual_book", "perpetual_mid_history")

    # Matched to trend_following and breakout so the comparison is like for like.
    lookback_samples = 21
    maximum_spread_bps = 5.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300
    stop_sigma = 1.0
    reward_risk_ratio = 1.5
    minimum_stop_bps = 8.0
    maximum_stop_bps = 60.0

    # One entry per ~entry_period_seconds on average, so the control's trade COUNT is comparable
    # to the signal strategies rather than swamping or undersampling them.
    entry_period_seconds = 600
    seed = "binance-paper-control-v1"

    @property
    def parameters(self):
        return {
            "lookback_samples": self.lookback_samples,
            "maximum_spread_bps": self.maximum_spread_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
            "stop_sigma": self.stop_sigma,
            "reward_risk_ratio": self.reward_risk_ratio,
            "minimum_stop_bps": self.minimum_stop_bps,
            "maximum_stop_bps": self.maximum_stop_bps,
            "entry_period_seconds": self.entry_period_seconds,
            "seed": self.seed,
        }

    def _draw(self, timestamp_ms: int) -> tuple[bool, bool]:
        """(should_enter, go_long) from a hash. Deterministic, not a global RNG."""
        bucket = int(timestamp_ms) // 1000
        digest = hashlib.sha256(
            f"{self.strategy_id}|{self.strategy_version}|{self.seed}|{bucket}".encode()
        ).digest()
        enter_draw = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        side_draw = digest[4] & 1
        should_enter = enter_draw < (1.0 / max(1, self.entry_period_seconds))
        return should_enter, bool(side_draw)

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
        if len(history) < self.lookback_samples:
            missing.append("perpetual_mid_history")
        if missing:
            return self.no_data(
                snapshot, tuple(missing), base_features, "control_inputs_unavailable"
            )

        window = history[-self.lookback_samples :]
        returns = [
            math.log(window[index] / window[index - 1])
            for index in range(1, len(window))
            if window[index] > 0 and window[index - 1] > 0
        ]
        volatility_bps = (
            statistics.pstdev(returns) * 10_000.0 if len(returns) >= 2 else 0.0
        )
        should_enter, go_long = self._draw(snapshot.received_at_ms)
        features = {
            **base_features,
            "volatility_bps": volatility_bps,
            "control_entry_draw": should_enter,
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
        if not should_enter:
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
                reason_codes=("control_no_entry_this_sample",),
            )

        side = PositionSide.LONG if go_long else PositionSide.SHORT
        sign = 1.0 if go_long else -1.0
        horizon_scale = math.sqrt(max(1.0, float(self.maximum_holding_seconds)))
        stop_bps = max(
            self.minimum_stop_bps,
            min(self.maximum_stop_bps, volatility_bps * horizon_scale * self.stop_sigma),
        )
        target_bps = max(stop_bps * self.reward_risk_ratio, self.minimum_take_profit_bps)
        features["stop_bps"] = stop_bps
        features["target_bps"] = target_bps
        return self._decision(
            snapshot,
            action=Action.OPEN_LONG if go_long else Action.OPEN_SHORT,
            side=side,
            # score and confidence are ZERO by construction: this strategy makes no claim.
            # Reporting a confidence here would make the control look like a forecast.
            score=0.0,
            confidence=0.0,
            requested_notional_usd=self.requested_notional_usd,
            stop_price=snapshot.mark_price - sign * snapshot.mark_price * stop_bps / 10_000.0,
            take_profit_price=snapshot.mark_price
            + sign * snapshot.mark_price * target_bps / 10_000.0,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=("zero_information_control", "not_a_forecast"),
        )
