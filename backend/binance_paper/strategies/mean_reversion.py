"""Causal mean reversion: fade a stretched move, the opposite species to breakout.

WHY THIS ONE AND NOT ANOTHER MOMENTUM VARIANT

    The paper registry held two strategies and both were CONTINUATION bets - trend_following
    buys aligned EMAs, breakout buys a new extreme. Adding a third continuation variant would
    test the same hypothesis a third time, and the research suite has already answered it: the
    breakout bracket lost in all nine configurations, and the control lost equally, so the loss
    was structural rather than a tuning failure.

    Mean reversion is the one directional species the paper lane has never run. It is also the
    species that a 1-second microstructure sample is most likely to see, because short-horizon
    quote displacement is frequently inventory-driven and reverts, whereas continuation at this
    horizon must overcome the spread it just crossed.

    This is a HYPOTHESIS, not a claim. It is expected to be read against random_control, and if
    it does not beat that control it has established nothing - which is exactly the standard the
    research suite applies and the paper lane previously could not.

WHAT IT DOES

    Enters AGAINST a displacement that is large relative to recent volatility, but only when the
    move looks like noise rather than information:

      * z-score of the current mid against a causal rolling window must exceed entry_z;
      * the displacement must NOT be accompanied by a trade-intensity surge - a stretched price
        on heavy volume is more likely to be information being priced in, which is precisely the
        case where fading loses. This veto is the difference between fading noise and standing
        in front of a repricing.

    Every window excludes the current sample, so no threshold is formed from the observation
    being judged.
"""
from __future__ import annotations

import math
import statistics

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase


class MeanReversionStrategy(StrategyBase):
    strategy_id = "mean_reversion"
    strategy_name = "Mean Reversion (fade stretched, low-intensity moves)"
    strategy_version = "phase2-v1"
    timeframe = "1s samples / max 5m hold"
    required_inputs = (
        "perpetual_book",
        "perpetual_mid_history",
        "perpetual_trade_intensity",
    )

    lookback_samples = 60
    entry_z = 2.0
    # A stretched price on heavy tape is information, not noise. Fading it is the losing half of
    # this trade, so a tape surge vetoes the entry.
    #
    # This is an ABSOLUTE ceiling, matching breakout's absolute floor, because MarketSnapshot
    # carries no intensity baseline and a strategy may not hold state across samples without
    # breaking replay determinism. An earlier draft compared against a
    # `snapshot.agg_trade_count_baseline` that does not exist; the getattr fallback would have
    # made this strategy permanently NO_EDGE - alive-looking and never trading.
    #
    # Set from the recorded distribution rather than invented. Rolling 60-second aggTrade counts
    # over 33,753 observed seconds: p50 807, p75 1196, p90 1699, p95 2114, p99 2974.
    # 1700 is p90: the busiest tenth of the tape is treated as information and skipped.
    maximum_trade_count_60s = 1700
    maximum_spread_bps = 5.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300
    stop_sigma = 1.5
    reward_risk_ratio = 1.0
    # A fade that has extended this multiple past its entry threshold is no longer a
    # stretched move reverting - it is a move continuing, which is the losing half.
    extension_stop_multiple = 1.5
    minimum_stop_bps = 8.0
    maximum_stop_bps = 60.0

    @property
    def parameters(self):
        return {
            "lookback_samples": self.lookback_samples,
            "entry_z": self.entry_z,
            "extension_stop_multiple": self.extension_stop_multiple,
            "maximum_trade_count_60s": self.maximum_trade_count_60s,
            "maximum_spread_bps": self.maximum_spread_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
            "stop_sigma": self.stop_sigma,
            "reward_risk_ratio": self.reward_risk_ratio,
            "minimum_stop_bps": self.minimum_stop_bps,
            "maximum_stop_bps": self.maximum_stop_bps,
        }

    def position_exit_reason(self, position: dict, snapshot: MarketSnapshot) -> str | None:
        """A fade has TWO dynamic exits, because its thesis can complete as well as break.

        The trade is "price is stretched and will revert". So:
          * reversion ACHIEVED - z has crossed back through zero. The reason for the position is
            gone; holding on is a new bet in the opposite direction that this strategy never
            made. Static take-profit would keep waiting for a price it has no reason to expect.
          * thesis BROKEN - z has extended well past the entry threshold. Fading a move that
            keeps going is the losing half of this trade, and waiting for the stop pays more for
            the same information.

        Both are read off the same z-score the entry used, so no new threshold is introduced
        beyond the extension multiple.
        """
        history = tuple(float(value) for value in snapshot.mid_history)
        if snapshot.feed_health is not DataQuality.HEALTHY or len(history) < self.lookback_samples + 1:
            return None
        prior = history[-self.lookback_samples - 1:-1]
        dispersion = statistics.pstdev(prior)
        if dispersion <= 0:
            return None
        z_score = (snapshot.mark_price - statistics.fmean(prior)) / dispersion
        side = str(position.get("side", "")).upper()
        # A long was opened because z was NEGATIVE (price below the mean).
        if side.endswith("LONG"):
            if z_score >= 0.0:
                return "REVERSION_ACHIEVED"
            if z_score <= -self.entry_z * self.extension_stop_multiple:
                return "THESIS_INVALIDATED"
        elif side.endswith("SHORT"):
            if z_score <= 0.0:
                return "REVERSION_ACHIEVED"
            if z_score >= self.entry_z * self.extension_stop_multiple:
                return "THESIS_INVALIDATED"
        return None

    def decide(self, snapshot: MarketSnapshot):
        history = tuple(float(value) for value in snapshot.mid_history)
        base_features = {
            "mark_price": snapshot.mark_price,
            "spread_bps": snapshot.spread_bps,
            "history_count": len(history),
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
                snapshot, tuple(missing), base_features, "mean_reversion_inputs_unavailable"
            )

        # Exclude the current sample: the reference must predate the observation being judged.
        prior = history[-self.lookback_samples - 1 : -1]
        mean = statistics.fmean(prior)
        dispersion = statistics.pstdev(prior)
        returns = [
            math.log(prior[index] / prior[index - 1])
            for index in range(1, len(prior))
            if prior[index] > 0 and prior[index - 1] > 0
        ]
        volatility_bps = (
            statistics.pstdev(returns) * 10_000.0 if len(returns) >= 2 else 0.0
        )
        z_score = (
            (snapshot.mark_price - mean) / dispersion if dispersion > 0 else 0.0
        )
        features = {
            **base_features,
            "window_mean": mean,
            "window_dispersion": dispersion,
            "z_score": z_score,
            "volatility_bps": volatility_bps,
        }

        def no_edge(*codes: str):
            return self._decision(
                snapshot,
                action=Action.NO_EDGE,
                side=None,
                score=max(-1.0, min(1.0, -z_score / 4.0)),
                confidence=0.0,
                requested_notional_usd=0.0,
                stop_price=None,
                take_profit_price=None,
                maximum_holding_seconds=self.maximum_holding_seconds,
                features=features,
                reason_codes=codes,
            )

        if snapshot.spread_bps > self.maximum_spread_bps:
            return no_edge("spread_too_wide")
        if dispersion <= 0:
            return no_edge("degenerate_window_dispersion")
        if abs(z_score) < self.entry_z:
            return no_edge("displacement_below_entry_threshold")

        # Intensity veto: a stretched price on a surging tape is information being priced in,
        # and fading that is the losing half of this trade.
        trade_count = snapshot.agg_trade_count_60s
        if trade_count is None:
            # An unevaluated veto must block the trade, never wave it through.
            return no_edge("intensity_unavailable")
        features["trade_count_60s"] = int(trade_count)
        if int(trade_count) > self.maximum_trade_count_60s:
            return no_edge("intensity_surge_suggests_information")

        # Fade: price above the mean is sold, price below is bought.
        go_long = z_score < 0
        side = PositionSide.LONG if go_long else PositionSide.SHORT
        sign = 1.0 if go_long else -1.0
        horizon_scale = math.sqrt(max(1.0, float(self.maximum_holding_seconds)))
        stop_bps = max(
            self.minimum_stop_bps,
            min(self.maximum_stop_bps, volatility_bps * horizon_scale * self.stop_sigma),
        )
        target_bps = max(stop_bps * self.reward_risk_ratio, self.minimum_take_profit_bps)
        strength = min(1.0, (abs(z_score) - self.entry_z) / 2.0 + 0.25)
        features["stop_bps"] = stop_bps
        features["target_bps"] = target_bps
        return self._decision(
            snapshot,
            action=Action.OPEN_LONG if go_long else Action.OPEN_SHORT,
            side=side,
            score=sign * strength,
            confidence=min(0.75, 0.40 + strength * 0.35),
            requested_notional_usd=self.requested_notional_usd,
            stop_price=snapshot.mark_price - sign * snapshot.mark_price * stop_bps / 10_000.0,
            take_profit_price=snapshot.mark_price
            + sign * snapshot.mark_price * target_bps / 10_000.0,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=("stretched_displacement_faded", "intensity_veto_passed"),
        )
