"""A safety fault must stop new risk without trapping an existing position.

WHAT WAS WRONG
    RiskEngine.evaluate appended kill_switch, sequence_unhealthy, feed_age_unknown and
    stale_feed UNCONDITIONALLY. Only model_unavailable and the exposure limits consulted
    intent.reduce_only. So during precisely the fault that fires the kill switch, a reduce-only
    flatten was blocked along with new entries.

    Being unable to OPEN during a fault is the intended behaviour. Being unable to CLOSE is the
    more dangerous failure, and it was the behaviour in effect.

THE DISTINCTION
    Waivable for reduce-only, recorded as advisories so a flatten during a kill switch is
    auditable rather than invisible:
        kill_switch, sequence_unhealthy, stale_feed, feed_age_unknown, model_unavailable

    Never waivable, because they make "reduce" unverifiable rather than merely degraded:
        unknown_position, invalid_notional, leverage_limit

    python backend/tests/test_close_only_authority.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_OK = True

WAIVABLE = {
    "kill_switch": {"kill_switch": True},
    "sequence_unhealthy": {"sequence_healthy": False},
    "stale_feed": {"feed_age_ms": 1e9},
    "feed_age_unknown": {"feed_age_ms": float("nan")},
    "model_unavailable": {"model_available": False},
}


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _parts():
    from backend.quant_platform.risk_engine import (
        OrderIntent, RiskAction, RiskEngine, RiskState,
    )

    return OrderIntent, RiskAction, RiskEngine, RiskState


def _intent(OrderIntent, *, reduce_only: bool, notional: float = 100.0,
            leverage: float = 1.0, side: str = "SELL", quantity: float = 1.0,
            venue_reduce_only_supported: bool = True):
    return OrderIntent(venue="binance", instrument="BTCUSDT", strategy_id="test",
                       notional=notional, leverage=leverage, reduce_only=reduce_only,
                       side=side if reduce_only else None,
                       quantity=quantity if reduce_only else None,
                       price=100.0 if reduce_only else None,
                       instrument_type="DERIVATIVE",
                       venue_reduce_only_supported=(
                           venue_reduce_only_supported if reduce_only else False
                       ))


def _healthy_state(RiskState, **overrides):
    base = {
        "kill_switch": False, "position_known": True, "model_available": True,
        "sequence_healthy": True, "feed_age_ms": 10.0, "daily_pnl": 0.0,
        "weekly_pnl": 0.0, "open_notional": 0.0, "correlated_exposure": 0.0,
        "current_signed_quantity": 1.0,
    }
    base.update(overrides)
    return RiskState(**base)


def test_each_fault_blocks_opening_but_permits_flattening() -> None:
    print("every waivable fault blocks OPENING and permits FLATTENING")
    OrderIntent, RiskAction, RiskEngine, RiskState = _parts()
    engine = RiskEngine()

    for name, override in WAIVABLE.items():
        state = _healthy_state(RiskState, **override)

        opening = engine.evaluate(
            _intent(OrderIntent, reduce_only=False), state)
        chk(opening.action == RiskAction.BLOCK and name in opening.reasons,
            f"{name}: a NEW position is blocked ({opening.reasons})")

        flatten = engine.evaluate(
            _intent(OrderIntent, reduce_only=True), state)
        chk(flatten.allowed,
            f"{name}: a reduce-only flatten is PERMITTED ({flatten.action})")
        chk(name in flatten.advisories,
            f"{name}: and the waived fault is recorded as an advisory ({flatten.advisories})")
        chk(flatten.action == RiskAction.ALLOW_REDUCE_ONLY,
            f"{name}: the action names the degraded admission ({flatten.action})")


def test_all_faults_at_once_still_permits_flattening() -> None:
    """The realistic emergency: everything is wrong at the same moment."""
    print("with EVERY fault active at once, flattening is still possible")
    OrderIntent, RiskAction, RiskEngine, RiskState = _parts()
    engine = RiskEngine()
    state = _healthy_state(
        RiskState, kill_switch=True, sequence_healthy=False,
        feed_age_ms=1e9, model_available=False,
        daily_pnl=-1e9, weekly_pnl=-1e9, open_notional=1e9, correlated_exposure=1e9,
    )
    opening = engine.evaluate(
        _intent(OrderIntent, reduce_only=False), state)
    chk(opening.action == RiskAction.BLOCK, "opening is blocked, as it must be")

    flatten = engine.evaluate(
        _intent(OrderIntent, reduce_only=True), state)
    chk(flatten.allowed,
        f"the position can still be closed ({flatten.action}, advisories={len(flatten.advisories)})")
    chk(len(flatten.advisories) >= 4,
        f"and every waived fault is listed, not summarised away ({flatten.advisories})")


def test_unverifiable_reduction_is_still_blocked() -> None:
    """A 'reduce' we cannot verify could open or flip. Those stay hard blocks."""
    print("a reduction that cannot be verified is still refused")
    OrderIntent, RiskAction, RiskEngine, RiskState = _parts()
    engine = RiskEngine()

    unknown = _healthy_state(RiskState, position_known=False)
    decision = engine.evaluate(
        _intent(OrderIntent, reduce_only=True), unknown)
    chk(decision.action == RiskAction.BLOCK and "unknown_position" in decision.reasons,
        f"unknown position blocks even a reduce-only order ({decision.reasons})")

    healthy = _healthy_state(RiskState)
    for label, intent in (
        ("invalid notional", _intent(OrderIntent, reduce_only=True, notional=0.0)),
        ("excess leverage", _intent(OrderIntent, reduce_only=True, leverage=999.0)),
    ):
        decision = engine.evaluate(intent, healthy)
        chk(decision.action == RiskAction.BLOCK,
            f"{label} blocks even a reduce-only order ({decision.reasons})")

    for label, intent, expected in (
        (
            "wrong side",
            _intent(OrderIntent, reduce_only=True, side="BUY"),
            "reduce_only_does_not_reduce",
        ),
        (
            "oversized flip",
            _intent(OrderIntent, reduce_only=True, quantity=2.0),
            "reduce_only_would_flip",
        ),
        (
            "venue flag missing",
            _intent(
                OrderIntent,
                reduce_only=True,
                venue_reduce_only_supported=False,
            ),
            "venue_reduce_only_unverified",
        ),
    ):
        decision = engine.evaluate(intent, healthy)
        chk(
            decision.action == RiskAction.BLOCK and expected in decision.reasons,
            f"{label} is rejected ({decision.reasons})",
        )


def test_healthy_state_is_a_plain_allow() -> None:
    print("with no faults, a normal order is a plain ALLOW")
    OrderIntent, RiskAction, RiskEngine, RiskState = _parts()
    engine = RiskEngine()
    state = _healthy_state(RiskState)
    decision = engine.evaluate(
        _intent(OrderIntent, reduce_only=False), state)
    chk(decision.action == RiskAction.ALLOW and not decision.advisories,
        f"healthy open -> ALLOW with no advisories ({decision.action})")
    flatten = engine.evaluate(
        _intent(OrderIntent, reduce_only=True), state)
    chk(flatten.action == RiskAction.ALLOW and not flatten.advisories,
        "a reduce-only order in a healthy state is a plain ALLOW, not a degraded one")


def test_exposure_limits_do_not_trap_a_position() -> None:
    print("breaching an exposure or loss limit does not trap the position")
    OrderIntent, RiskAction, RiskEngine, RiskState = _parts()
    engine = RiskEngine()
    state = _healthy_state(
        RiskState, open_notional=1e9, correlated_exposure=1e9,
        daily_pnl=-1e9, weekly_pnl=-1e9)
    opening = engine.evaluate(
        _intent(OrderIntent, reduce_only=False), state)
    chk(opening.action == RiskAction.BLOCK, "new exposure is blocked past the limits")
    flatten = engine.evaluate(
        _intent(OrderIntent, reduce_only=True), state)
    chk(flatten.allowed,
        "but the reduce-only order that LOWERS those very numbers is permitted")


def main() -> int:
    test_each_fault_blocks_opening_but_permits_flattening()
    test_all_faults_at_once_still_permits_flattening()
    test_unverifiable_reduction_is_still_blocked()
    test_healthy_state_is_a_plain_allow()
    test_exposure_limits_do_not_trap_a_position()
    print("\nCLOSE-ONLY AUTHORITY", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
