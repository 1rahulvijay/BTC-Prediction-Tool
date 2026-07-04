"""Deterministic Polymarket L2 book, exact taker VWAP, and maker queue estimates.

The order book uses Decimal internally because outcome-token prices are small decimal
fractions and execution research should not depend on binary float rounding.  Taker
VWAP is exact for the levels present in a synchronized book.  Queue estimates are
explicit scenarios: the public market channel does not expose order IDs or queue rank.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence


ZERO = Decimal("0")
ONE = Decimal("1")


def decimal_value(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


def crypto_taker_fee(price: Decimal, size: Decimal, fee_rate=Decimal("0.07")) -> Decimal:
    """Current crypto-market fee curve, configurable so recorded market metadata can replace it."""
    p = min(ONE, max(ZERO, decimal_value(price)))
    q = max(ZERO, decimal_value(size))
    rate = max(ZERO, decimal_value(fee_rate))
    return rate * p * (ONE - p) * q


@dataclass(frozen=True)
class VWAPResult:
    side: str
    requested_size: float
    filled_size: float
    unfilled_size: float
    complete: bool
    best_price: float | None
    worst_price: float | None
    vwap: float | None
    gross_notional: float
    fee: float
    total_cash: float
    all_in_unit_price: float | None
    slippage_per_share: float | None
    levels_consumed: int
    reject_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class L2Book:
    """A single outcome token's synchronized level-2 book."""

    def __init__(self, asset_id: str):
        self.asset_id = str(asset_id)
        self.market = ""
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.synchronized = False
        self.valid = False
        self.last_exchange_ts_ms = 0
        self.last_recv_ts_ns = 0
        self.book_hash = ""
        self.invalid_reason: str | None = "no_snapshot"

    @staticmethod
    def _parse_levels(levels: Iterable[Mapping]) -> dict[Decimal, Decimal]:
        parsed: dict[Decimal, Decimal] = {}
        for level in levels or ():
            price = decimal_value(level.get("price"))
            size = decimal_value(level.get("size"))
            if not ZERO < price < ONE or size < ZERO:
                raise ValueError(f"invalid book level price={price} size={size}")
            if size > ZERO:
                parsed[price] = size
        return parsed

    def load_snapshot(
        self,
        bids: Iterable[Mapping],
        asks: Iterable[Mapping],
        *,
        market: str = "",
        exchange_ts_ms: int = 0,
        recv_ts_ns: int = 0,
        book_hash: str = "",
    ) -> bool:
        if exchange_ts_ms and exchange_ts_ms < self.last_exchange_ts_ms:
            return False
        self.bids = self._parse_levels(bids)
        self.asks = self._parse_levels(asks)
        self.market = str(market or self.market)
        self.last_exchange_ts_ms = int(exchange_ts_ms or self.last_exchange_ts_ms)
        self.last_recv_ts_ns = int(recv_ts_ns or self.last_recv_ts_ns)
        self.book_hash = str(book_hash or "")
        self.synchronized = True
        self._validate()
        return self.valid

    def level_size(self, side: str, price) -> Decimal:
        ladder = self.bids if str(side).upper() == "BUY" else self.asks
        return ladder.get(decimal_value(price), ZERO)

    def apply_price_change(
        self,
        side: str,
        price,
        size,
        *,
        exchange_ts_ms: int = 0,
        recv_ts_ns: int = 0,
    ) -> bool:
        if not self.synchronized:
            self.valid = False
            self.invalid_reason = "increment_before_snapshot"
            return False
        if exchange_ts_ms and exchange_ts_ms < self.last_exchange_ts_ms:
            return False
        direction = str(side).upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError(f"unknown level side: {side!r}")
        p = decimal_value(price)
        q = decimal_value(size)
        if not ZERO < p < ONE or q < ZERO:
            raise ValueError(f"invalid price change price={p} size={q}")
        ladder = self.bids if direction == "BUY" else self.asks
        if q == ZERO:
            ladder.pop(p, None)
        else:
            ladder[p] = q
        self.last_exchange_ts_ms = int(exchange_ts_ms or self.last_exchange_ts_ms)
        self.last_recv_ts_ns = int(recv_ts_ns or self.last_recv_ts_ns)
        self._validate()
        return self.valid

    def _validate(self) -> None:
        if not self.synchronized:
            self.valid = False
            self.invalid_reason = "no_snapshot"
            return
        if not self.bids or not self.asks:
            self.valid = False
            self.invalid_reason = "one_sided_book"
            return
        if max(self.bids) >= min(self.asks):
            self.valid = False
            self.invalid_reason = "crossed_book"
            return
        self.valid = True
        self.invalid_reason = None

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def sorted_levels(self, side: str) -> list[tuple[Decimal, Decimal]]:
        direction = str(side).upper()
        if direction == "BUY":
            return sorted(self.bids.items(), reverse=True)
        if direction == "SELL":
            return sorted(self.asks.items())
        raise ValueError(f"unknown book side: {side!r}")

    def execution_vwap(self, action: str, size, fee_rate=Decimal("0.07")) -> VWAPResult:
        """Consume asks for BUY or bids for SELL; reject stale/invalid state upstream."""
        direction = str(action).upper()
        requested = decimal_value(size)
        if requested <= ZERO:
            raise ValueError("execution size must be positive")
        if direction not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        if not self.valid:
            return VWAPResult(
                direction, float(requested), 0.0, float(requested), False,
                None, None, None, 0.0, 0.0, 0.0, None, None, 0,
                self.invalid_reason or "invalid_book",
            )

        levels = self.sorted_levels("SELL" if direction == "BUY" else "BUY")
        remaining = requested
        notional = ZERO
        fees = ZERO
        filled = ZERO
        worst = None
        consumed = 0
        for price, available in levels:
            take = min(remaining, available)
            if take <= ZERO:
                continue
            notional += price * take
            fees += crypto_taker_fee(price, take, fee_rate)
            filled += take
            remaining -= take
            worst = price
            consumed += 1
            if remaining == ZERO:
                break

        best = levels[0][0] if levels else None
        vwap = notional / filled if filled else None
        # A seller receives gross proceeds minus fee; a buyer pays gross plus fee.
        total_cash = notional + fees if direction == "BUY" else notional - fees
        all_in = total_cash / filled if filled else None
        slippage = None
        if vwap is not None and best is not None:
            slippage = vwap - best if direction == "BUY" else best - vwap
        return VWAPResult(
            side=direction,
            requested_size=float(requested),
            filled_size=float(filled),
            unfilled_size=float(remaining),
            complete=remaining == ZERO,
            best_price=float(best) if best is not None else None,
            worst_price=float(worst) if worst is not None else None,
            vwap=float(vwap) if vwap is not None else None,
            gross_notional=float(notional),
            fee=float(fees),
            total_cash=float(total_cash),
            all_in_unit_price=float(all_in) if all_in is not None else None,
            slippage_per_share=float(slippage) if slippage is not None else None,
            levels_consumed=consumed,
            reject_reason=None if remaining == ZERO else "insufficient_depth",
        )

    def summary(self) -> dict:
        bid = self.best_bid
        ask = self.best_ask
        return {
            "asset_id": self.asset_id,
            "market": self.market,
            "synchronized": self.synchronized,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
            "best_bid": float(bid) if bid is not None else None,
            "best_ask": float(ask) if ask is not None else None,
            "best_bid_size": float(self.bids.get(bid, ZERO)) if bid is not None else None,
            "best_ask_size": float(self.asks.get(ask, ZERO)) if ask is not None else None,
            "spread": float(ask - bid) if bid is not None and ask is not None else None,
            "bid_levels": len(self.bids),
            "ask_levels": len(self.asks),
            "bid_depth": float(sum(self.bids.values(), ZERO)),
            "ask_depth": float(sum(self.asks.values(), ZERO)),
            "last_exchange_ts_ms": self.last_exchange_ts_ms,
            "last_recv_ts_ns": self.last_recv_ts_ns,
            "book_hash": self.book_hash,
        }


@dataclass(frozen=True)
class QueueEvent:
    ts_ns: int
    kind: str
    price: float
    size: float = 0.0
    side: str = ""
    new_level_size: float | None = None


@dataclass(frozen=True)
class QueueEstimate:
    mode: str
    order_side: str
    price: float
    requested_size: float
    filled_size: float
    fill_ratio: float
    queue_ahead_end: float
    time_to_first_fill_ms: float | None
    time_to_full_fill_ms: float | None
    active_ts_ns: int
    end_ts_ns: int
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


class QueueEstimator:
    """Scenario-based FIFO queue estimator for one hypothetical maker order."""

    CANCEL_CREDIT = {"conservative": Decimal("0"), "base": Decimal("0.5"), "optimistic": Decimal("1")}

    def __init__(self, order_side: str, price, size, queue_ahead, mode: str, active_ts_ns: int):
        self.order_side = str(order_side).upper()
        if self.order_side not in {"BUY", "SELL"}:
            raise ValueError("order_side must be BUY or SELL")
        if mode not in self.CANCEL_CREDIT:
            raise ValueError(f"unknown queue mode: {mode}")
        self.mode = mode
        self.price = decimal_value(price)
        self.requested = decimal_value(size)
        self.queue_ahead = max(ZERO, decimal_value(queue_ahead))
        if self.requested <= ZERO:
            raise ValueError("queue order size must be positive")
        self.filled = ZERO
        self.active_ts_ns = int(active_ts_ns)
        self.first_fill_ts_ns: int | None = None
        self.full_fill_ts_ns: int | None = None
        self.last_ts_ns = int(active_ts_ns)
        self.public_level_size = self.queue_ahead

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.requested - self.filled)

    def on_level_change(self, new_size, ts_ns: int) -> None:
        new_level = max(ZERO, decimal_value(new_size))
        reduction = max(ZERO, self.public_level_size - new_level)
        credit = reduction * self.CANCEL_CREDIT[self.mode]
        self.queue_ahead = max(ZERO, self.queue_ahead - credit)
        self.public_level_size = new_level
        self.last_ts_ns = max(self.last_ts_ns, int(ts_ns))

    def on_trade(self, aggressor_side: str, size, ts_ns: int) -> None:
        aggressor = str(aggressor_side).upper()
        required = "SELL" if self.order_side == "BUY" else "BUY"
        if aggressor != required or self.remaining == ZERO:
            self.last_ts_ns = max(self.last_ts_ns, int(ts_ns))
            return
        volume = max(ZERO, decimal_value(size))
        ahead_before = self.queue_ahead
        self.queue_ahead = max(ZERO, self.queue_ahead - volume)
        eligible = max(ZERO, volume - ahead_before)
        fill = min(self.remaining, eligible)
        if fill > ZERO:
            self.filled += fill
            if self.first_fill_ts_ns is None:
                self.first_fill_ts_ns = int(ts_ns)
            if self.remaining == ZERO:
                self.full_fill_ts_ns = int(ts_ns)
        self.public_level_size = max(ZERO, self.public_level_size - volume)
        self.last_ts_ns = max(self.last_ts_ns, int(ts_ns))

    def result(self, end_ts_ns: int | None = None) -> QueueEstimate:
        end = int(end_ts_ns or self.last_ts_ns)
        status = "FULL" if self.remaining == ZERO else "PARTIAL" if self.filled > ZERO else "UNFILLED"
        first_ms = ((self.first_fill_ts_ns - self.active_ts_ns) / 1e6
                    if self.first_fill_ts_ns is not None else None)
        full_ms = ((self.full_fill_ts_ns - self.active_ts_ns) / 1e6
                   if self.full_fill_ts_ns is not None else None)
        return QueueEstimate(
            self.mode, self.order_side, float(self.price), float(self.requested), float(self.filled),
            float(self.filled / self.requested), float(self.queue_ahead), first_ms, full_ms,
            self.active_ts_ns, end, status,
        )


def simulate_queue(
    *,
    order_side: str,
    price,
    size,
    displayed_size,
    decision_ts_ns: int,
    events: Sequence[QueueEvent],
    mode: str = "conservative",
    submit_latency_ms: float = 0.0,
    expiry_ts_ns: int | None = None,
) -> QueueEstimate:
    """Replay public events causally after simulated order activation."""
    active = int(decision_ts_ns + max(0.0, submit_latency_ms) * 1e6)
    current_level = decimal_value(displayed_size)
    ordered = sorted(events, key=lambda event: event.ts_ns)
    for event in ordered:
        if event.ts_ns >= active:
            break
        if decimal_value(event.price) == decimal_value(price) and event.kind == "LEVEL" and event.new_level_size is not None:
            current_level = max(ZERO, decimal_value(event.new_level_size))
    estimator = QueueEstimator(order_side, price, size, current_level, mode, active)
    end = int(expiry_ts_ns or (ordered[-1].ts_ns if ordered else active))
    for event in ordered:
        if event.ts_ns < active or event.ts_ns > end:
            continue
        if decimal_value(event.price) != estimator.price:
            continue
        if event.kind == "LEVEL" and event.new_level_size is not None:
            estimator.on_level_change(event.new_level_size, event.ts_ns)
        elif event.kind == "TRADE":
            estimator.on_trade(event.side, event.size, event.ts_ns)
        if estimator.remaining == ZERO:
            break
    return estimator.result(end)
