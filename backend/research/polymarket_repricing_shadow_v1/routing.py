"""Pure four-policy routing simulator. It has no order-submission capability."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

POLICY_NAMES = (
    "A_BASELINE_TAKER",
    "B_REPRICING_URGENCY",
    "C_MAKER_FIRST_TTL2",
    "D_SIZE_AWARE_TTL5",
)


def taker_fee_per_share(price: float, fee_rate: float = 0.07) -> float:
    value = max(0.0, min(1.0, float(price)))
    return round(max(0.0, fee_rate) * value * (1.0 - value), 5)


def parse_ladder(value: str | dict[str, Any] | None) -> dict[str, list[list[float]]]:
    parsed = json.loads(value) if isinstance(value, str) else value
    parsed = parsed if isinstance(parsed, dict) else {}
    output: dict[str, list[list[float]]] = {"b": [], "a": []}
    for key, reverse in (("b", True), ("a", False)):
        rows = []
        for raw in parsed.get(key) or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            price, size = float(raw[0]), float(raw[1])
            if 0.0 <= price <= 1.0 and size > 0.0:
                rows.append([price, size])
        output[key] = sorted(rows, key=lambda item: item[0], reverse=reverse)
    return output


def walk_asks(
    ladder: dict[str, list[list[float]]],
    quantity: float,
    maximum_price: float = 1.0,
) -> dict[str, float | bool]:
    remaining = max(0.0, float(quantity))
    cost = 0.0
    filled = 0.0
    for price, available in ladder.get("a") or []:
        if price > maximum_price + 1e-12 or remaining <= 1e-12:
            break
        take = min(remaining, available)
        cost += take * price
        filled += take
        remaining -= take
    return {
        "filled_quantity": filled,
        "complete": remaining <= 1e-9,
        "vwap": cost / filled if filled > 0 else math.nan,
        "notional": cost,
    }


def inside_limit(best_bid: float, best_ask: float, tick: float) -> float:
    if best_ask - best_bid > tick + 1e-12:
        return round(min(best_ask - tick, best_bid + tick), 4)
    return round(best_bid, 4)


@dataclass
class Candidate:
    candidate_id: str
    timestamp: float
    market_id: str
    condition_id: str
    selected_side: str
    quantity: float
    bid: float
    ask: float
    spread: float
    top_ask_depth: float
    ladder: dict[str, list[list[float]]]
    baseline_probability: float
    baseline_edge: float
    worsening_probability: float
    quote_age_seconds: float
    seconds_left: float
    event_probabilities: dict[str, float]
    feature_values: dict[str, float]


@dataclass
class RouteState:
    candidate_id: str
    policy: str
    decision: str
    proposed_limit: float | None
    ttl_seconds: int
    requested_quantity: float
    status: str = "PENDING"
    filled_quantity: float = 0.0
    notional: float = 0.0
    fee: float = 0.0
    fill_time_seconds: float | None = None
    fallback_used: bool = False
    observations: dict[int, float] = field(default_factory=dict)

    @property
    def average_price(self) -> float | None:
        return (
            self.notional / self.filled_quantity if self.filled_quantity > 0 else None
        )


def create_routes(candidate: Candidate, protocol: dict[str, Any]) -> list[RouteState]:
    routing = protocol["routing"]
    threshold = float(routing["worsening_probability_threshold"])
    high_risk = candidate.worsening_probability >= threshold
    limit = inside_limit(candidate.bid, candidate.ask, float(routing["inside_tick"]))
    abnormal = (
        candidate.quote_age_seconds
        > float(protocol["candidate"]["maximum_quote_age_seconds"])
        or candidate.spread > float(routing["maximum_spread"])
        or candidate.top_ask_depth < float(routing["minimum_top_ask_depth"])
    )
    thin = candidate.top_ask_depth < max(
        candidate.quantity, float(routing["sufficient_depth"])
    )
    routes = [
        RouteState(
            candidate.candidate_id,
            "A_BASELINE_TAKER",
            "TAKE_NOW",
            None,
            0,
            candidate.quantity,
        ),
        RouteState(
            candidate.candidate_id,
            "B_REPRICING_URGENCY",
            "TAKE_NOW_HIGH_RISK" if high_risk else "BASELINE_TAKE_LOW_RISK",
            None,
            0,
            candidate.quantity,
        ),
        RouteState(
            candidate.candidate_id,
            "C_MAKER_FIRST_TTL2",
            "TAKE_NOW_HIGH_RISK" if high_risk else "MAKER_FIRST",
            None if high_risk else limit,
            0 if high_risk else int(routing["maker_ttl_seconds"]),
            candidate.quantity,
        ),
        RouteState(
            candidate.candidate_id,
            "D_SIZE_AWARE_TTL5",
            (
                "SKIP_BAD_BOOK"
                if abnormal
                else "TAKE_NOW_HIGH_RISK_OR_THIN"
                if high_risk or thin
                else "MAKER_FIRST_SIZE_AWARE"
            ),
            None if abnormal or high_risk or thin else limit,
            0
            if abnormal or high_risk or thin
            else int(routing["size_aware_ttl_seconds"]),
            candidate.quantity,
        ),
    ]
    if [route.policy for route in routes] != list(POLICY_NAMES):
        raise AssertionError("routing policy order changed")
    return routes


def fill_taker(
    route: RouteState, ladder: dict[str, list[list[float]]], elapsed: float
) -> None:
    remaining = route.requested_quantity - route.filled_quantity
    execution = walk_asks(ladder, remaining)
    filled = float(execution["filled_quantity"])
    if filled > 0:
        route.notional += float(execution["notional"])
        route.filled_quantity += filled
        route.fee += filled * taker_fee_per_share(float(execution["vwap"]))
        route.fill_time_seconds = elapsed
    route.status = (
        "FILLED"
        if route.filled_quantity >= route.requested_quantity - 1e-9
        else "PARTIAL"
        if route.filled_quantity > 0
        else "MISSED"
    )


def update_route(
    route: RouteState,
    ladder: dict[str, list[list[float]]],
    elapsed: float,
    *,
    fallback_cross: bool,
) -> None:
    if route.status in {"FILLED", "MISSED", "SKIPPED"}:
        return
    if route.decision == "SKIP_BAD_BOOK":
        route.status = "SKIPPED"
        return
    if route.proposed_limit is None:
        fill_taker(route, ladder, elapsed)
        return
    execution = walk_asks(
        ladder,
        route.requested_quantity - route.filled_quantity,
        maximum_price=route.proposed_limit,
    )
    filled = float(execution["filled_quantity"])
    if filled > 0:
        route.notional += float(execution["notional"])
        route.filled_quantity += filled
        route.fill_time_seconds = elapsed
        # A passive fill is modeled with zero fee. This is only a conservative
        # touch proxy until the full L2 trade/queue reconstruction grades it.
    if route.filled_quantity >= route.requested_quantity - 1e-9:
        route.status = "FILLED"
        return
    if elapsed >= route.ttl_seconds:
        route.fallback_used = bool(fallback_cross)
        if fallback_cross:
            fill_taker(route, ladder, elapsed)
        else:
            route.status = "PARTIAL" if route.filled_quantity > 0 else "MISSED"
