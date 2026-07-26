"""Exact fee, exit-price, path-label, and frozen-plan mathematics."""
from __future__ import annotations

import math
from typing import Iterable

try:
    from polymarket_fee import (
        DEFAULT_CRYPTO_TAKER_FEE_RATE as FEE_RATE,
        polymarket_taker_fee_per_share as taker_fee,
    )
except ImportError:
    from backend.polymarket_fee import (
        DEFAULT_CRYPTO_TAKER_FEE_RATE as FEE_RATE,
        polymarket_taker_fee_per_share as taker_fee,
    )

from .trade_schema import EXIT_PLANS


def clamp_probability(value: float, epsilon: float = 1e-6) -> float:
    return min(1.0 - epsilon, max(epsilon, float(value)))


def logit(value: float) -> float:
    p = clamp_probability(value)
    return math.log(p / (1.0 - p))


def inv_logit(value: float) -> float:
    x = max(-40.0, min(40.0, float(value)))
    return 1.0 / (1.0 + math.exp(-x))


def entry_cost_per_share(entry_vwap: float, fee_rate: float = FEE_RATE) -> float:
    return float(entry_vwap) + taker_fee(float(entry_vwap), fee_rate)


def exit_proceeds_per_share(exit_vwap: float, fee_rate: float = FEE_RATE) -> float:
    return float(exit_vwap) - taker_fee(float(exit_vwap), fee_rate)


def net_pnl_per_share(
    entry_vwap: float, exit_vwap: float, fee_rate: float = FEE_RATE
) -> float:
    return exit_proceeds_per_share(exit_vwap, fee_rate) - entry_cost_per_share(
        entry_vwap, fee_rate
    )


def required_exit_bid(
    entry_vwap: float,
    target_profit_per_share: float = 0.0,
    fee_rate: float = FEE_RATE,
) -> float | None:
    """Solve the minimum gross exit bid needed after entry and both taker fees."""
    target = entry_cost_per_share(entry_vwap, fee_rate) + float(
        target_profit_per_share
    )
    if target <= 0.0:
        return 0.0
    if target > 1.0:
        return None
    lo, hi = 0.0, 1.0
    if exit_proceeds_per_share(hi, fee_rate) + 1e-12 < target:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if exit_proceeds_per_share(mid, fee_rate) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def first_crossing(
    times_s: Iterable[float],
    net_path: Iterable[float],
    upper: float | None = None,
    lower: float | None = None,
) -> tuple[str, float | None, float | None]:
    """First causal crossing. Upper wins an exact same-observation tie."""
    for time_s, net in zip(times_s, net_path):
        value = float(net)
        if upper is not None and value >= float(upper):
            return ("TARGET", float(time_s), value)
        if lower is not None and value <= float(lower):
            return ("STOP", float(time_s), value)
    return ("NONE", None, None)


def evaluate_exit_plan(
    plan: str,
    times_s: list[float],
    net_path: list[float],
    settle_net: float,
) -> dict:
    """Evaluate one predeclared causal plan; never select the historical best exit."""
    if plan not in EXIT_PLANS:
        raise ValueError(f"unknown frozen exit plan: {plan}")
    if plan == "HOLD_TO_SETTLEMENT":
        return {"exit_kind": "SETTLE", "holding_s": None, "net": float(settle_net)}

    if plan.startswith("TAKE_") and "_OR_STOP_" not in plan:
        cents = int(plan.split("_")[1][:-1])
        kind, at, value = first_crossing(times_s, net_path, upper=cents / 100.0)
        return (
            {"exit_kind": "TARGET", "holding_s": at, "net": value}
            if kind == "TARGET"
            else {"exit_kind": "SETTLE", "holding_s": None, "net": float(settle_net)}
        )

    if plan == "TAKE_3C_OR_STOP_3C":
        kind, at, value = first_crossing(times_s, net_path, 0.03, -0.03)
        return (
            {"exit_kind": kind, "holding_s": at, "net": value}
            if kind != "NONE"
            else {"exit_kind": "SETTLE", "holding_s": None, "net": float(settle_net)}
        )

    if plan.startswith("TIME_EXIT_"):
        deadline = int(plan.split("_")[2][:-1])
        for at, value in zip(times_s, net_path):
            if float(at) >= deadline:
                return {"exit_kind": "TIME", "holding_s": float(at), "net": float(value)}
        return {"exit_kind": "SETTLE", "holding_s": None, "net": float(settle_net)}

    # Once +3c is first reached, arm a break-even floor. A later observation that
    # falls back to/below zero triggers an exit at that observed executable value.
    # The arming observation itself cannot also be the exit; otherwise this plan
    # would be identical to TAKE_3C.
    armed_at: int | None = None
    for index, (at, value) in enumerate(zip(times_s, net_path)):
        value = float(value)
        if armed_at is None and value >= 0.03:
            armed_at = index
            continue
        if armed_at is not None and index > armed_at and value <= 0.0:
            return {
                "exit_kind": "BREAK_EVEN_FLOOR",
                "holding_s": float(at),
                "net": value,
            }
    return {"exit_kind": "SETTLE", "holding_s": None, "net": float(settle_net)}


def summarize_realized_path(
    times_s: list[float], net_path: list[float], settle_net: float
) -> dict:
    executable = [float(value) for value in net_path]
    all_values = executable + [float(settle_net)]
    profitable = [(t, n) for t, n in zip(times_s, executable) if n > 0.0]
    first_profitable_index = next(
        (index for index, value in enumerate(executable) if value > 0.0),
        None,
    )
    labels = {
        "actual_first_profitable_s": (
            float(profitable[0][0]) if profitable else None
        ),
        "actual_mfe": max(all_values) if all_values else float(settle_net),
        "actual_mae": min(all_values) if all_values else float(settle_net),
        "label_ever_profitable": int(bool(profitable) or float(settle_net) > 0.0),
        "label_stays_profitable_to_settlement": int(
            first_profitable_index is not None
            and all(value > 0.0 for value in executable[first_profitable_index:])
            and float(settle_net) > 0.0
        ),
        "label_lockable_1c": int(any(value >= 0.01 for value in executable)),
    }
    for target, stop in ((1, 3), (3, 3), (5, 5)):
        kind, _, _ = first_crossing(
            times_s, executable, target / 100.0, -stop / 100.0
        )
        labels[f"label_take_{target}c_before_stop_{stop}c"] = int(kind == "TARGET")
    for plan in EXIT_PLANS:
        result = evaluate_exit_plan(plan, times_s, executable, settle_net)
        key = "plan_" + plan.lower()
        labels[f"{key}_net"] = result["net"]
        labels[f"{key}_holding_s"] = result["holding_s"]
        labels[f"{key}_exit_kind"] = result["exit_kind"]
    return labels


def profit_state(
    *,
    entry_vwap: float,
    current_exit_vwap: float | None,
    full_quantity_fillable: bool,
    predicted_q10_next: float | None = None,
) -> str:
    if current_exit_vwap is None:
        return "BELOW_BREAK_EVEN"
    net = net_pnl_per_share(entry_vwap, current_exit_vwap)
    if net < 0:
        return "LOSS"
    if not full_quantity_fillable:
        return "PROFIT_AVAILABLE"
    if predicted_q10_next is not None and float(predicted_q10_next) < 0.0:
        return "EXIT_NOW"
    return "PROFIT_LOCKABLE"


def selftest() -> None:
    # The canonical repository helper rounds each per-share fee to five decimals.
    assert abs(entry_cost_per_share(0.62) - 0.63649) < 1e-9
    be = required_exit_bid(0.62)
    tp = required_exit_bid(0.62, 0.03)
    assert be is not None and abs(be - 0.6523623501) < 1e-8
    assert tp is not None and abs(tp - 0.68168) < 1e-8
    kind, at, _ = first_crossing([1, 2], [-0.04, 0.04], 0.03, -0.03)
    assert kind == "STOP" and at == 1
    result = evaluate_exit_plan("TAKE_3C_OR_STOP_3C", [1, 2], [-0.01, 0.04], -0.5)
    assert result["exit_kind"] == "TARGET" and result["holding_s"] == 2
    lock = evaluate_exit_plan(
        "BREAK_EVEN_LOCK_AFTER_3C",
        [1, 2, 3],
        [0.03, 0.02, -0.01],
        -0.5,
    )
    assert lock["exit_kind"] == "BREAK_EVEN_FLOOR" and lock["holding_s"] == 3
    assert profit_state(
        entry_vwap=0.5,
        current_exit_vwap=0.6,
        full_quantity_fillable=True,
        predicted_q10_next=-0.01,
    ) == "EXIT_NOW"
    print("trade_labels self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
