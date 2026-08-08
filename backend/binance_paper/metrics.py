"""Evidence-first paper metrics with day-block uncertainty."""
from __future__ import annotations

import random
import statistics
from typing import Any

from .independence import evidence_profile


MIN_EVIDENCE_TRADES = 500
MIN_EVIDENCE_DAYS = 5
MIN_PROMOTION_OBSERVATION_DAYS = 56
MIN_PROMOTION_TRADING_DAYS = 30


def _day_block_lower_bound(trades: list[dict[str, Any]]) -> tuple[float | None, int]:
    by_day: dict[int, list[float]] = {}
    for trade in trades:
        day = int(trade["exit_time_ms"]) // 86_400_000
        by_day.setdefault(day, []).append(float(trade["net_pnl_usd"]))
    n_days = len(by_day)
    if n_days < 5:
        return None, n_days
    days = sorted(by_day)
    rng = random.Random(20260725)
    draws = []
    for _ in range(2_000):
        sample = [days[rng.randrange(n_days)] for _ in range(n_days)]
        pnl = [value for day in sample for value in by_day[day]]
        if pnl:
            draws.append(statistics.fmean(pnl))
    draws.sort()
    return (draws[int(0.025 * len(draws))] if draws else None), n_days


def _side_metrics(trades: list[dict], side: str) -> dict[str, Any]:
    values = [float(row["net_pnl_usd"]) for row in trades if row["side"] == side]
    return {
        "sample_size": len(values),
        "net_pnl_usd": round(sum(values), 6),
        "mean_expectancy_usd": (
            round(statistics.fmean(values), 6) if values else None
        ),
    }


def _promotion_gate(
    trades: list[dict[str, Any]],
    *,
    observation_days: float,
    n_days: int,
    profit_factor: float | None,
    lower_bound: float | None,
) -> dict[str, Any]:
    values = [float(row["net_pnl_usd"]) for row in trades]
    by_week: dict[int, float] = {}
    by_day: dict[int, float] = {}
    for row in trades:
        exit_time_ms = int(row["exit_time_ms"])
        value = float(row["net_pnl_usd"])
        day_index = exit_time_ms // 86_400_000
        monday_day_index = day_index - ((day_index + 3) % 7)
        by_week[monday_day_index] = by_week.get(monday_day_index, 0.0) + value
        by_day[day_index] = by_day.get(day_index, 0.0) + value
    positive_days = [max(0.0, value) for value in by_day.values()]
    total_positive_days = sum(positive_days)
    largest_day_profit_concentration = (
        max(positive_days) / total_positive_days if total_positive_days > 0 else None
    )
    positive_week_fraction = (
        sum(value > 0 for value in by_week.values()) / len(by_week)
        if by_week
        else None
    )
    fee_stress_net = sum(
        float(row["net_pnl_usd"])
        - 0.5 * (float(row["entry_fee_usd"]) + float(row["exit_fee_usd"]))
        for row in trades
    )
    slippage_stress_net = sum(
        float(row["net_pnl_usd"]) - 0.5 * float(row["slippage_usd"])
        for row in trades
    )
    # The old gate was named `independent_trades_500` and implemented as `len(trades) >= 500`.
    # That proves COUNT, not independence: 5m/15m positions overlap, so consecutive trades
    # routinely share a price path, and 500 correlated observations make a day-block bound far
    # narrower than the evidence supports. The count is kept under an honest name; independence
    # is now measured.
    evidence = evidence_profile(trades)
    checks: dict[str, bool | None] = {
        "trade_count_500": len(trades) >= 500,
        "non_overlapping_episodes_250": evidence["non_overlapping_episodes"] >= 250,
        "effective_sample_size_200": evidence["effective_sample_size"] >= 200.0,
        "no_single_day_cluster_gt_25pct": (
            evidence["largest_cluster_share"] is not None
            and evidence["largest_cluster_share"] < 0.25
        ),
        "forward_observation_56d": observation_days >= MIN_PROMOTION_OBSERVATION_DAYS,
        "observed_trading_days_30": n_days >= MIN_PROMOTION_TRADING_DAYS,
        "positive_after_cost_expectancy": bool(values)
        and statistics.fmean(values) > 0,
        "positive_day_block_lb": lower_bound is not None and lower_bound > 0,
        "profit_factor_gt_1_20": profit_factor is not None and profit_factor > 1.20,
        "positive_fee_50pct_stress": bool(values) and fee_stress_net > 0,
        "positive_slippage_50pct_stress": bool(values) and slippage_stress_net > 0,
        "positive_weeks_majority": positive_week_fraction is not None
        and positive_week_fraction > 0.5,
        "single_day_profit_concentration_lt_20pct": (
            largest_day_profit_concentration is not None
            and largest_day_profit_concentration < 0.20
        ),
        "positive_under_1s_latency": None,
        "single_regime_profit_concentration_lt_50pct": None,
        "deflated_sharpe_supports_skill": None,
        "backtest_overfit_probability_acceptable": None,
    }
    if any(value is False for value in checks.values()):
        status = "BLOCKED_FAILED_GATE"
    elif any(value is None for value in checks.values()):
        status = "BLOCKED_UNMEASURED"
    else:
        status = "FORWARD_GATE_PASSED_PAPER_ONLY"
    return {
        "status": status,
        "checks": checks,
        "positive_week_fraction": (
            round(positive_week_fraction, 6)
            if positive_week_fraction is not None
            else None
        ),
        "largest_day_profit_concentration": (
            round(largest_day_profit_concentration, 6)
            if largest_day_profit_concentration is not None
            else None
        ),
        # Reported so a reader can see WHY an independence gate failed, rather than only that
        # it did: 500 trades and 12 episodes is a very different sample from 500 and 480.
        "evidence_profile": evidence,
        "fee_50pct_stress_net_usd": round(fee_stress_net, 6),
        "slippage_50pct_stress_net_usd": round(slippage_stress_net, 6),
        "real_orders_remain_impossible": True,
    }


def strategy_metrics(persistence, strategy_id: str) -> dict[str, Any]:
    trades = persistence.trades(limit=10_000, strategy_id=strategy_id)
    account = persistence.account(strategy_id)
    values = [float(row["net_pnl_usd"]) for row in trades]
    gross_wins = sum(max(0.0, value) for value in values)
    gross_losses = -sum(min(0.0, value) for value in values)
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else None
    lower_bound, n_days = _day_block_lower_bound(trades)
    fees = sum(
        float(row["entry_fee_usd"]) + float(row["exit_fee_usd"])
        for row in trades
    )
    slippage = sum(float(row["slippage_usd"]) for row in trades)
    exposure_seconds = sum(float(row["holding_seconds"]) for row in trades)
    sample_size = len(trades)
    first_observation_ms, last_observation_ms = persistence.observation_window_ms(
        strategy_id
    )
    observation_days = (
        (last_observation_ms - first_observation_ms) / 86_400_000.0
        if first_observation_ms is not None
        and last_observation_ms is not None
        and last_observation_ms > first_observation_ms
        else 0.0
    )
    if sample_size and observation_days > 0:
        entries_per_day = sample_size / observation_days
        days_to_gate = 500.0 / entries_per_day if entries_per_day > 0 else None
    else:
        entries_per_day = 0.0
        days_to_gate = None
    if observation_days < 1.0:
        measurability = "INSUFFICIENT_OBSERVATION"
    elif days_to_gate is None:
        measurability = "NEVER_FIRES"
    elif days_to_gate <= 56:
        measurability = "OK"
    elif days_to_gate <= 365:
        measurability = "SLOW"
    else:
        measurability = "UNMEASURABLE"
    status = (
        "EVIDENCE_READY"
        if sample_size >= MIN_EVIDENCE_TRADES and n_days >= MIN_EVIDENCE_DAYS
        else "INSUFFICIENT_DATA"
    )
    return {
        "strategy_id": strategy_id,
        "status": status,
        "sample_size": sample_size,
        "n_days": n_days,
        "net_pnl_usd": round(sum(values), 6),
        "profit_factor": (
            round(profit_factor, 6) if profit_factor is not None else None
        ),
        "mean_expectancy_usd": (
            round(statistics.fmean(values), 6) if values else None
        ),
        "median_expectancy_usd": (
            round(statistics.median(values), 6) if values else None
        ),
        "ev_lb_block_usd": (
            round(lower_bound, 6) if lower_bound is not None else None
        ),
        "ev_lb_block_c": (
            round(lower_bound * 100.0, 2) if lower_bound is not None else None
        ),
        "median_c": (
            round(statistics.median(values) * 100.0, 2) if values else None
        ),
        "lb_method": (
            "day-block bootstrap (2000 resamples)"
            if lower_bound is not None
            else "day-block bootstrap unavailable (fewer than 5 observed days)"
        ),
        "entries_per_day": round(entries_per_day, 4),
        "days_to_gate": round(days_to_gate) if days_to_gate is not None else None,
        "observation_days": round(observation_days, 4),
        "measurability": measurability,
        "maximum_drawdown_usd": round(float(account["maximum_drawdown_usd"]), 6),
        "fees_usd": round(fees, 6),
        "slippage_usd": round(slippage, 6),
        "exposure_seconds": round(exposure_seconds, 3),
        "long": _side_metrics(trades, "LONG"),
        "short": _side_metrics(trades, "SHORT"),
        "win_rate": (
            round(sum(value > 0 for value in values) / sample_size, 6)
            if sample_size
            else None
        ),
        "promotion_gate": _promotion_gate(
            trades,
            observation_days=observation_days,
            n_days=n_days,
            profit_factor=profit_factor,
            lower_bound=lower_bound,
        ),
    }


def _apply_control_relative_gate(strategies: list[dict[str, Any]]) -> None:
    """Add the criterion the registry already demands in prose (scan-5 item 5.30).

    `strategy_registry` states it in its own module docstring:

        "A strategy that does not beat random_control over the [same period] has established
         nothing."

    `_promotion_gate` contained ZERO references to `random_control` or CONTROL_STRATEGY_ID. It
    evaluated a strategy against its OWN positive expectancy, profit factor, bootstrap lower
    bound, stress tests and concentration - every one of which a zero-information control can
    also satisfy in a trending sample. The requirement was written down and never enforced,
    which is this repository's most repeated defect shape.

    Applied HERE rather than inside `strategy_metrics` because only the cross-strategy view
    knows what the control did. Mutates each gate in place.

    The control itself is exempt: it is the yardstick, and "the control must beat the control"
    is not a claim about anything.
    """
    from .strategy_registry import CONTROL_STRATEGY_ID

    control = next((s for s in strategies
                    if s.get("strategy_id") == CONTROL_STRATEGY_ID), None)
    control_expectancy = None if control is None else control.get("mean_expectancy_usd")

    for item in strategies:
        gate = item.get("promotion_gate")
        if not isinstance(gate, dict):
            continue
        criteria = gate.get("criteria") if isinstance(gate.get("criteria"), dict) else gate
        if item.get("strategy_id") == CONTROL_STRATEGY_ID:
            criteria["beats_random_control"] = True
            criteria["control_relative_basis"] = "IS_THE_CONTROL"
            continue
        own = item.get("mean_expectancy_usd")
        if control_expectancy is None or own is None:
            # UNKNOWN is not a pass. A missing control means the comparison the registry
            # requires was never made, and that is a reason to refuse rather than to proceed.
            criteria["beats_random_control"] = False
            criteria["control_relative_basis"] = "CONTROL_UNAVAILABLE"
        else:
            criteria["beats_random_control"] = bool(float(own) > float(control_expectancy))
            criteria["control_relative_basis"] = (
                f"own={float(own):.6f} vs control={float(control_expectancy):.6f}")
        # The overall verdict must reflect the new criterion, not just list it.
        for key in ("passes", "eligible", "promotable", "ready"):
            if key in gate and gate[key] is True and not criteria["beats_random_control"]:
                gate[key] = False


def all_metrics(persistence) -> dict[str, Any]:
    strategies = [
        strategy_metrics(persistence, row["strategy_id"])
        for row in persistence.accounts()
    ]
    _apply_control_relative_gate(strategies)
    return {
        "status": (
            "EVIDENCE_READY"
            if strategies and all(item["status"] == "EVIDENCE_READY" for item in strategies)
            else "INSUFFICIENT_DATA"
        ),
        "sample_size": sum(item["sample_size"] for item in strategies),
        "n_days": max((item["n_days"] for item in strategies), default=0),
        "strategies": strategies,
    }
