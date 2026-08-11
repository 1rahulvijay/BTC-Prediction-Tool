"""DuckDB persistence isolated from Polymarket and analytics databases."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

import duckdb

from .paper_types import ExecutionResult, FillStatus, OrderRequest, PositionState


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_meta(
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_orders(
    order_id VARCHAR PRIMARY KEY,
    request_sha256 VARCHAR NOT NULL,
    decision_ts_ns BIGINT NOT NULL,
    fill_ts_ns BIGINT NOT NULL,
    instrument VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    requested_quantity DOUBLE NOT NULL,
    filled_quantity DOUBLE NOT NULL,
    average_price DOUBLE,
    filled_notional DOUBLE NOT NULL,
    fee DOUBLE NOT NULL,
    realized_pnl_gross DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    reduce_only BOOLEAN NOT NULL,
    leverage DOUBLE NOT NULL,
    reason_codes VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions(
    instrument VARCHAR PRIMARY KEY,
    quantity DOUBLE NOT NULL,
    average_entry DOUBLE NOT NULL,
    realized_pnl_gross DOUBLE NOT NULL,
    fees_paid DOUBLE NOT NULL,
    funding_pnl DOUBLE NOT NULL,
    cash_balance DOUBLE NOT NULL,
    leverage DOUBLE NOT NULL,
    updated_at_ns BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_funding(
    funding_id VARCHAR PRIMARY KEY,
    instrument VARCHAR NOT NULL,
    timestamp_ns BIGINT NOT NULL,
    position_quantity DOUBLE NOT NULL,
    mark_price DOUBLE NOT NULL,
    funding_rate DOUBLE NOT NULL,
    funding_pnl DOUBLE NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_equity_snapshots(
    timestamp_ns BIGINT NOT NULL,
    instrument VARCHAR NOT NULL,
    mark_price DOUBLE NOT NULL,
    equity DOUBLE NOT NULL,
    unrealized_pnl DOUBLE NOT NULL,
    initial_margin DOUBLE NOT NULL,
    available_balance DOUBLE NOT NULL,
    liquidation_price DOUBLE,
    PRIMARY KEY(timestamp_ns, instrument)
);
"""


class BinancePaperStore:
    def __init__(self, path: str | Path, starting_capital: float):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as con:
            con.execute(SCHEMA)
            order_columns = {
                str(row[1])
                for row in con.execute("PRAGMA table_info('paper_orders')").fetchall()
            }
            if "realized_pnl_gross" not in order_columns:
                con.execute(
                    "ALTER TABLE paper_orders ADD COLUMN "
                    "realized_pnl_gross DOUBLE DEFAULT 0.0"
                )
            position_columns = {
                str(row[1])
                for row in con.execute(
                    "PRAGMA table_info('paper_positions')"
                ).fetchall()
            }
            if "leverage" not in position_columns:
                con.execute(
                    "ALTER TABLE paper_positions ADD COLUMN leverage DOUBLE DEFAULT 1.0"
                )
            existing = con.execute(
                "SELECT value FROM paper_meta WHERE key = 'starting_capital'"
            ).fetchone()
            if existing is None:
                con.execute(
                    "INSERT INTO paper_meta VALUES ('starting_capital', ?)",
                    [repr(float(starting_capital))],
                )
            elif abs(float(existing[0]) - float(starting_capital)) > 1e-9:
                raise ValueError(
                    "starting_capital differs from the immutable paper database"
                )

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self.path))
        try:
            yield con
        finally:
            con.close()

    def load_position(
        self, instrument: str, starting_capital: float
    ) -> PositionState:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT instrument, quantity, average_entry, realized_pnl_gross, "
                "fees_paid, funding_pnl, cash_balance, leverage, updated_at_ns "
                "FROM paper_positions WHERE instrument = ?",
                [instrument],
            ).fetchone()
        if row is None:
            return PositionState(instrument=instrument, cash_balance=starting_capital)
        return PositionState(*row)

    def load_order(self, order_id: str) -> tuple[str, ExecutionResult] | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT request_sha256, status, requested_quantity, filled_quantity, "
                "average_price, filled_notional, fee, realized_pnl_gross, "
                "fill_ts_ns, reason_codes "
                "FROM paper_orders WHERE order_id = ?",
                [order_id],
            ).fetchone()
        if row is None:
            return None
        (
            request_sha,
            status,
            requested,
            filled,
            avg,
            notional,
            fee,
            realized,
            fill_ts,
            reasons,
        ) = row
        result = ExecutionResult(
            order_id=order_id,
            request_sha256=request_sha,
            status=FillStatus(status),
            requested_quantity=requested,
            filled_quantity=filled,
            average_price=avg,
            filled_notional=notional,
            fee=fee,
            realized_pnl_gross=realized,
            fill_ts_ns=fill_ts,
            reason_codes=tuple(filter(None, str(reasons).split("|"))),
        )
        return request_sha, result

    def commit_order(
        self,
        request: OrderRequest,
        result: ExecutionResult,
        position: PositionState,
    ) -> None:
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(
                    "INSERT INTO paper_orders "
                    "(order_id, request_sha256, decision_ts_ns, fill_ts_ns, "
                    "instrument, strategy_id, side, requested_quantity, "
                    "filled_quantity, average_price, filled_notional, fee, "
                    "realized_pnl_gross, status, reduce_only, leverage, reason_codes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        request.order_id,
                        request.request_sha256,
                        request.decision_ts_ns,
                        result.fill_ts_ns,
                        request.instrument,
                        request.strategy_id,
                        request.side.value,
                        request.quantity,
                        result.filled_quantity,
                        result.average_price,
                        result.filled_notional,
                        result.fee,
                        result.realized_pnl_gross,
                        result.status.value,
                        request.reduce_only,
                        request.leverage,
                        "|".join(result.reason_codes),
                    ],
                )
                con.execute(
                    "INSERT OR REPLACE INTO paper_positions "
                    "(instrument, quantity, average_entry, realized_pnl_gross, "
                    "fees_paid, funding_pnl, cash_balance, leverage, updated_at_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        position.instrument,
                        position.quantity,
                        position.average_entry,
                        position.realized_pnl_gross,
                        position.fees_paid,
                        position.funding_pnl,
                        position.cash_balance,
                        position.leverage,
                        position.updated_at_ns,
                    ],
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def commit_funding(
        self,
        funding_id: str,
        position: PositionState,
        timestamp_ns: int,
        mark_price: float,
        funding_rate: float,
        funding_pnl: float,
    ) -> bool:
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                existing = con.execute(
                    "SELECT instrument, timestamp_ns, position_quantity, mark_price, "
                    "funding_rate, funding_pnl FROM paper_funding WHERE funding_id = ?",
                    [funding_id],
                ).fetchone()
                if existing is not None:
                    incoming = (
                        position.instrument,
                        timestamp_ns,
                        position.quantity,
                        mark_price,
                        funding_rate,
                        funding_pnl,
                    )
                    if tuple(existing) != incoming:
                        raise ValueError(
                            "funding_id collision with different immutable content"
                        )
                    con.execute("COMMIT")
                    return False
                con.execute(
                    "INSERT INTO paper_funding VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        funding_id,
                        position.instrument,
                        timestamp_ns,
                        position.quantity,
                        mark_price,
                        funding_rate,
                        funding_pnl,
                    ],
                )
                con.execute(
                    "INSERT OR REPLACE INTO paper_positions "
                    "(instrument, quantity, average_entry, realized_pnl_gross, "
                    "fees_paid, funding_pnl, cash_balance, leverage, updated_at_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        position.instrument,
                        position.quantity,
                        position.average_entry,
                        position.realized_pnl_gross,
                        position.fees_paid,
                        position.funding_pnl,
                        position.cash_balance,
                        position.leverage,
                        position.updated_at_ns,
                    ],
                )
                con.execute("COMMIT")
                return True
            except Exception:
                con.execute("ROLLBACK")
                raise

    def append_equity(self, values: dict) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO paper_equity_snapshots "
                "(timestamp_ns, instrument, mark_price, equity, unrealized_pnl, "
                "initial_margin, available_balance, liquidation_price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    values["timestamp_ns"],
                    values["instrument"],
                    values["mark_price"],
                    values["equity"],
                    values["unrealized_pnl"],
                    values["initial_margin"],
                    values["available_balance"],
                    values["liquidation_price"],
                ],
            )

    def summary(self, limit: int = 50) -> dict:
        limit = max(1, min(int(limit), 500))
        with self._lock, self._connect() as con:
            counts = {
                table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "paper_orders",
                    "paper_funding",
                    "paper_equity_snapshots",
                )
            }
            rows = con.execute(
                "SELECT order_id, decision_ts_ns, fill_ts_ns, instrument, strategy_id, "
                "side, requested_quantity, filled_quantity, average_price, "
                "filled_notional, fee, realized_pnl_gross, status, reduce_only, "
                "leverage, reason_codes "
                "FROM paper_orders ORDER BY fill_ts_ns DESC, order_id DESC LIMIT ?",
                [limit],
            ).fetchall()
        keys = (
            "order_id",
            "decision_ts_ns",
            "fill_ts_ns",
            "instrument",
            "strategy_id",
            "side",
            "requested_quantity",
            "filled_quantity",
            "average_price",
            "filled_notional",
            "fee",
            "realized_pnl_gross",
            "status",
            "reduce_only",
            "leverage",
            "reason_codes",
        )
        return {
            "counts": counts,
            "recent_orders": [dict(zip(keys, row, strict=True)) for row in rows],
        }

    def pnl_since(self, instrument: str, cutoff_ns: int) -> float:
        with self._lock, self._connect() as con:
            order_pnl = con.execute(
                "SELECT coalesce(sum(realized_pnl_gross - fee), 0.0) "
                "FROM paper_orders WHERE instrument = ? AND fill_ts_ns >= ?",
                [instrument, cutoff_ns],
            ).fetchone()[0]
            funding_pnl = con.execute(
                "SELECT coalesce(sum(funding_pnl), 0.0) "
                "FROM paper_funding WHERE instrument = ? AND timestamp_ns >= ?",
                [instrument, cutoff_ns],
            ).fetchone()[0]
        return float(order_pnl) + float(funding_pnl)

    def replay_position(
        self, instrument: str, starting_capital: float
    ) -> PositionState:
        state = PositionState(instrument=instrument, cash_balance=starting_capital)
        with self._lock, self._connect() as con:
            orders = con.execute(
                "SELECT side, filled_quantity, average_price, fee, leverage, fill_ts_ns "
                "FROM paper_orders WHERE instrument = ? AND filled_quantity > 0 "
                "ORDER BY fill_ts_ns, order_id",
                [instrument],
            ).fetchall()
            funding = con.execute(
                "SELECT timestamp_ns, funding_pnl FROM paper_funding "
                "WHERE instrument = ? ORDER BY timestamp_ns, funding_id",
                [instrument],
            ).fetchall()
        events = [(row[5], 0, row) for row in orders] + [
            (row[0], 1, row) for row in funding
        ]
        for _, kind, row in sorted(events, key=lambda item: (item[0], item[1])):
            if kind == 0:
                side, quantity, price, fee, leverage, timestamp_ns = row
                apply_position_fill(
                    state,
                    OrderSide(side),
                    quantity,
                    price,
                    fee,
                    timestamp_ns,
                    leverage,
                )
            else:
                timestamp_ns, funding_pnl = row
                state.funding_pnl += funding_pnl
                state.cash_balance += funding_pnl
                state.updated_at_ns = max(state.updated_at_ns, timestamp_ns)
        return state


def apply_position_fill(
    state: PositionState,
    side,
    quantity: float,
    price: float,
    fee: float,
    timestamp_ns: int,
    leverage: float,
) -> float:
    from .paper_types import OrderSide

    delta = quantity if side is OrderSide.BUY else -quantity
    old_quantity = state.quantity
    realized = 0.0
    if abs(old_quantity) <= 1e-12:
        state.quantity = delta
        state.average_entry = price
        state.leverage = leverage
    elif old_quantity * delta > 0:
        total = abs(old_quantity) + abs(delta)
        state.average_entry = (
            abs(old_quantity) * state.average_entry + abs(delta) * price
        ) / total
        state.quantity = old_quantity + delta
        state.leverage = min(state.leverage, leverage)
    else:
        close_quantity = min(abs(old_quantity), abs(delta))
        realized = (
            close_quantity
            * (price - state.average_entry)
            * (1.0 if old_quantity > 0 else -1.0)
        )
        new_quantity = old_quantity + delta
        if abs(new_quantity) <= 1e-12:
            state.quantity = 0.0
            state.average_entry = 0.0
            state.leverage = 1.0
        elif old_quantity * new_quantity > 0:
            state.quantity = new_quantity
        else:
            state.quantity = new_quantity
            state.average_entry = price
            state.leverage = leverage
    state.realized_pnl_gross += realized
    state.fees_paid += fee
    state.cash_balance += realized - fee
    state.updated_at_ns = timestamp_ns
    return realized


from .paper_types import OrderSide  # noqa: E402
