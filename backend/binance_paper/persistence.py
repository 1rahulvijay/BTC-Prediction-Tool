"""Physically isolated, serialized DuckDB persistence for Binance paper state."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import threading
import time
from typing import Any
import uuid

import duckdb

from .schemas import (
    FillResult,
    MarketSnapshot,
    StrategyDecision,
    validate_order_transition,
)


SCHEMA_VERSION = 5
STRATEGY_IDS = ("trend_following", "breakout")

#: How close an observed mark has to be to a funding settlement for the notional it
#: prices to be the notional the exchange actually charged. Funding settles every 8h and
#: is discovered from a "last settled" REST field, so the observation is normally hours
#: later - `OBSERVATION_TIME_MARK_ESTIMATED` is the expected label, not the exception.
MARK_AT_FUNDING_TOLERANCE_MS = 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rows(cursor) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class BinancePaperPersistence:
    """One process-lifetime connection and one serialized writer path."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = duckdb.connect(str(self.path))
        self._initialize_schema()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN TRANSACTION")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @contextmanager
    def transaction_or_connection(self, connection=None):
        """Join an existing atomic execution transaction or create a new one."""
        if connection is not None:
            yield connection
            return
        with self.transaction() as conn:
            yield conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _initialize_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS binance_paper_schema_version (
                version INTEGER PRIMARY KEY,
                applied_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_accounts (
                strategy_id VARCHAR PRIMARY KEY,
                starting_cash_usd DOUBLE NOT NULL,
                available_cash_usd DOUBLE NOT NULL,
                used_margin_usd DOUBLE NOT NULL,
                equity_usd DOUBLE NOT NULL,
                realized_pnl_usd DOUBLE NOT NULL,
                unrealized_pnl_usd DOUBLE NOT NULL,
                trading_fees_usd DOUBLE NOT NULL,
                funding_usd DOUBLE NOT NULL,
                peak_equity_usd DOUBLE NOT NULL,
                maximum_drawdown_usd DOUBLE NOT NULL,
                closed_trade_count BIGINT NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_strategy_configs (
                strategy_id VARCHAR PRIMARY KEY,
                strategy_name VARCHAR NOT NULL,
                strategy_version VARCHAR NOT NULL,
                enabled BOOLEAN NOT NULL,
                config_json VARCHAR NOT NULL,
                config_hash VARCHAR NOT NULL,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_signals (
                signal_id VARCHAR PRIMARY KEY,
                strategy_id VARCHAR NOT NULL,
                strategy_version VARCHAR NOT NULL,
                strategy_config_hash VARCHAR NOT NULL,
                feature_schema_hash VARCHAR NOT NULL,
                feature_values_hash VARCHAR NOT NULL,
                decision_ts_ms BIGINT NOT NULL,
                symbol VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                side VARCHAR,
                score DOUBLE NOT NULL,
                confidence DOUBLE NOT NULL,
                requested_notional_usd DOUBLE NOT NULL,
                stop_price DOUBLE,
                take_profit_price DOUBLE,
                maximum_holding_seconds BIGINT NOT NULL,
                valid_until_ms BIGINT,
                maximum_entry_price DOUBLE,
                minimum_entry_price DOUBLE,
                probability_calibrated BOOLEAN NOT NULL DEFAULT FALSE,
                uncertainty_status VARCHAR NOT NULL DEFAULT 'UNMEASURED',
                expected_net_pnl_usd DOUBLE,
                expected_net_pnl_heuristic_haircut_usd DOUBLE,
                feature_snapshot_json VARCHAR NOT NULL,
                required_inputs_json VARCHAR NOT NULL,
                available_inputs_json VARCHAR NOT NULL,
                missing_inputs_json VARCHAR NOT NULL,
                data_quality_status VARCHAR NOT NULL,
                reason_codes_json VARCHAR NOT NULL,
                created_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_orders (
                order_event_id VARCHAR PRIMARY KEY,
                order_id VARCHAR NOT NULL,
                signal_id VARCHAR NOT NULL,
                strategy_id VARCHAR NOT NULL,
                operation VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                requested_quantity DOUBLE NOT NULL,
                requested_notional_usd DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                decision_ts_ms BIGINT NOT NULL,
                simulated_send_ts_ms BIGINT NOT NULL,
                simulated_arrival_ts_ms BIGINT NOT NULL,
                rejection_reason VARCHAR,
                created_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_fills (
                fill_id VARCHAR PRIMARY KEY,
                order_id VARCHAR NOT NULL,
                signal_id VARCHAR NOT NULL,
                strategy_id VARCHAR NOT NULL,
                operation VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                requested_quantity DOUBLE NOT NULL,
                filled_quantity DOUBLE NOT NULL,
                unfilled_quantity DOUBLE NOT NULL,
                decision_ts_ms BIGINT NOT NULL,
                simulated_send_ts_ms BIGINT NOT NULL,
                simulated_arrival_ts_ms BIGINT NOT NULL,
                market_ts_ms BIGINT NOT NULL,
                received_at_ms BIGINT NOT NULL,
                quote_age_ms BIGINT NOT NULL,
                executable_price_source VARCHAR NOT NULL,
                average_fill_price DOUBLE,
                spread_cost_usd DOUBLE NOT NULL,
                slippage_cost_usd DOUBLE NOT NULL,
                fee_usd DOUBLE NOT NULL,
                fee_rate_bps DOUBLE NOT NULL,
                latency_assumption_ms BIGINT NOT NULL,
                fill_quality_status VARCHAR NOT NULL,
                rejection_reason VARCHAR,
                created_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_funding_events (
                funding_event_id VARCHAR PRIMARY KEY,
                position_id VARCHAR NOT NULL,
                strategy_id VARCHAR NOT NULL,
                funding_time_ms BIGINT NOT NULL,
                observed_at_ms BIGINT NOT NULL,
                funding_rate DOUBLE NOT NULL,
                mark_price DOUBLE NOT NULL,
                notional_usd DOUBLE NOT NULL,
                funding_usd DOUBLE NOT NULL,
                source VARCHAR NOT NULL,
                created_at_ms BIGINT NOT NULL,
                -- WHICH MOMENT PRICED THIS CASHFLOW. The exchange charges funding on the
                -- notional at `funding_time_ms`; the only mark this engine holds is the one
                -- observed at `observed_at_ms`, and funding settles every 8h so the two can
                -- be hours apart. The charge is still applied - skipping a real cashflow
                -- would flatter paper P&L, which is the worse error - but the row now says
                -- so. The dollar effect is small (a mark error of x% moves a ~0.01% funding
                -- rate by x% OF that), and it is a provenance defect either way: one row
                -- must not silently describe two moments.
                mark_basis VARCHAR DEFAULT '',
                mark_lag_ms BIGINT DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_positions (
                position_id VARCHAR PRIMARY KEY,
                strategy_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity DOUBLE NOT NULL,
                entry_price DOUBLE NOT NULL,
                entry_notional_usd DOUBLE NOT NULL,
                leverage DOUBLE NOT NULL,
                margin_usd DOUBLE NOT NULL,
                entry_fee_usd DOUBLE NOT NULL,
                stop_price DOUBLE NOT NULL,
                take_profit_price DOUBLE NOT NULL,
                maximum_holding_seconds BIGINT NOT NULL,
                entry_signal_id VARCHAR NOT NULL,
                entry_order_id VARCHAR NOT NULL,
                entry_fill_id VARCHAR NOT NULL,
                opened_at_ms BIGINT NOT NULL,
                last_mark_price DOUBLE NOT NULL,
                unrealized_pnl_usd DOUBLE NOT NULL,
                status VARCHAR NOT NULL,
                closed_at_ms BIGINT,
                updated_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_trades (
                trade_id VARCHAR PRIMARY KEY,
                position_id VARCHAR NOT NULL,
                strategy_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                quantity DOUBLE NOT NULL,
                entry_price DOUBLE NOT NULL,
                exit_price DOUBLE NOT NULL,
                gross_pnl_usd DOUBLE NOT NULL,
                entry_fee_usd DOUBLE NOT NULL,
                exit_fee_usd DOUBLE NOT NULL,
                funding_usd DOUBLE NOT NULL,
                net_pnl_usd DOUBLE NOT NULL,
                slippage_usd DOUBLE NOT NULL,
                entry_time_ms BIGINT NOT NULL,
                exit_time_ms BIGINT NOT NULL,
                holding_seconds DOUBLE NOT NULL,
                exit_reason VARCHAR NOT NULL,
                strategy_version VARCHAR NOT NULL,
                created_at_ms BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_equity_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                strategy_id VARCHAR NOT NULL,
                timestamp_ms BIGINT NOT NULL,
                equity_usd DOUBLE NOT NULL,
                available_cash_usd DOUBLE NOT NULL,
                used_margin_usd DOUBLE NOT NULL,
                realized_pnl_usd DOUBLE NOT NULL,
                unrealized_pnl_usd DOUBLE NOT NULL,
                gross_exposure_usd DOUBLE NOT NULL,
                long_exposure_usd DOUBLE NOT NULL,
                short_exposure_usd DOUBLE NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS binance_paper_events (
                event_id VARCHAR PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                strategy_id VARCHAR,
                severity VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                details_json VARCHAR NOT NULL,
                created_at_ms BIGINT NOT NULL
            )
            """,
        ]
        with self.transaction() as conn:
            for statement in statements:
                conn.execute(statement)
            signal_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info('binance_paper_signals')"
                ).fetchall()
            }
            # SCHEMA v4: `expected_net_pnl_lower_bound_usd` never held a lower bound. It held
            # a fixed 0.05 probability haircut, and the name asserted a statistical property
            # the arithmetic does not have. Renamed - and the existing rows are CARRIED ACROSS
            # rather than stranded in an orphan column, because they are real observations of
            # what the strategy computed, just under an honest name.
            if "expected_net_pnl_lower_bound_usd" in signal_columns:
                if "expected_net_pnl_heuristic_haircut_usd" not in signal_columns:
                    conn.execute(
                        "ALTER TABLE binance_paper_signals RENAME COLUMN "
                        "expected_net_pnl_lower_bound_usd TO "
                        "expected_net_pnl_heuristic_haircut_usd"
                    )
                else:
                    conn.execute(
                        "UPDATE binance_paper_signals "
                        "SET expected_net_pnl_heuristic_haircut_usd = "
                        "COALESCE(expected_net_pnl_heuristic_haircut_usd, "
                        "expected_net_pnl_lower_bound_usd)"
                    )
                    conn.execute("ALTER TABLE binance_paper_signals "
                                 "DROP COLUMN expected_net_pnl_lower_bound_usd")

            # Re-read: the rename above changes what the additive loop must do.
            signal_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info('binance_paper_signals')"
                ).fetchall()
            }

            signal_migrations = {
                "valid_until_ms": "BIGINT",
                "maximum_entry_price": "DOUBLE",
                "minimum_entry_price": "DOUBLE",
                "probability_calibrated": "BOOLEAN DEFAULT FALSE",
                "uncertainty_status": "VARCHAR DEFAULT 'UNMEASURED'",
                "expected_net_pnl_usd": "DOUBLE",
                "expected_net_pnl_heuristic_haircut_usd": "DOUBLE",
            }
            for name, definition in signal_migrations.items():
                if name not in signal_columns:
                    conn.execute(
                        f"ALTER TABLE binance_paper_signals "
                        f"ADD COLUMN {name} {definition}"
                    )

            # SCHEMA v5: which moment priced each funding cashflow. Additive, and it must
            # run on existing databases - the insert below is positional, so a table
            # created before these columns would reject every funding event and the
            # engine would silently stop charging funding at all.
            funding_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info('binance_paper_funding_events')"
                ).fetchall()
            }
            for name, definition in {
                "mark_basis": "VARCHAR DEFAULT ''",
                "mark_lag_ms": "BIGINT DEFAULT 0",
            }.items():
                if name not in funding_columns:
                    conn.execute(
                        f"ALTER TABLE binance_paper_funding_events "
                        f"ADD COLUMN {name} {definition}"
                    )

            versions = conn.execute(
                "SELECT version FROM binance_paper_schema_version ORDER BY version"
            ).fetchall()
            if versions and versions[-1][0] > SCHEMA_VERSION:
                raise RuntimeError("Binance paper database schema is newer than this code")
            if not versions or versions[-1][0] < SCHEMA_VERSION:
                conn.execute(
                    "INSERT INTO binance_paper_schema_version VALUES (?, ?)",
                    (SCHEMA_VERSION, _now_ms()),
                )

    def ensure_strategy(
        self,
        strategy_id: str,
        strategy_name: str,
        strategy_version: str,
        enabled: bool,
        config_json: str,
        config_hash: str,
        starting_cash_usd: float,
    ) -> None:
        now = _now_ms()
        with self.transaction() as conn:
            account = conn.execute(
                "SELECT strategy_id FROM binance_paper_accounts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if account is None:
                conn.execute(
                    """
                    INSERT INTO binance_paper_accounts VALUES
                    (?, ?, ?, 0, ?, 0, 0, 0, 0, ?, 0, 0, ?)
                    """,
                    (
                        strategy_id,
                        starting_cash_usd,
                        starting_cash_usd,
                        starting_cash_usd,
                        starting_cash_usd,
                        now,
                    ),
                )
            config = conn.execute(
                """
                SELECT strategy_id FROM binance_paper_strategy_configs
                WHERE strategy_id = ?
                """,
                (strategy_id,),
            ).fetchone()
            if config is None:
                conn.execute(
                    """
                    INSERT INTO binance_paper_strategy_configs
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id,
                        strategy_name,
                        strategy_version,
                        enabled,
                        config_json,
                        config_hash,
                        now,
                    ),
                )

    def update_strategy_config(
        self, strategy_id: str, enabled: bool, config_json: str, config_hash: str
    ) -> None:
        with self.transaction() as conn:
            changed = conn.execute(
                """
                UPDATE binance_paper_strategy_configs
                SET enabled = ?, config_json = ?, config_hash = ?, updated_at_ms = ?
                WHERE strategy_id = ?
                RETURNING strategy_id
                """,
                (enabled, config_json, config_hash, _now_ms(), strategy_id),
            ).fetchone()
            if changed is None:
                raise KeyError(f"unknown strategy: {strategy_id}")

    def strategy_configs(self) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    "SELECT * FROM binance_paper_strategy_configs ORDER BY strategy_id"
                )
            )

    def accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    "SELECT * FROM binance_paper_accounts ORDER BY strategy_id"
                )
            )

    def account(self, strategy_id: str) -> dict[str, Any]:
        with self._lock:
            rows = _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_accounts WHERE strategy_id = ?
                    """,
                    (strategy_id,),
                )
            )
        if not rows:
            raise KeyError(f"missing account: {strategy_id}")
        return rows[0]

    def open_positions(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM binance_paper_positions WHERE status = 'OPEN'"
        params: tuple = ()
        if strategy_id is not None:
            sql += " AND strategy_id = ?"
            params = (strategy_id,)
        sql += " ORDER BY opened_at_ms"
        with self._lock:
            return _rows(self._conn.execute(sql, params))

    def position(self, position_id: str) -> dict[str, Any]:
        with self._lock:
            rows = _rows(
                self._conn.execute(
                    "SELECT * FROM binance_paper_positions WHERE position_id = ?",
                    (position_id,),
                )
            )
        if not rows:
            raise KeyError(f"unknown position: {position_id}")
        return rows[0]

    def record_signal(self, decision: StrategyDecision) -> bool:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM binance_paper_signals WHERE signal_id = ?",
                (decision.signal_id,),
            ).fetchone()
            if existing:
                return False
            value = decision.to_dict()
            conn.execute(
                """
                INSERT INTO binance_paper_signals (
                    signal_id, strategy_id, strategy_version, strategy_config_hash,
                    feature_schema_hash, feature_values_hash, decision_ts_ms,
                    symbol, timeframe, action, side, score, confidence,
                    requested_notional_usd, stop_price, take_profit_price,
                    maximum_holding_seconds, valid_until_ms, maximum_entry_price,
                    minimum_entry_price, probability_calibrated, uncertainty_status,
                    expected_net_pnl_usd, expected_net_pnl_heuristic_haircut_usd,
                    feature_snapshot_json, required_inputs_json,
                    available_inputs_json, missing_inputs_json, data_quality_status,
                    reason_codes_json, created_at_ms
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    decision.signal_id,
                    decision.strategy_id,
                    decision.strategy_version,
                    decision.strategy_config_hash,
                    decision.feature_schema_hash,
                    decision.feature_values_hash,
                    decision.timestamp_ms,
                    decision.symbol,
                    decision.timeframe,
                    value["action"],
                    value["side"],
                    decision.score,
                    decision.confidence,
                    decision.requested_notional_usd,
                    decision.stop_price,
                    decision.take_profit_price,
                    decision.maximum_holding_seconds,
                    decision.valid_until_ms,
                    decision.maximum_entry_price,
                    decision.minimum_entry_price,
                    decision.probability_calibrated,
                    decision.uncertainty_status,
                    decision.expected_net_pnl_usd,
                    decision.expected_net_pnl_heuristic_haircut_usd,
                    json.dumps(decision.features, sort_keys=True),
                    json.dumps(decision.required_inputs),
                    json.dumps(decision.available_inputs),
                    json.dumps(decision.missing_inputs),
                    value["data_quality_status"],
                    json.dumps(decision.reason_codes),
                    _now_ms(),
                ),
            )
        return True

    def append_order_event(
        self,
        *,
        order_id: str,
        signal_id: str,
        strategy_id: str,
        operation: str,
        side: str,
        requested_quantity: float,
        requested_notional_usd: float,
        status: str,
        decision_ts_ms: int,
        simulated_send_ts_ms: int,
        simulated_arrival_ts_ms: int,
        rejection_reason: str | None = None,
        connection=None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self.transaction_or_connection(connection) as conn:
            previous = conn.execute(
                """
                SELECT status, created_at_ms
                FROM binance_paper_orders
                WHERE order_id = ?
                ORDER BY created_at_ms DESC, order_event_id DESC
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
            validate_order_transition(previous[0] if previous else None, status)
            created_at_ms = max(
                _now_ms(),
                int(previous[1]) + 1 if previous else 0,
            )
            conn.execute(
                """
                INSERT INTO binance_paper_orders VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    order_id,
                    signal_id,
                    strategy_id,
                    operation,
                    side,
                    requested_quantity,
                    requested_notional_usd,
                    status,
                    decision_ts_ms,
                    simulated_send_ts_ms,
                    simulated_arrival_ts_ms,
                    rejection_reason,
                    created_at_ms,
                ),
            )
        return event_id

    def append_fill(self, fill: FillResult, *, connection=None) -> None:
        value = fill.to_dict()
        with self.transaction_or_connection(connection) as conn:
            conn.execute(
                """
                INSERT INTO binance_paper_fills VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.signal_id,
                    fill.strategy_id,
                    fill.operation,
                    value["side"],
                    fill.requested_quantity,
                    fill.filled_quantity,
                    fill.unfilled_quantity,
                    fill.decision_ts_ms,
                    fill.simulated_send_ts_ms,
                    fill.simulated_arrival_ts_ms,
                    fill.market_ts_ms,
                    fill.received_at_ms,
                    fill.quote_age_ms,
                    fill.executable_price_source,
                    fill.average_fill_price,
                    fill.spread_cost_usd,
                    fill.slippage_cost_usd,
                    fill.fee_usd,
                    fill.fee_rate_bps,
                    fill.latency_assumption_ms,
                    fill.fill_quality_status,
                    fill.rejection_reason,
                    _now_ms(),
                ),
            )

    def open_position(
        self,
        *,
        position_id: str,
        decision: StrategyDecision,
        fill: FillResult,
        leverage: float,
        connection=None,
    ) -> dict[str, Any]:
        if fill.average_fill_price is None or fill.filled_quantity <= 0:
            raise ValueError("cannot open a position without an executable fill")
        if decision.stop_price is None or decision.take_profit_price is None:
            raise ValueError("stop and take-profit are required")
        entry_notional = fill.average_fill_price * fill.filled_quantity
        margin = entry_notional / leverage
        now = fill.received_at_ms
        with self.transaction_or_connection(connection) as conn:
            existing = conn.execute(
                """
                SELECT position_id FROM binance_paper_positions
                WHERE strategy_id = ? AND status = 'OPEN'
                """,
                (decision.strategy_id,),
            ).fetchall()
            if existing:
                raise RuntimeError("strategy already has an open position")
            account = conn.execute(
                """
                SELECT available_cash_usd, used_margin_usd, equity_usd,
                       trading_fees_usd, peak_equity_usd
                FROM binance_paper_accounts WHERE strategy_id = ?
                """,
                (decision.strategy_id,),
            ).fetchone()
            if account is None:
                raise RuntimeError("strategy account is missing")
            available, used_margin, _equity, fees, peak = map(float, account)
            cash_required = margin + fill.fee_usd
            if available + 1e-9 < cash_required:
                raise RuntimeError("insufficient paper cash for margin and fee")
            available -= cash_required
            used_margin += margin
            equity = available + used_margin
            peak = max(peak, equity)
            conn.execute(
                """
                INSERT INTO binance_paper_positions (
                    position_id, strategy_id, symbol, side, quantity, entry_price,
                    entry_notional_usd, leverage, margin_usd, entry_fee_usd,
                    stop_price, take_profit_price, maximum_holding_seconds,
                    entry_signal_id, entry_order_id, entry_fill_id, opened_at_ms,
                    last_mark_price, unrealized_pnl_usd, status, closed_at_ms,
                    updated_at_ms
                ) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    decision.strategy_id,
                    decision.symbol,
                    decision.side.value,
                    fill.filled_quantity,
                    fill.average_fill_price,
                    entry_notional,
                    leverage,
                    margin,
                    fill.fee_usd,
                    decision.stop_price,
                    decision.take_profit_price,
                    decision.maximum_holding_seconds,
                    decision.signal_id,
                    fill.order_id,
                    fill.fill_id,
                    now,
                    fill.average_fill_price,
                    0.0,
                    "OPEN",
                    None,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE binance_paper_accounts
                SET available_cash_usd = ?, used_margin_usd = ?, equity_usd = ?,
                    unrealized_pnl_usd = 0, trading_fees_usd = ?,
                    peak_equity_usd = ?, updated_at_ms = ?
                WHERE strategy_id = ?
                """,
                (
                    available,
                    used_margin,
                    equity,
                    fees + fill.fee_usd,
                    peak,
                    now,
                    decision.strategy_id,
                ),
            )
        return self.open_positions(decision.strategy_id)[0]

    @staticmethod
    def _unrealized(position: dict[str, Any], mark: float) -> float:
        direction = 1.0 if position["side"] == "LONG" else -1.0
        return (
            direction
            * float(position["quantity"])
            * (float(mark) - float(position["entry_price"]))
        )

    def mark_positions(self, snapshot: MarketSnapshot) -> None:
        now = snapshot.received_at_ms
        with self.transaction() as conn:
            positions = _rows(
                conn.execute(
                    """
                    SELECT * FROM binance_paper_positions
                    WHERE status = 'OPEN' AND symbol = ?
                    """,
                    (snapshot.symbol,),
                )
            )
            for position in positions:
                unrealized = self._unrealized(position, snapshot.mark_price)
                conn.execute(
                    """
                    UPDATE binance_paper_positions
                    SET last_mark_price = ?, unrealized_pnl_usd = ?, updated_at_ms = ?
                    WHERE position_id = ? AND status = 'OPEN'
                    """,
                    (snapshot.mark_price, unrealized, now, position["position_id"]),
                )
                account = conn.execute(
                    """
                    SELECT available_cash_usd, used_margin_usd, realized_pnl_usd,
                           trading_fees_usd, funding_usd, peak_equity_usd,
                           maximum_drawdown_usd
                    FROM binance_paper_accounts WHERE strategy_id = ?
                    """,
                    (position["strategy_id"],),
                ).fetchone()
                if account is None:
                    raise RuntimeError("open position has no strategy account")
                available, margin, realized, fees, funding, peak, max_dd = map(
                    float, account
                )
                equity = available + margin + unrealized
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                conn.execute(
                    """
                    UPDATE binance_paper_accounts
                    SET equity_usd = ?, unrealized_pnl_usd = ?,
                        peak_equity_usd = ?, maximum_drawdown_usd = ?,
                        updated_at_ms = ?
                    WHERE strategy_id = ?
                    """,
                    (equity, unrealized, peak, max_dd, now, position["strategy_id"]),
                )

    def apply_observed_funding(
        self, snapshot: MarketSnapshot
    ) -> list[dict[str, Any]]:
        if snapshot.funding_rate is None or snapshot.funding_time_ms is None:
            return []
        funding_rate = float(snapshot.funding_rate)
        funding_time_ms = int(snapshot.funding_time_ms)
        if (
            not math.isfinite(funding_rate)
            or funding_time_ms <= 0
            or funding_time_ms > snapshot.received_at_ms
        ):
            return []
        applied: list[dict[str, Any]] = []
        with self.transaction() as conn:
            positions = _rows(
                conn.execute(
                    """
                    SELECT * FROM binance_paper_positions
                    WHERE status = 'OPEN' AND symbol = ? AND opened_at_ms < ?
                    """,
                    (snapshot.symbol, funding_time_ms),
                )
            )
            for position in positions:
                event_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            "binance-paper-funding:"
                            f"{position['position_id']}:{funding_time_ms}"
                        ),
                    )
                )
                exists = conn.execute(
                    """
                    SELECT 1 FROM binance_paper_funding_events
                    WHERE funding_event_id = ?
                    """,
                    (event_id,),
                ).fetchone()
                if exists is not None:
                    continue
                direction = 1.0 if position["side"] == "LONG" else -1.0
                notional = float(position["quantity"]) * snapshot.mark_price
                funding_usd = -direction * notional * funding_rate
                mark_lag_ms = int(snapshot.received_at_ms) - funding_time_ms
                mark_basis = (
                    "FUNDING_TIME_MARK"
                    if 0 <= mark_lag_ms <= MARK_AT_FUNDING_TOLERANCE_MS
                    else "OBSERVATION_TIME_MARK_ESTIMATED"
                )
                account = conn.execute(
                    """
                    SELECT available_cash_usd, used_margin_usd,
                           unrealized_pnl_usd, funding_usd,
                           peak_equity_usd, maximum_drawdown_usd
                    FROM binance_paper_accounts WHERE strategy_id = ?
                    """,
                    (position["strategy_id"],),
                ).fetchone()
                if account is None:
                    raise RuntimeError("funded position has no strategy account")
                available, margin, unrealized, funding_total, peak, max_dd = map(
                    float, account
                )
                available += funding_usd
                equity = available + margin + unrealized
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)
                conn.execute(
                    """
                    INSERT INTO binance_paper_funding_events VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        position["position_id"],
                        position["strategy_id"],
                        funding_time_ms,
                        snapshot.received_at_ms,
                        funding_rate,
                        snapshot.mark_price,
                        notional,
                        funding_usd,
                        "binance_futures_public_rest_last_settled",
                        _now_ms(),
                        mark_basis,
                        mark_lag_ms,
                    ),
                )
                conn.execute(
                    """
                    UPDATE binance_paper_accounts
                    SET available_cash_usd = ?, equity_usd = ?, funding_usd = ?,
                        peak_equity_usd = ?, maximum_drawdown_usd = ?,
                        updated_at_ms = ?
                    WHERE strategy_id = ?
                    """,
                    (
                        available,
                        equity,
                        funding_total + funding_usd,
                        peak,
                        max_dd,
                        snapshot.received_at_ms,
                        position["strategy_id"],
                    ),
                )
                applied.append(
                    {
                        "funding_event_id": event_id,
                        "position_id": position["position_id"],
                        "strategy_id": position["strategy_id"],
                        "funding_time_ms": funding_time_ms,
                        "funding_rate": funding_rate,
                        "funding_usd": funding_usd,
                    }
                )
        return applied

    def close_position(
        self,
        *,
        position_id: str,
        fill: FillResult,
        exit_reason: str,
        strategy_version: str,
        connection=None,
    ) -> dict[str, Any]:
        if fill.average_fill_price is None or fill.filled_quantity <= 0:
            raise ValueError("cannot close without an executable fill")
        with self.transaction_or_connection(connection) as conn:
            rows = _rows(
                conn.execute(
                    """
                    SELECT * FROM binance_paper_positions
                    WHERE position_id = ? AND status = 'OPEN'
                    """,
                    (position_id,),
                )
            )
            if not rows:
                raise KeyError(f"open position not found: {position_id}")
            position = rows[0]
            if abs(fill.filled_quantity - float(position["quantity"])) > 1e-9:
                raise RuntimeError("Phase-1 exits must close the full position")
            direction = 1.0 if position["side"] == "LONG" else -1.0
            gross = (
                direction
                * float(position["quantity"])
                * (fill.average_fill_price - float(position["entry_price"]))
            )
            entry_fee = float(position["entry_fee_usd"])
            position_funding = float(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(funding_usd), 0)
                    FROM binance_paper_funding_events
                    WHERE position_id = ?
                    """,
                    (position_id,),
                ).fetchone()[0]
                or 0.0
            )
            net = gross - entry_fee - fill.fee_usd + position_funding
            account = conn.execute(
                """
                SELECT available_cash_usd, used_margin_usd, realized_pnl_usd,
                       trading_fees_usd, funding_usd, peak_equity_usd,
                       maximum_drawdown_usd, closed_trade_count
                FROM binance_paper_accounts WHERE strategy_id = ?
                """,
                (position["strategy_id"],),
            ).fetchone()
            if account is None:
                raise RuntimeError("position account is missing")
            (
                available,
                used_margin,
                realized,
                account_fees,
                funding,
                peak,
                max_dd,
                closed_count,
            ) = account
            available = float(available) + float(position["margin_usd"]) + gross - fill.fee_usd
            used_margin = float(used_margin) - float(position["margin_usd"])
            if used_margin < -0.01:
                raise RuntimeError("account used margin would become negative")
            used_margin = max(0.0, used_margin)
            realized = float(realized) + net
            account_fees = float(account_fees) + fill.fee_usd
            equity = available + used_margin
            peak = max(float(peak), equity)
            max_dd = max(float(max_dd), peak - equity)
            now = fill.received_at_ms
            trade_id = str(uuid.uuid4())
            conn.execute(
                """
                UPDATE binance_paper_positions
                SET last_mark_price = ?, unrealized_pnl_usd = 0,
                    status = 'CLOSED', closed_at_ms = ?, updated_at_ms = ?
                WHERE position_id = ?
                """,
                (fill.average_fill_price, now, now, position_id),
            )
            conn.execute(
                """
                INSERT INTO binance_paper_trades VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    position_id,
                    position["strategy_id"],
                    position["symbol"],
                    position["side"],
                    position["quantity"],
                    position["entry_price"],
                    fill.average_fill_price,
                    gross,
                    entry_fee,
                    fill.fee_usd,
                    position_funding,
                    net,
                    fill.slippage_cost_usd,
                    position["opened_at_ms"],
                    now,
                    max(0.0, (now - int(position["opened_at_ms"])) / 1000.0),
                    exit_reason,
                    strategy_version,
                    _now_ms(),
                ),
            )
            conn.execute(
                """
                UPDATE binance_paper_accounts
                SET available_cash_usd = ?, used_margin_usd = ?, equity_usd = ?,
                    realized_pnl_usd = ?, unrealized_pnl_usd = 0,
                    trading_fees_usd = ?, peak_equity_usd = ?,
                    maximum_drawdown_usd = ?, closed_trade_count = ?,
                    updated_at_ms = ?
                WHERE strategy_id = ?
                """,
                (
                    available,
                    used_margin,
                    equity,
                    realized,
                    account_fees,
                    peak,
                    max_dd,
                    int(closed_count) + 1,
                    now,
                    position["strategy_id"],
                ),
            )
        return self.trades(limit=1, strategy_id=position["strategy_id"])[0]

    def append_equity_snapshots(self, timestamp_ms: int) -> None:
        accounts = {row["strategy_id"]: row for row in self.accounts()}
        positions = {row["strategy_id"]: row for row in self.open_positions()}
        with self.transaction() as conn:
            for strategy_id, account in accounts.items():
                position = positions.get(strategy_id)
                notional = (
                    float(position["quantity"]) * float(position["last_mark_price"])
                    if position
                    else 0.0
                )
                long_exposure = notional if position and position["side"] == "LONG" else 0.0
                short_exposure = notional if position and position["side"] == "SHORT" else 0.0
                conn.execute(
                    """
                    INSERT INTO binance_paper_equity_snapshots VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        strategy_id,
                        int(timestamp_ms),
                        account["equity_usd"],
                        account["available_cash_usd"],
                        account["used_margin_usd"],
                        account["realized_pnl_usd"],
                        account["unrealized_pnl_usd"],
                        notional,
                        long_exposure,
                        short_exposure,
                    ),
                )

    def append_event(
        self,
        event_type: str,
        message: str,
        *,
        strategy_id: str | None = None,
        severity: str = "INFO",
        details: dict[str, Any] | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO binance_paper_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event_type,
                    strategy_id,
                    severity,
                    message,
                    json.dumps(details or {}, sort_keys=True),
                    _now_ms(),
                ),
            )
        return event_id

    def latest_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_orders
                    ORDER BY created_at_ms DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                )
            )

    def fills(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_fills
                    ORDER BY created_at_ms DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                )
            )

    def funding_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_funding_events
                    ORDER BY funding_time_ms DESC, created_at_ms DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                )
            )

    def trades(
        self, limit: int = 100, strategy_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM binance_paper_trades"
        params: list[Any] = []
        if strategy_id:
            sql += " WHERE strategy_id = ?"
            params.append(strategy_id)
        sql += " ORDER BY exit_time_ms DESC LIMIT ?"
        params.append(max(1, min(10_000, int(limit))))
        with self._lock:
            return _rows(self._conn.execute(sql, tuple(params)))

    def competition_trades_since(
        self, strategy_id: str, since_ms: int
    ) -> list[dict[str, Any]]:
        """Return the complete chronological race ledger without a UI row cap."""
        with self._lock:
            return _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_trades
                    WHERE strategy_id = ?
                      AND entry_time_ms >= ?
                      AND exit_time_ms >= ?
                    ORDER BY exit_time_ms ASC, trade_id ASC
                    """,
                    (str(strategy_id), int(since_ms), int(since_ms)),
                )
            )

    def equity_snapshots(
        self, limit: int = 1000, strategy_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM binance_paper_equity_snapshots"
        params: list[Any] = []
        if strategy_id:
            sql += " WHERE strategy_id = ?"
            params.append(strategy_id)
        sql += " ORDER BY timestamp_ms DESC LIMIT ?"
        params.append(max(1, min(20_000, int(limit))))
        with self._lock:
            return _rows(self._conn.execute(sql, tuple(params)))

    def observation_window_ms(
        self, strategy_id: str
    ) -> tuple[int | None, int | None]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT MIN(timestamp_ms), MAX(timestamp_ms)
                FROM binance_paper_equity_snapshots
                WHERE strategy_id = ?
                """,
                (strategy_id,),
            ).fetchone()
        if not row or row[0] is None or row[1] is None:
            return None, None
        return int(row[0]), int(row[1])

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(
                self._conn.execute(
                    """
                    SELECT * FROM binance_paper_events
                    ORDER BY created_at_ms DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                )
            )

    def recent_trade_count(self, strategy_id: str, since_ms: int) -> int:
        """ENTRIES opened in the window - the quantity `maximum_trades_per_hour` names.

        This counted CLOSED trades by `exit_time_ms`. Two errors in one query (scan-5 5.27):

          - a strategy could open several positions inside the hour and hit no limit at all,
            because none of them had closed yet;
          - a long-held trade was attributed to the hour it EXITED, throttling an hour in which
            no entry was made.

        Entries are now counted by `entry_time_ms`, and positions still OPEN are included -
        an entry is an entry whether or not it has finished.
        """
        with self._lock:
            closed = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM binance_paper_trades
                    WHERE strategy_id = ? AND entry_time_ms >= ?
                    """,
                    (strategy_id, int(since_ms)),
                ).fetchone()[0]
                or 0
            )
            still_open = int(
                self._conn.execute(
                    """
                    SELECT COUNT(*) FROM binance_paper_positions
                    WHERE strategy_id = ? AND opened_at_ms >= ? AND status = 'OPEN'
                    """,
                    (strategy_id, int(since_ms)),
                ).fetchone()[0]
                or 0
            )
            return int(closed + still_open
            )

    def net_pnl_since(self, strategy_id: str, since_ms: int) -> float:
        """Period P&L for the LOSS LIMITS, including what is still open (scan-5 5.28).

        This summed completed trades by `exit_time_ms` alone, so a position spanning midnight
        put its entire P&L on its exit day - absent from the day it was actually losing, and
        fully charged to a day it barely traded. Funding paid today likewise did not appear
        until the position eventually closed.

        A loss LIMIT asks "how far down am I over this period", and a large open loser is
        exactly the thing it must not miss. Realised trades entered OR exited in the window are
        counted once, and the unrealised P&L of positions opened in it is added.

        The full remedy is a timestamped realised-cashflow ledger (entry fee, funding, exit).
        This is the bounded version: it can no longer be evaded by simply not closing.
        """
        with self._lock:
            realised = self._conn.execute(
                """
                SELECT COALESCE(SUM(net_pnl_usd), 0)
                FROM binance_paper_trades
                WHERE strategy_id = ?
                  AND (exit_time_ms >= ? OR entry_time_ms >= ?)
                """,
                (strategy_id, int(since_ms), int(since_ms)),
            ).fetchone()[0]
            unrealised = self._conn.execute(
                """
                SELECT COALESCE(SUM(unrealized_pnl_usd), 0)
                FROM binance_paper_positions
                WHERE strategy_id = ? AND opened_at_ms >= ? AND status = 'OPEN'
                """,
                (strategy_id, int(since_ms)),
            ).fetchone()[0]
        return float(realised or 0.0) + float(unrealised or 0.0)

    def daily_net_pnl(self, strategy_id: str, day_start_ms: int) -> float:
        return self.net_pnl_since(strategy_id, day_start_ms)

    def last_exit_time_ms(self, strategy_id: str) -> int | None:
        with self._lock:
            value = self._conn.execute(
                """
                SELECT MAX(exit_time_ms) FROM binance_paper_trades
                WHERE strategy_id = ?
                """,
                (strategy_id,),
            ).fetchone()[0]
        return int(value) if value is not None else None

    def has_signal(self, signal_id: str) -> bool:
        with self._lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM binance_paper_signals WHERE signal_id = ?",
                    (signal_id,),
                ).fetchone()
                is not None
            )

    def cancel_orphan_pending_orders(self) -> int:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT o.order_id, o.signal_id, o.strategy_id, o.operation, o.side,
                       o.requested_quantity, o.requested_notional_usd,
                       o.decision_ts_ms, o.simulated_send_ts_ms,
                       o.simulated_arrival_ts_ms
                FROM binance_paper_orders o
                WHERE o.status = 'PENDING'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM binance_paper_orders terminal
                    WHERE terminal.order_id = o.order_id
                      AND terminal.status <> 'PENDING'
                  )
                """
            ).fetchall()
        for row in rows:
            self.append_order_event(
                order_id=row[0],
                signal_id=row[1],
                strategy_id=row[2],
                operation=row[3],
                side=row[4],
                requested_quantity=row[5],
                requested_notional_usd=row[6],
                status="CANCELLED_RECOVERY",
                decision_ts_ms=row[7],
                simulated_send_ts_ms=row[8],
                simulated_arrival_ts_ms=row[9],
                rejection_reason="process_restarted_before_latency_fill",
            )
        return len(rows)

    def reconcile_or_raise(self) -> None:
        accounts = {row["strategy_id"]: row for row in self.accounts()}
        positions = self.open_positions()
        seen: set[str] = set()
        margins: dict[str, float] = {}
        for position in positions:
            strategy_id = position["strategy_id"]
            if strategy_id in seen:
                raise RuntimeError(f"multiple open positions for {strategy_id}")
            seen.add(strategy_id)
            if strategy_id not in accounts:
                raise RuntimeError(f"open position has no account: {strategy_id}")
            numeric = (
                position["quantity"],
                position["entry_price"],
                position["margin_usd"],
                position["entry_fee_usd"],
            )
            if not all(math.isfinite(float(value)) and float(value) >= 0 for value in numeric):
                raise RuntimeError(f"invalid open position state: {position['position_id']}")
            margins[strategy_id] = float(position["margin_usd"])
        for strategy_id, account in accounts.items():
            numeric = (
                account["available_cash_usd"],
                account["used_margin_usd"],
                account["equity_usd"],
                account["realized_pnl_usd"],
                account["unrealized_pnl_usd"],
            )
            if not all(math.isfinite(float(value)) for value in numeric):
                raise RuntimeError(f"invalid account state: {strategy_id}")
            expected_margin = margins.get(strategy_id, 0.0)
            if abs(float(account["used_margin_usd"]) - expected_margin) > 0.01:
                raise RuntimeError(f"used margin mismatch for {strategy_id}")
            expected_equity = (
                float(account["available_cash_usd"])
                + float(account["used_margin_usd"])
                + float(account["unrealized_pnl_usd"])
            )
            if abs(float(account["equity_usd"]) - expected_equity) > 0.01:
                raise RuntimeError(f"equity mismatch for {strategy_id}")

    def table_names(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        return {str(row[0]) for row in rows}
