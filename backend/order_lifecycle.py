"""Strict, durable order lifecycle and duplicate-order reservation.

This module remains adapter-neutral, but its invariants are production invariants:

* local timeout/5xx outcomes become UNKNOWN, never REJECTED;
* transitions follow an explicit matrix and terminal states are immutable;
* cumulative fill cannot decrease, exceed the request or contradict the state;
* every nonterminal order reserves its instrument against duplicate submission;
* optional DuckDB persistence restores unresolved orders before trading resumes.

No real adapter is enabled by this module.
"""
from __future__ import annotations

import math
import sys
import time
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class OrderState(StrEnum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


TERMINAL = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }
)

NONTERMINAL = frozenset(set(OrderState) - TERMINAL)

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING_SUBMIT: frozenset(
        {OrderState.SUBMITTED, OrderState.UNKNOWN}
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.UNKNOWN,
        }
    ),
    # UNKNOWN is reconciled only from an explicit venue query/user stream. It may resolve to
    # any venue state that can truthfully follow an uncertain submit.
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
            OrderState.UNKNOWN,
        }
    ),
}

LOCAL_FAILURES = frozenset(
    {
        "timeout",
        "connection_error",
        "read_timeout",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
        "unknown_error",
    }
)


class DoubleFillRisk(RuntimeError):
    """Raised when an instrument already has a live order reservation."""


class InvalidOrderTransition(RuntimeError):
    """Raised when venue state contradicts the lifecycle contract."""


def _positive_finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


class OrderRecord:
    def __init__(
        self,
        client_order_id: str,
        instrument: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> None:
        if not str(client_order_id).strip():
            raise ValueError("client_order_id is required")
        if not str(instrument).strip():
            raise ValueError("instrument is required")
        self.client_order_id = str(client_order_id)
        self.instrument = str(instrument)
        self.quantity = _positive_finite(quantity, "quantity")
        self.reduce_only = bool(reduce_only)
        self.state = OrderState.PENDING_SUBMIT
        self.venue_order_id: str | None = None
        self.filled_quantity = 0.0
        self.history: list[tuple[float, OrderState, str]] = []
        self._on_change: Callable[["OrderRecord"], None] | None = None
        self._record("created")

    def set_change_callback(
        self, callback: Callable[["OrderRecord"], None] | None
    ) -> None:
        self._on_change = callback

    def _record(self, reason: str) -> None:
        self.history.append((time.time(), self.state, reason))
        if self._on_change is not None:
            self._on_change(self)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def active(self) -> bool:
        return self.state in NONTERMINAL

    @property
    def needs_reconciliation(self) -> bool:
        return self.state == OrderState.UNKNOWN

    def _transition(
        self,
        target: OrderState,
        *,
        venue_order_id: str | None = None,
        filled_quantity: float | None = None,
        reason: str = "",
    ) -> None:
        target = OrderState(target)
        if self.terminal:
            if target == self.state:
                return
            raise InvalidOrderTransition(
                f"terminal order {self.client_order_id} cannot move "
                f"{self.state}->{target}"
            )
        allowed = ALLOWED_TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            raise InvalidOrderTransition(
                f"order {self.client_order_id} cannot move {self.state}->{target}"
            )

        cumulative = self.filled_quantity
        if filled_quantity is not None:
            try:
                cumulative = float(filled_quantity)
            except (TypeError, ValueError) as exc:
                raise ValueError("filled_quantity must be numeric") from exc
            if not math.isfinite(cumulative) or cumulative < 0:
                raise ValueError("filled_quantity must be finite and non-negative")
            if cumulative + 1e-12 < self.filled_quantity:
                raise ValueError("cumulative filled_quantity cannot decrease")
            if cumulative > self.quantity + 1e-12:
                raise ValueError("filled_quantity cannot exceed requested quantity")

        if target == OrderState.PARTIALLY_FILLED and not (
            0 < cumulative < self.quantity
        ):
            raise ValueError(
                "PARTIALLY_FILLED requires 0 < filled_quantity < requested quantity"
            )
        if target == OrderState.FILLED and not math.isclose(
            cumulative, self.quantity, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("FILLED requires filled_quantity == requested quantity")
        if target == OrderState.REJECTED and cumulative != 0:
            raise ValueError("REJECTED cannot carry a fill")

        self.state = target
        self.filled_quantity = cumulative
        if venue_order_id:
            self.venue_order_id = str(venue_order_id)
        self._record(f"venue:{reason or target}")

    def mark_submitted(self) -> None:
        self._transition(OrderState.SUBMITTED, reason="submitted")

    def mark_local_failure(self, kind: str) -> None:
        """A local failure never proves venue rejection."""
        if kind not in LOCAL_FAILURES:
            raise ValueError(f"{kind!r} is not a local failure; query the venue")
        if self.terminal:
            return
        self._transition(
            OrderState.UNKNOWN,
            filled_quantity=self.filled_quantity,
            reason=f"local_failure:{kind}",
        )

    def mark_venue_response(
        self,
        state: OrderState,
        *,
        venue_order_id: str | None = None,
        filled_quantity: float | None = None,
        reason: str = "",
    ) -> None:
        self._transition(
            state,
            venue_order_id=venue_order_id,
            filled_quantity=filled_quantity,
            reason=reason,
        )

    def status(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "instrument": self.instrument,
            "requested_quantity": self.quantity,
            "state": str(self.state),
            "terminal": self.terminal,
            "active": self.active,
            "needs_reconciliation": self.needs_reconciliation,
            "venue_order_id": self.venue_order_id,
            "filled_quantity": self.filled_quantity,
            "transitions": len(self.history),
        }


class _DurableOrderStore:
    def __init__(self, path: str | Path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        import duckdb

        return duckdb.connect(self.path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_lifecycle (
                    client_order_id VARCHAR PRIMARY KEY,
                    instrument VARCHAR NOT NULL,
                    requested_quantity DOUBLE NOT NULL,
                    reduce_only BOOLEAN NOT NULL,
                    state VARCHAR NOT NULL,
                    venue_order_id VARCHAR,
                    filled_quantity DOUBLE NOT NULL,
                    updated_at_s DOUBLE NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS order_lifecycle_events (
                    client_order_id VARCHAR NOT NULL,
                    event_index BIGINT NOT NULL,
                    event_ts_s DOUBLE NOT NULL,
                    state VARCHAR NOT NULL,
                    reason VARCHAR NOT NULL,
                    filled_quantity DOUBLE NOT NULL,
                    venue_order_id VARCHAR,
                    PRIMARY KEY(client_order_id, event_index)
                )
                """
            )

    def save(self, record: OrderRecord) -> None:
        event_index = len(record.history) - 1
        event_ts, event_state, reason = record.history[-1]
        with self._connect() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO order_lifecycle VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_order_id) DO UPDATE SET
                        instrument=excluded.instrument,
                        requested_quantity=excluded.requested_quantity,
                        reduce_only=excluded.reduce_only,
                        state=excluded.state,
                        venue_order_id=excluded.venue_order_id,
                        filled_quantity=excluded.filled_quantity,
                        updated_at_s=excluded.updated_at_s
                    """,
                    (
                        record.client_order_id,
                        record.instrument,
                        record.quantity,
                        record.reduce_only,
                        str(record.state),
                        record.venue_order_id,
                        record.filled_quantity,
                        event_ts,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO order_lifecycle_events
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.client_order_id,
                        event_index,
                        event_ts,
                        str(event_state),
                        reason,
                        record.filled_quantity,
                        record.venue_order_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def load(self) -> list[OrderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT client_order_id, instrument, requested_quantity, reduce_only,
                       state, venue_order_id, filled_quantity
                FROM order_lifecycle
                """
            ).fetchall()
            histories = connection.execute(
                """
                SELECT client_order_id, event_ts_s, state, reason
                FROM order_lifecycle_events
                ORDER BY client_order_id, event_index
                """
            ).fetchall()
        history_by_id: dict[str, list[tuple[float, OrderState, str]]] = {}
        for order_id, event_ts, state, reason in histories:
            history_by_id.setdefault(str(order_id), []).append(
                (float(event_ts), OrderState(state), str(reason))
            )
        records = []
        for order_id, instrument, quantity, reduce_only, state, venue_id, filled in rows:
            record = OrderRecord(order_id, instrument, quantity, reduce_only)
            record.state = OrderState(state)
            record.venue_order_id = str(venue_id) if venue_id else None
            record.filled_quantity = float(filled)
            record.history = history_by_id.get(str(order_id)) or record.history
            records.append(record)
        return records


class OrderBookkeeper:
    """Tracks reservations and optionally persists them across process restarts."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._orders: dict[str, OrderRecord] = {}
        self._store = _DurableOrderStore(storage_path) if storage_path else None
        if self._store is not None:
            for record in self._store.load():
                record.set_change_callback(self._persist)
                self._orders[record.client_order_id] = record

    def _persist(self, record: OrderRecord) -> None:
        if self._store is not None:
            self._store.save(record)

    def active(self, instrument: str | None = None) -> list[OrderRecord]:
        return [
            order
            for order in self._orders.values()
            if order.active
            and (instrument is None or order.instrument == instrument)
        ]

    def unreconciled(self, instrument: str | None = None) -> list[OrderRecord]:
        return [
            order
            for order in self._orders.values()
            if order.needs_reconciliation
            and (instrument is None or order.instrument == instrument)
        ]

    def position_known(self, instrument: str) -> bool:
        return not self.unreconciled(instrument)

    def place(
        self,
        client_order_id: str,
        instrument: str,
        quantity: float,
        reduce_only: bool = False,
    ) -> OrderRecord:
        blocking = self.active(instrument)
        if blocking:
            raise DoubleFillRisk(
                f"refusing to place on {instrument!r}: active reservation(s) "
                f"{[order.client_order_id for order in blocking]}"
            )
        if client_order_id in self._orders:
            raise ValueError(f"duplicate client_order_id {client_order_id!r}")
        record = OrderRecord(client_order_id, instrument, quantity, reduce_only)
        self._orders[client_order_id] = record
        record.set_change_callback(self._persist)
        self._persist(record)
        return record

    def reconcile(
        self,
        client_order_id: str,
        venue_state: OrderState,
        *,
        filled_quantity: float | None = None,
        venue_order_id: str | None = None,
    ) -> OrderRecord:
        record = self._orders[client_order_id]
        if not record.needs_reconciliation:
            raise InvalidOrderTransition(
                f"{client_order_id} is {record.state}, not UNKNOWN"
            )
        record.mark_venue_response(
            venue_state,
            venue_order_id=venue_order_id,
            filled_quantity=filled_quantity,
            reason="reconciled",
        )
        return record

    def status(self) -> dict[str, Any]:
        unknown = self.unreconciled()
        active = self.active()
        return {
            "orders": len(self._orders),
            "active": [order.client_order_id for order in active],
            "unreconciled": [order.client_order_id for order in unknown],
            "reservations": {
                order.instrument: order.client_order_id for order in active
            },
            "blockers": [
                f"order_unreconciled:{order.client_order_id}" for order in unknown
            ],
            "healthy": not unknown,
            "durable": self._store is not None,
        }


def selftest() -> int:  # noqa: C901
    import tempfile

    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {message}")
        ok = ok and condition

    print("quantity and transition invariants")
    for invalid in (0, -1, float("nan"), float("inf")):
        try:
            OrderRecord("bad", "BTCUSDT", invalid)
            check(False, f"invalid quantity {invalid!r} must fail")
        except ValueError:
            check(True, f"invalid quantity {invalid!r} is refused")

    order = OrderRecord("o1", "BTCUSDT", 2.0)
    order.mark_submitted()
    order.mark_venue_response(OrderState.ACKNOWLEDGED, venue_order_id="V1")
    order.mark_venue_response(OrderState.PARTIALLY_FILLED, filled_quantity=1.0)
    try:
        order.mark_venue_response(OrderState.PARTIALLY_FILLED, filled_quantity=0.5)
        check(False, "cumulative fill may not decrease")
    except ValueError:
        check(True, "cumulative fill may not decrease")
    order.mark_venue_response(OrderState.FILLED, filled_quantity=2.0)
    try:
        order.mark_venue_response(OrderState.CANCELED, filled_quantity=2.0)
        check(False, "FILLED may not become CANCELED")
    except InvalidOrderTransition:
        check(True, "terminal state is immutable")

    print("state and fill contradictions")
    for target, filled in (
        (OrderState.PARTIALLY_FILLED, 0.0),
        (OrderState.PARTIALLY_FILLED, 2.0),
        (OrderState.FILLED, 1.0),
        (OrderState.REJECTED, 0.5),
    ):
        candidate = OrderRecord(f"bad-{target}-{filled}", "BTCUSDT", 2.0)
        candidate.mark_submitted()
        try:
            candidate.mark_venue_response(target, filled_quantity=filled)
            check(False, f"{target} with fill={filled} must fail")
        except ValueError:
            check(True, f"{target} with fill={filled} is refused")

    print("all nonterminal orders reserve the instrument")
    for state in (
        OrderState.PENDING_SUBMIT,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.UNKNOWN,
    ):
        books = OrderBookkeeper()
        first = books.place("first", "BTCUSDT", 2.0)
        if state != OrderState.PENDING_SUBMIT:
            first.mark_submitted()
        if state == OrderState.ACKNOWLEDGED:
            first.mark_venue_response(OrderState.ACKNOWLEDGED)
        elif state == OrderState.PARTIALLY_FILLED:
            first.mark_venue_response(
                OrderState.PARTIALLY_FILLED, filled_quantity=1.0
            )
        elif state == OrderState.UNKNOWN:
            first.mark_local_failure("timeout")
        try:
            books.place("second", "BTCUSDT", 1.0)
            check(False, f"{state} must reserve the instrument")
        except DoubleFillRisk:
            check(True, f"{state} reserves the instrument")

    print("local uncertainty and explicit reconciliation")
    books = OrderBookkeeper()
    uncertain = books.place("u1", "BTCUSDT", 1.0)
    uncertain.mark_submitted()
    uncertain.mark_local_failure("http_503")
    check(not books.position_known("BTCUSDT"), "UNKNOWN makes position unknown")
    books.reconcile(
        "u1",
        OrderState.FILLED,
        filled_quantity=1.0,
        venue_order_id="V2",
    )
    check(books.position_known("BTCUSDT"), "venue reconciliation restores knowledge")

    print("durable restart preserves reservations and history")
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "orders.duckdb"
        durable = OrderBookkeeper(path)
        pending = durable.place("d1", "BTCUSDT", 3.0)
        pending.mark_submitted()
        pending.mark_venue_response(
            OrderState.PARTIALLY_FILLED, filled_quantity=1.0
        )
        restarted = OrderBookkeeper(path)
        restored = restarted._orders["d1"]
        check(
            restored.state == OrderState.PARTIALLY_FILLED
            and restored.filled_quantity == 1.0,
            "state and cumulative fill survive restart",
        )
        check(len(restored.history) == 3, "transition history survives restart")
        try:
            restarted.place("d2", "BTCUSDT", 1.0)
            check(False, "restored nonterminal order must reserve the instrument")
        except DoubleFillRisk:
            check(True, "restored nonterminal order reserves the instrument")

    print("ORDER LIFECYCLE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
