"""Exact same-size complete-set economics over two synchronized L2 books."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from backend.polymarket.l2_book import L2Book, decimal_value

ZERO = Decimal(0)
ONE = Decimal(1)


def fee_rate_from_base_bps(base_fee_bps: float) -> float:
    """Convert the public fee-rate endpoint's integer basis points to a rate."""
    value = float(base_fee_bps)
    if not 0.0 <= value <= 10_000.0:
        raise ValueError(f"invalid base fee bps: {base_fee_bps!r}")
    return value / 10_000.0


def fee_per_share(price: Decimal, fee_rate: float) -> Decimal:
    """Mirror the protocol fee curve with the repository's five-decimal convention."""
    p = min(ONE, max(ZERO, decimal_value(price)))
    value = round(float(fee_rate) * float(p) * (1.0 - float(p)), 5)
    return Decimal(str(value))


@dataclass(frozen=True)
class LegExecution:
    action: str
    quantity: float
    complete: bool
    best_price: float | None
    worst_price: float | None
    vwap: float | None
    gross_notional: float
    fee: float
    cash: float
    levels_consumed: int
    reject_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PairEvaluation:
    direction: str
    quantity: float
    complete: bool
    raw_net_usd: float | None
    conservative_net_usd: float | None
    raw_net_per_pair: float | None
    conservative_net_per_pair: float | None
    up: LegExecution
    down: LegExecution
    reject_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def execute_leg(
    book: L2Book,
    action: str,
    quantity: float,
    fee_rate: float,
) -> LegExecution:
    direction = str(action).upper()
    requested = decimal_value(quantity)
    if requested <= ZERO:
        raise ValueError("quantity must be positive")
    if direction not in {"BUY", "SELL"}:
        raise ValueError("action must be BUY or SELL")
    if not book.valid:
        return LegExecution(
            direction,
            float(requested),
            False,
            None,
            None,
            None,
            0.0,
            0.0,
            0.0,
            0,
            book.invalid_reason or "invalid_book",
        )

    ladder_side = "SELL" if direction == "BUY" else "BUY"
    levels = book.sorted_levels(ladder_side)
    remaining = requested
    filled = ZERO
    notional = ZERO
    fees = ZERO
    worst: Decimal | None = None
    consumed = 0
    for price, available in levels:
        take = min(remaining, available)
        if take <= ZERO:
            continue
        notional += price * take
        fees += fee_per_share(price, fee_rate) * take
        filled += take
        remaining -= take
        worst = price
        consumed += 1
        if remaining == ZERO:
            break

    best = levels[0][0] if levels else None
    vwap = notional / filled if filled else None
    cash = notional + fees if direction == "BUY" else notional - fees
    complete = remaining == ZERO
    return LegExecution(
        action=direction,
        quantity=float(requested),
        complete=complete,
        best_price=float(best) if best is not None else None,
        worst_price=float(worst) if worst is not None else None,
        vwap=float(vwap) if vwap is not None else None,
        gross_notional=float(notional),
        fee=float(fees),
        cash=float(cash),
        levels_consumed=consumed,
        reject_reason=None if complete else "insufficient_depth",
    )


def evaluate_pair(
    up_book: L2Book,
    down_book: L2Book,
    direction: str,
    quantity: float,
    up_fee_rate: float,
    down_fee_rate: float,
    *,
    safety_margin_per_pair: float,
    fixed_operational_cost: float,
) -> PairEvaluation:
    mode = str(direction).upper()
    if mode not in {"BUY_BOTH_MERGE", "SPLIT_SELL_BOTH"}:
        raise ValueError(f"unknown complete-set direction: {direction!r}")
    action = "BUY" if mode == "BUY_BOTH_MERGE" else "SELL"
    up = execute_leg(up_book, action, quantity, up_fee_rate)
    down = execute_leg(down_book, action, quantity, down_fee_rate)
    if not up.complete or not down.complete:
        reason = up.reject_reason or down.reject_reason or "incomplete_pair"
        return PairEvaluation(
            mode,
            float(quantity),
            False,
            None,
            None,
            None,
            None,
            up,
            down,
            reason,
        )

    q = float(quantity)
    if mode == "BUY_BOTH_MERGE":
        raw_net = q - up.cash - down.cash
    else:
        raw_net = up.cash + down.cash - q
    conservative_net = (
        raw_net - q * float(safety_margin_per_pair) - float(fixed_operational_cost)
    )
    return PairEvaluation(
        direction=mode,
        quantity=q,
        complete=True,
        raw_net_usd=raw_net,
        conservative_net_usd=conservative_net,
        raw_net_per_pair=raw_net / q,
        conservative_net_per_pair=conservative_net / q,
        up=up,
        down=down,
        reject_reason=None,
    )


def _cumulative_depths(book: L2Book, action: str) -> list[float]:
    side = "SELL" if action == "BUY" else "BUY"
    total = ZERO
    values: list[float] = []
    for _, size in book.sorted_levels(side):
        total += size
        values.append(float(total))
    return values


def capacity_summary(
    up_book: L2Book,
    down_book: L2Book,
    direction: str,
    up_fee_rate: float,
    down_fee_rate: float,
    *,
    safety_margin_per_pair: float,
    fixed_operational_cost: float,
    minimum_quantity: float,
) -> dict[str, float | None]:
    """Return best-profit size and maximum positive-EV size over all depth breakpoints."""
    action = "BUY" if direction == "BUY_BOTH_MERGE" else "SELL"
    up_depths = _cumulative_depths(up_book, action)
    down_depths = _cumulative_depths(down_book, action)
    if not up_depths or not down_depths:
        return {
            "available_quantity": 0.0,
            "best_profit_quantity": None,
            "best_conservative_net_usd": None,
            "max_profitable_quantity": None,
        }
    available = min(up_depths[-1], down_depths[-1])
    points = sorted(
        {
            float(min(available, value))
            for value in [*up_depths, *down_depths, available, minimum_quantity]
            if minimum_quantity <= value <= available
        }
    )
    evaluated: list[PairEvaluation] = [
        evaluate_pair(
            up_book,
            down_book,
            direction,
            quantity,
            up_fee_rate,
            down_fee_rate,
            safety_margin_per_pair=safety_margin_per_pair,
            fixed_operational_cost=fixed_operational_cost,
        )
        for quantity in points
    ]
    complete = [
        value
        for value in evaluated
        if value.complete and value.conservative_net_usd is not None
    ]
    if not complete:
        return {
            "available_quantity": available,
            "best_profit_quantity": None,
            "best_conservative_net_usd": None,
            "max_profitable_quantity": None,
        }
    best = max(complete, key=lambda value: float(value.conservative_net_usd))
    positive = [
        value for value in complete if float(value.conservative_net_usd) > 0.0
    ]
    max_positive = max(positive, key=lambda value: value.quantity) if positive else None

    max_profitable = max_positive.quantity if max_positive else None
    if max_positive is not None:
        later_negative = next(
            (
                value
                for value in complete
                if value.quantity > max_positive.quantity
                and float(value.conservative_net_usd) <= 0.0
            ),
            None,
        )
        if later_negative is not None:
            low = max_positive.quantity
            high = later_negative.quantity
            for _ in range(40):
                mid = (low + high) / 2.0
                result = evaluate_pair(
                    up_book,
                    down_book,
                    direction,
                    mid,
                    up_fee_rate,
                    down_fee_rate,
                    safety_margin_per_pair=safety_margin_per_pair,
                    fixed_operational_cost=fixed_operational_cost,
                )
                if (
                    result.complete
                    and result.conservative_net_usd is not None
                    and result.conservative_net_usd > 0.0
                ):
                    low = mid
                else:
                    high = mid
            max_profitable = low
    return {
        "available_quantity": available,
        "best_profit_quantity": best.quantity,
        "best_conservative_net_usd": float(best.conservative_net_usd),
        "max_profitable_quantity": max_profitable,
    }


def staggered_pair_net(
    direction: str,
    quantity: float,
    entry: PairEvaluation,
    delayed: PairEvaluation,
    *,
    safety_margin_per_pair: float,
    fixed_operational_cost: float,
) -> dict[str, float | None]:
    """Stress the two possible leg orders when the second leg moves before execution."""
    if not entry.complete or not delayed.complete:
        return {
            "up_first_net_usd": None,
            "down_first_net_usd": None,
            "failed_leg_worst_net_usd": None,
        }
    q = float(quantity)
    costs = q * float(safety_margin_per_pair) + float(fixed_operational_cost)
    if direction == "BUY_BOTH_MERGE":
        up_first = q - entry.up.cash - delayed.down.cash - costs
        down_first = q - entry.down.cash - delayed.up.cash - costs
    elif direction == "SPLIT_SELL_BOTH":
        up_first = entry.up.cash + delayed.down.cash - q - costs
        down_first = entry.down.cash + delayed.up.cash - q - costs
    else:
        raise ValueError(f"unknown complete-set direction: {direction!r}")
    return {
        "up_first_net_usd": up_first,
        "down_first_net_usd": down_first,
        "failed_leg_worst_net_usd": min(up_first, down_first),
    }


def selftest() -> None:
    def book(asset: str, bid: float, ask: float, size: float = 100.0) -> L2Book:
        value = L2Book(asset)
        value.load_snapshot(
            [{"price": bid, "size": size}],
            [{"price": ask, "size": size}],
            exchange_ts_ms=1,
            recv_ts_ns=1,
        )
        return value

    up = book("up", 0.46, 0.47)
    down = book("down", 0.46, 0.47)
    result = evaluate_pair(
        up,
        down,
        "BUY_BOTH_MERGE",
        10,
        0.10,
        0.10,
        safety_margin_per_pair=0.0,
        fixed_operational_cost=0.0,
    )
    assert result.complete and result.conservative_net_usd is not None
    assert abs(result.conservative_net_usd - 0.1018) < 1e-9, result

    no_edge = evaluate_pair(
        book("up2", 0.48, 0.49),
        book("down2", 0.48, 0.49),
        "BUY_BOTH_MERGE",
        10,
        0.10,
        0.10,
        safety_margin_per_pair=0.0,
        fixed_operational_cost=0.0,
    )
    assert no_edge.conservative_net_usd is not None
    assert no_edge.conservative_net_usd < 0.0

    sell = evaluate_pair(
        book("up3", 0.53, 0.54),
        book("down3", 0.53, 0.54),
        "SPLIT_SELL_BOTH",
        10,
        0.10,
        0.10,
        safety_margin_per_pair=0.0,
        fixed_operational_cost=0.0,
    )
    assert sell.conservative_net_usd is not None
    assert sell.conservative_net_usd > 0.0
    assert fee_rate_from_base_bps(1000) == 0.10
    print("complete-set economics self-test: PASS")


if __name__ == "__main__":
    selftest()
