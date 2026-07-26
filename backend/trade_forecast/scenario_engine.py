"""Evaluate only the frozen causal exit plans over forecast quantile scenarios."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .trade_labels import entry_cost_per_share, evaluate_exit_plan, net_pnl_per_share
from .trade_schema import EXIT_PLANS, FUTURE_OFFSETS_S


SCENARIO_QUANTILES = ("q10", "q25", "q50", "q75", "q90")
# Quantile paths do not identify a true joint path distribution. These weights are
# an explicit approximation used only for shadow ranking, with a separate uncertainty
# penalty in the optimizer.
SCENARIO_WEIGHTS = (0.125, 0.25, 0.25, 0.25, 0.125)


def weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    order = np.argsort(values)
    sorted_values = np.asarray(values, dtype=float)[order]
    sorted_weights = np.asarray(weights, dtype=float)[order]
    cumulative = np.cumsum(sorted_weights)
    cumulative /= cumulative[-1]
    return float(sorted_values[np.searchsorted(cumulative, quantile, side="left")])


def weighted_cvar(
    values: list[float], weights: list[float], tail_probability: float = 0.05
) -> float:
    order = np.argsort(values)
    remaining = float(tail_probability)
    total = 0.0
    for index in order:
        take = min(remaining, float(weights[index]))
        if take > 0:
            total += float(values[index]) * take
            remaining -= take
        if remaining <= 1e-12:
            break
    return total / float(tail_probability)


def _quantile_path(
    share_path: dict[str, dict[str, float]], label: str
) -> tuple[list[float], list[float]]:
    times: list[float] = []
    values: list[float] = []
    for offset in FUTURE_OFFSETS_S:
        point = share_path.get(str(offset)) or {}
        if point.get(label) is None:
            continue
        times.append(float(offset))
        values.append(float(point[label]))
    return times, values


def evaluate_plans(
    *,
    entry_vwap: float,
    share_path: dict[str, dict[str, float]],
    p_settlement_win: float,
) -> dict[str, dict[str, Any]]:
    """Return a distribution for each frozen plan; no historical best-exit search."""
    p_win = min(1.0, max(0.0, float(p_settlement_win)))
    results: dict[str, dict[str, Any]] = {}
    for plan in EXIT_PLANS:
        outcomes: list[float] = []
        weights: list[float] = []
        holding_times: list[float] = []
        for quantile_label, path_weight in zip(SCENARIO_QUANTILES, SCENARIO_WEIGHTS):
            times, bids = _quantile_path(share_path, quantile_label)
            net_path = [net_pnl_per_share(entry_vwap, bid) for bid in bids]
            for settle_value, settle_weight in ((1.0, p_win), (0.0, 1.0 - p_win)):
                if settle_weight <= 0.0:
                    continue
                settle_net = settle_value - entry_cost_per_share(entry_vwap)
                result = evaluate_exit_plan(plan, times, net_path, settle_net)
                outcomes.append(float(result["net"]))
                weights.append(float(path_weight) * settle_weight)
                if result["holding_s"] is not None:
                    holding_times.append(float(result["holding_s"]))
        if not outcomes:
            continue
        total_weight = sum(weights)
        weights = [weight / total_weight for weight in weights]
        expected = float(np.average(outcomes, weights=weights))
        weighted_profit = float(
            sum(max(0.0, value) * weight for value, weight in zip(outcomes, weights))
        )
        weighted_loss = float(
            -sum(min(0.0, value) * weight for value, weight in zip(outcomes, weights))
        )
        results[plan] = {
            "expected_pnl": expected,
            "p_profit": float(
                sum(weight for value, weight in zip(outcomes, weights) if value > 0.0)
            ),
            "pnl_q10": weighted_quantile(outcomes, weights, 0.10),
            "pnl_q25": weighted_quantile(outcomes, weights, 0.25),
            "pnl_q50": weighted_quantile(outcomes, weights, 0.50),
            "pnl_q75": weighted_quantile(outcomes, weights, 0.75),
            "pnl_q90": weighted_quantile(outcomes, weights, 0.90),
            "cvar_05": weighted_cvar(outcomes, weights, 0.05),
            "profit_factor": (
                weighted_profit / weighted_loss if weighted_loss > 1e-12 else None
            ),
            "expected_holding_s": (
                float(np.mean(holding_times)) if holding_times else None
            ),
            "scenario_count": len(outcomes),
            "method": "QUANTILE_PATH_APPROXIMATION_SHADOW_ONLY",
        }
    return results


def selftest() -> None:
    share_path = {
        str(offset): {
            "q10": 0.50,
            "q25": 0.55,
            "q50": 0.60,
            "q75": 0.65,
            "q90": 0.70,
        }
        for offset in FUTURE_OFFSETS_S
    }
    plans = evaluate_plans(
        entry_vwap=0.55, share_path=share_path, p_settlement_win=0.7
    )
    assert set(plans) == set(EXIT_PLANS)
    assert plans["HOLD_TO_SETTLEMENT"]["pnl_q10"] < 0
    assert math.isfinite(plans["TAKE_3C"]["expected_pnl"])
    print("scenario_engine self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
