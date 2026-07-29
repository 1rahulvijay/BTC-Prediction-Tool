"""Order lifecycle for a real venue. A timeout means UNKNOWN, never FAILED.

WHY THIS EXISTS BEFORE ANY REAL ADAPTER DOES
    binance_paper's FillStatus is FILLED / PARTIAL / REJECTED. There is no state that can
    represent "the request did not come back". That is harmless for a paper engine, where the
    fill is computed locally and deterministically - and dangerous the moment a network sits
    between the decision and the match.

    A submit that times out has three possible truths: the venue never saw it, the venue saw it
    and rejected it, or the venue saw it and FILLED it. Coercing that into REJECTED is the
    classic way to end up with twice the intended position: the code concludes "no fill", places
    the order again, and both fill. Coercing it into FILLED is the mirror error - you believe you
    hold a position you do not have, and never close it.

    Binance's own REST documentation warns that on a timeout or certain 5xx responses the
    execution status is UNKNOWN and must be resolved by querying the order or the user stream.
    This module makes that the only representable behaviour.

THE RULES ENFORCED HERE
    1. A local timeout, connection error or 5xx maps to UNKNOWN. Nothing local may map to
       REJECTED - only an explicit venue rejection can.
    2. UNKNOWN is NOT terminal. It is resolved by ASKING the venue, never by waiting, retrying
       or assuming.
    3. While any order on an instrument is UNKNOWN, no new order may be placed on that
       instrument. This is the actual double-fill guard.
    4. An UNKNOWN order means the position is unknown. RiskEngine already hard-blocks on
       `unknown_position` even for reduce-only, so the two compose: you cannot "just flatten"
       your way out of an unreconciled state, because you do not know what to flatten.

    python backend/order_lifecycle.py --selftest
"""
from __future__ import annotations

import sys
import time
from enum import StrEnum
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class OrderState(StrEnum):
    PENDING_SUBMIT = "PENDING_SUBMIT"      # created locally, not yet sent
    SUBMITTED = "SUBMITTED"                # sent, no acknowledgement yet
    ACKNOWLEDGED = "ACKNOWLEDGED"          # venue confirmed receipt and assigned an id
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"                      # terminal
    CANCELED = "CANCELED"                  # terminal
    REJECTED = "REJECTED"                  # terminal - venue EXPLICITLY refused
    EXPIRED = "EXPIRED"                    # terminal
    UNKNOWN = "UNKNOWN"                    # not terminal - requires reconciliation


TERMINAL = frozenset({
    OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED,
})

# Local failures. NONE of these may become REJECTED: the venue may still have the order.
LOCAL_FAILURES = frozenset({
    "timeout", "connection_error", "read_timeout", "http_500", "http_502",
    "http_503", "http_504", "unknown_error",
})


class DoubleFillRisk(RuntimeError):
    """Raised when placing an order while an unreconciled order exists on that instrument."""


class OrderRecord:
    def __init__(self, client_order_id: str, instrument: str, quantity: float,
                 reduce_only: bool = False) -> None:
        self.client_order_id = client_order_id
        self.instrument = instrument
        self.quantity = float(quantity)
        self.reduce_only = bool(reduce_only)
        self.state = OrderState.PENDING_SUBMIT
        self.venue_order_id: str | None = None
        self.filled_quantity = 0.0
        self.history: list[tuple[float, OrderState, str]] = []
        self._record("created")

    def _record(self, reason: str) -> None:
        self.history.append((time.time(), self.state, reason))

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def needs_reconciliation(self) -> bool:
        return self.state == OrderState.UNKNOWN

    def mark_submitted(self) -> None:
        self.state = OrderState.SUBMITTED
        self._record("submitted")

    def mark_local_failure(self, kind: str) -> None:
        """A local failure NEVER means the venue refused the order.

        This is the whole point of the module. `kind` is recorded so reconciliation can report
        what happened, but the resulting state is UNKNOWN regardless of which local error it was.
        """
        if kind not in LOCAL_FAILURES:
            raise ValueError(f"{kind!r} is not a local failure; use mark_venue_response")
        if self.terminal:
            return                                  # a settled order is not un-settled by noise
        self.state = OrderState.UNKNOWN
        self._record(f"local_failure:{kind}")

    def mark_venue_response(self, state: OrderState, *, venue_order_id: str | None = None,
                            filled_quantity: float | None = None, reason: str = "") -> None:
        """Only the VENUE may move an order to a terminal state."""
        self.state = OrderState(state)
        if venue_order_id:
            self.venue_order_id = venue_order_id
        if filled_quantity is not None:
            self.filled_quantity = float(filled_quantity)
        self._record(f"venue:{reason or state}")

    def status(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "instrument": self.instrument,
            "state": str(self.state),
            "terminal": self.terminal,
            "needs_reconciliation": self.needs_reconciliation,
            "venue_order_id": self.venue_order_id,
            "filled_quantity": self.filled_quantity,
            "transitions": len(self.history),
        }


class OrderBookkeeper:
    """Tracks every order and refuses to place a new one while any is unreconciled."""

    def __init__(self) -> None:
        self._orders: dict[str, OrderRecord] = {}

    def unreconciled(self, instrument: str | None = None) -> list[OrderRecord]:
        return [
            o for o in self._orders.values()
            if o.needs_reconciliation and (instrument is None or o.instrument == instrument)
        ]

    def position_known(self, instrument: str) -> bool:
        """False while any order on this instrument is UNKNOWN.

        Feeds RiskState.position_known, which RiskEngine hard-blocks on even for reduce-only -
        so an unreconciled order cannot be escaped by flattening, because the size to flatten is
        exactly what is unknown."""
        return not self.unreconciled(instrument)

    def place(self, client_order_id: str, instrument: str, quantity: float,
              reduce_only: bool = False) -> OrderRecord:
        blocking = self.unreconciled(instrument)
        if blocking:
            raise DoubleFillRisk(
                f"refusing to place on '{instrument}': "
                f"{[o.client_order_id for o in blocking]} unreconciled. Resolve by QUERYING the "
                f"venue; a second order here is how one intended position becomes two")
        if client_order_id in self._orders:
            raise ValueError(f"duplicate client_order_id {client_order_id!r}")
        record = OrderRecord(client_order_id, instrument, quantity, reduce_only)
        self._orders[client_order_id] = record
        return record

    def reconcile(self, client_order_id: str, venue_state: OrderState, *,
                  filled_quantity: float = 0.0, venue_order_id: str | None = None) -> OrderRecord:
        """Resolve an UNKNOWN order with an answer OBTAINED FROM THE VENUE."""
        record = self._orders[client_order_id]
        if not record.needs_reconciliation:
            return record
        record.mark_venue_response(venue_state, venue_order_id=venue_order_id,
                                   filled_quantity=filled_quantity, reason="reconciled")
        return record

    def status(self) -> dict[str, Any]:
        pending = self.unreconciled()
        return {
            "orders": len(self._orders),
            "unreconciled": [o.client_order_id for o in pending],
            "blockers": [f"order_unreconciled:{o.client_order_id}" for o in pending],
            "healthy": not pending,
        }


def selftest() -> int:  # noqa: C901
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("no local failure may become REJECTED")
    for kind in sorted(LOCAL_FAILURES):
        order = OrderRecord("o1", "BTCUSDT", 1.0)
        order.mark_submitted()
        order.mark_local_failure(kind)
        chk(order.state == OrderState.UNKNOWN, f"{kind} -> UNKNOWN (not REJECTED)")
    chk(OrderState.UNKNOWN not in TERMINAL, "UNKNOWN is NOT terminal")
    chk(OrderState.REJECTED in TERMINAL, "REJECTED is terminal - only the venue may say it")

    print("an unreconciled order blocks a second order on that instrument")
    books = OrderBookkeeper()
    first = books.place("c1", "BTCUSDT", 1.0)
    first.mark_submitted()
    first.mark_local_failure("timeout")
    try:
        books.place("c2", "BTCUSDT", 1.0)
        chk(False, "a second order must be refused")
    except DoubleFillRisk as exc:
        chk("c1" in str(exc), f"refused, naming the blocking order ({str(exc)[:60]}...)")
    chk(books.place("c3", "ETHUSDT", 1.0) is not None,
        "a DIFFERENT instrument is unaffected")

    print("the position is unknown while an order is unreconciled")
    chk(books.position_known("BTCUSDT") is False, "BTCUSDT position is NOT known")
    chk(books.position_known("ETHUSDT") is True, "ETHUSDT position is known")

    print("only a venue answer resolves it")
    chk(first.needs_reconciliation, "the order still needs reconciliation after any local wait")
    books.reconcile("c1", OrderState.FILLED, filled_quantity=1.0, venue_order_id="V123")
    chk(first.state == OrderState.FILLED, "a venue FILLED answer resolves it")
    chk(first.filled_quantity == 1.0, "and records what actually filled")
    chk(books.position_known("BTCUSDT") is True, "the position is known again")
    chk(books.place("c4", "BTCUSDT", 1.0) is not None, "and trading may resume")

    print("the dangerous case: the timeout had actually FILLED")
    recovery = OrderBookkeeper()
    stuck = recovery.place("d1", "BTCUSDT", 1.0)
    stuck.mark_submitted()
    stuck.mark_local_failure("timeout")
    blocked = False
    try:
        recovery.place("d2", "BTCUSDT", 1.0)
    except DoubleFillRisk:
        blocked = True
    recovery.reconcile("d1", OrderState.FILLED, filled_quantity=1.0)
    chk(blocked and stuck.filled_quantity == 1.0,
        "the retry was blocked and the order HAD filled - exactly the double position avoided")

    print("a settled order is not un-settled by later noise")
    settled = OrderRecord("s1", "BTCUSDT", 1.0)
    settled.mark_venue_response(OrderState.FILLED, filled_quantity=1.0)
    settled.mark_local_failure("timeout")
    chk(settled.state == OrderState.FILLED, "a FILLED order stays FILLED through a later timeout")

    print("a non-local outcome cannot be smuggled in as a local failure")
    order = OrderRecord("x1", "BTCUSDT", 1.0)
    try:
        order.mark_local_failure("REJECTED")
        chk(False, "mark_local_failure must refuse a venue outcome")
    except ValueError:
        chk(True, "mark_local_failure refuses anything that is not a local failure")

    print("bookkeeper health surfaces unreconciled orders as blockers")
    pending = OrderBookkeeper()
    held = pending.place("p1", "BTCUSDT", 1.0)
    held.mark_submitted()
    held.mark_local_failure("http_503")
    status = pending.status()
    chk(status["healthy"] is False, "the bookkeeper reports unhealthy")
    chk(any("p1" in b for b in status["blockers"]), f"and names the order ({status['blockers']})")

    print("every transition is retained for audit")
    chk(len(held.history) >= 3, f"history records create/submit/failure ({len(held.history)})")

    print("\nORDER LIFECYCLE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
