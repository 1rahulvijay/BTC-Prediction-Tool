"""A paper strategy must be capable of profit before anyone reads its P&L.

THE DEFECT THIS ENCODES

    Both Phase-1 strategies sized their stop from `volatility_bps * 2.0`, where volatility_bps
    is the standard deviation of ONE 1-second sample. BTC's 1-second sd is ~0.81 bps, so:

        stop_bps = max(4.0, min(30.0, 0.81 * 2)) = max(4.0, 1.61) = 4.0   <- floor always bound
        target   = stop * 1.5                                     = 6.0 bps
        round trip = 2 x (fee 5.0 + slippage 1.0)                 = 12.0 bps

    Every trade that reached its take-profit still lost 6.0 bps. The strategies were not
    unlikely to be profitable - they were arithmetically incapable of it. Nothing downstream
    caught it because the accounting was correct; only the intent was wrong, and no test asked
    whether a winning trade wins.

    The stop was scaled to a 2-second event while the position is held for up to 300 seconds.
    Fixed by scaling volatility by sqrt(holding_seconds) and flooring the target above cost.

WHAT IS ASSERTED

    1. Every registered strategy's take-profit clears the round trip, checked on a synthetic
       snapshot rather than by reading constants - the arithmetic must hold after the code runs.
    2. StrategyBase REFUSES a target inside the round trip, so a future strategy cannot
       reintroduce this by choosing its own geometry.
    3. The engine's configured cost does not exceed what strategies assume. Raising
       BTC_BINANCE_PAPER_FEE_BPS without revisiting the strategies must fail here, not bleed.
    4. The control strategy is deterministic and makes no forecast claim.

INVOCATION
    python -m backend.binance_paper.test_strategy_economics

    Module invocation, matching every other test in this package, and NOT optional here:
    `backend/binance_paper/types.py` shadows the standard library `types` module. Running any
    file in this directory as a script puts the directory on sys.path[0], where the shadow
    breaks Python's own import machinery before the first line of the test executes:

        ImportError: cannot import name 'MappingProxyType' from 'types'

    The shadowing is pre-existing and harmless under package imports. It is noted in
    docs/VALIDATION_SWEEP_2026-07-31.md rather than fixed here, because renaming a module this
    package imports from is a wider change than this commit should carry.
"""
from __future__ import annotations

import math

from backend.binance_paper.config import EngineConfig
from backend.binance_paper.schemas import (
    Action,
    DataQuality,
    MarketSnapshot,
)
from backend.binance_paper.strategy_registry import (
    CONTROL_STRATEGY_ID,
    StrategyRegistry,
)

MARK = 64_000.0


def snapshot(mid_history, *, trade_count=800, spread_bps=1.0, timestamp_ms=1_700_000_000_000):
    half = MARK * spread_bps / 2.0 / 10_000.0
    return MarketSnapshot(
        symbol="BTCUSDT",
        event_ts_ms=timestamp_ms,
        received_at_ms=timestamp_ms,
        mark_price=MARK,
        best_bid=MARK - half,
        best_ask=MARK + half,
        bid_size=5.0,
        ask_size=5.0,
        spread=2.0 * half,
        spread_bps=spread_bps,
        feed_age_ms=50,
        feed_health=DataQuality.HEALTHY,
        update_id=1,
        funding_rate=0.0001,
        funding_time_ms=timestamp_ms + 3_600_000,
        agg_trade_age_ms=100,
        agg_trade_message_count=trade_count,
        agg_trade_count_60s=trade_count,
        last_completed_perp_cvd_bar_ts_ms=timestamp_ms - 60_000,
        mid_history=tuple(mid_history),
        feature_availability={"perpetual_trade_intensity": True},
    )


def _trending_history(n=120, step_bps=3.0):
    """Monotone ramp: triggers both continuation strategies."""
    return [MARK * (1.0 + step_bps / 10_000.0 * i) for i in range(-n, 1)]


def _stretched_history(n=120):
    """Flat window then a sharp displacement: triggers mean reversion."""
    values = [MARK * (1.0 + 0.00001 * ((i % 3) - 1)) for i in range(n)]
    return values + [MARK]


def test_every_strategy_target_clears_the_round_trip():
    """The arithmetic must hold after the code runs, not merely in the constants."""
    registry = StrategyRegistry()
    opened = 0
    for strategy in registry.all():
        for history in (_trending_history(), _stretched_history()):
            for timestamp in range(1_700_000_000_000, 1_700_000_000_000 + 4_000, 1_000):
                decision = strategy.decide(
                    snapshot(history, timestamp_ms=timestamp)
                )
                if decision.action not in (Action.OPEN_LONG, Action.OPEN_SHORT):
                    continue
                opened += 1
                assert decision.take_profit_price is not None
                target_bps = (
                    abs(decision.take_profit_price - MARK) / MARK * 10_000.0
                )
                assert target_bps >= strategy.assumed_round_trip_bps, (
                    f"{strategy.strategy_id}: target {target_bps:.2f} bps does not clear the "
                    f"{strategy.assumed_round_trip_bps:.2f} bps round trip"
                )
    # A vacuous pass is the failure mode this repository keeps hitting: if no strategy ever
    # opened, the loop above asserts nothing at all.
    assert opened >= 3, f"no strategy opened a position; the assertions never ran (opened={opened})"


def test_base_refuses_a_target_inside_the_round_trip():
    """A future strategy cannot reintroduce the defect by choosing its own geometry."""
    registry = StrategyRegistry()
    strategy = registry.get("trend_following")
    snap = snapshot(_trending_history())
    from backend.binance_paper.schemas import PositionSide

    too_close = MARK * (1.0 + (strategy.assumed_round_trip_bps - 1.0) / 10_000.0)
    raised = False
    try:
        strategy._decision(
            snap,
            action=Action.OPEN_LONG,
            side=PositionSide.LONG,
            score=0.5,
            confidence=0.6,
            requested_notional_usd=500.0,
            stop_price=MARK * 0.999,
            take_profit_price=too_close,
            maximum_holding_seconds=300,
            features={},
        )
    except ValueError as exc:
        raised = True
        assert "round trip" in str(exc)
    assert raised, "a take-profit inside the round trip must raise, not be silently accepted"


def test_engine_cost_does_not_exceed_what_strategies_assume():
    """Raising fees without revisiting strategies must fail here rather than bleed in paper."""
    config = EngineConfig.from_env()
    actual_round_trip = 2.0 * (config.fee_rate_bps + config.slippage_bps)
    for strategy in StrategyRegistry().all():
        assert strategy.assumed_round_trip_bps >= actual_round_trip, (
            f"{strategy.strategy_id} assumes {strategy.assumed_round_trip_bps} bps but the "
            f"engine charges {actual_round_trip} bps per round trip"
        )


def test_stop_is_scaled_to_the_holding_horizon_not_one_sample():
    """The original bug: a 1-second sigma sized a stop held for 300 seconds."""
    strategy = StrategyRegistry().get("trend_following")
    one_second_sigma_bps = 0.81
    naive = max(4.0, min(30.0, one_second_sigma_bps * 2.0))
    horizon = one_second_sigma_bps * math.sqrt(strategy.maximum_holding_seconds)
    assert naive == 4.0, "the old floor always bound, which is why the target was fixed at 6 bps"
    assert naive * 1.5 < strategy.assumed_round_trip_bps, (
        "the old target sat below the round trip - this is the defect being encoded"
    )
    assert horizon > strategy.assumed_round_trip_bps / 2.0, (
        "a horizon-scaled sigma must be large enough for a 1.5x target to clear costs"
    )


def test_control_is_deterministic_and_claims_nothing():
    """A control that varied run to run, or reported confidence, would not be a control."""
    control = StrategyRegistry().get(CONTROL_STRATEGY_ID)
    history = _trending_history()
    first = [control.decide(snapshot(history, timestamp_ms=t)).signal_id
             for t in range(1_700_000_000_000, 1_700_000_000_000 + 20_000, 1_000)]
    second = [control.decide(snapshot(history, timestamp_ms=t)).signal_id
              for t in range(1_700_000_000_000, 1_700_000_000_000 + 20_000, 1_000)]
    assert first == second, "the control must be reproducible across runs"

    for timestamp in range(1_700_000_000_000, 1_700_000_000_000 + 600_000, 1_000):
        decision = control.decide(snapshot(history, timestamp_ms=timestamp))
        if decision.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            assert decision.score == 0.0, "the control must not report a directional score"
            assert decision.confidence == 0.0, "the control must not report confidence"
            assert "zero_information_control" in decision.reason_codes
            break
    else:
        raise AssertionError("the control never opened a position in 600 samples")


def test_dynamic_exit_fires_and_control_stays_static():
    """Entry and exit must be one rule - and the control must NOT get a thesis.

    A zero-information benchmark that reacted to a thesis would no longer be zero-information,
    and every other strategy is read against it. If random_control ever grows a reassess(), the
    comparisons in the paper lane silently stop meaning anything, so this asserts it does not.
    """
    from backend.binance_paper.portfolio import BinancePaperPortfolio as Portfolio

    registry = StrategyRegistry()
    trend = registry.get("trend_following")

    # A long opened on a rising ramp, then the ramp reverses hard: alignment flips negative.
    rising = _trending_history(step_bps=3.0)
    falling = [MARK * (1.0 + 3.0 / 10_000.0 * i) for i in range(0, -121, -1)]
    position = {"side": "LONG", "opened_at_ms": 1_700_000_000_000,
                "stop_price": MARK * 0.90, "take_profit_price": MARK * 1.10,
                "maximum_holding_seconds": 300}

    assert trend.position_exit_reason(position, snapshot(rising)) is None,         "a long must be held while its alignment still supports it"
    assert trend.position_exit_reason(position, snapshot(falling)) == "THESIS_INVALIDATED",         "a long must close when the EMA alignment flips against it"

    # The portfolio must surface it, and static levels must still take precedence.
    assert Portfolio.exit_reason(position, snapshot(falling), trend) == "THESIS_INVALIDATED"
    assert Portfolio.exit_reason(position, snapshot(falling)) is None,         "without a strategy the old static-only behaviour must be unchanged"

    stopped = dict(position, stop_price=MARK * 1.10)   # bid already through the stop
    assert Portfolio.exit_reason(stopped, snapshot(falling), trend) == "STOP",         "a real stop-out must be recorded as STOP, not relabelled by a thesis check"

    # A reassess() that raises must never strand a position.
    class Exploding:
        def position_exit_reason(self, position, snapshot):
            raise RuntimeError("boom")
    assert Portfolio.exit_reason(position, snapshot(rising), Exploding()) is None

    # The control must express NO thesis.
    control = registry.get(CONTROL_STRATEGY_ID)
    assert "position_exit_reason" not in vars(type(control)),         "random_control must not implement position_exit_reason - a control with a thesis is not a control"
    assert control.position_exit_reason(position, snapshot(falling)) is None


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"  OK  {test.__name__}")
    print(f"\nPAPER STRATEGY ECONOMICS: PASS ({len(tests)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
