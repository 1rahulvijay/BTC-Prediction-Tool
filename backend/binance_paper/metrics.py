"""Evidence-first paper metrics with day-block uncertainty."""
from __future__ import annotations

import random
import statistics
from typing import Any


MIN_EVIDENCE_TRADES = 500
MIN_EVIDENCE_DAYS = 5


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


def strategy_metrics(persistence, strategy_id: str) -> dict[str, Any]:
    trades = persistence.trades(limit=10_000, strategy_id=strategy_id)
    account = persistence.account(strategy_id)
    values = [float(row["net_pnl_usd"]) for row in trades]
    gross_wins = sum(max(0.0, value) for value in values)
    gross_losses = -sum(min(0.0, value) for value in values)
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
            round(gross_wins / gross_losses, 6) if gross_losses > 0 else None
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
    }


def all_metrics(persistence) -> dict[str, Any]:
    strategies = [
        strategy_metrics(persistence, row["strategy_id"])
        for row in persistence.accounts()
    ]
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
