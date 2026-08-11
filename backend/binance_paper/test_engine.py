"""Executable accounting and persistence tests for Binance paper trading."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from backend.quant_platform.risk_engine import RiskLimits

from .engine import BinancePaperEngine
from .paper_types import BookLevel, BookSnapshot, FillStatus, OrderRequest, OrderSide


def _book(
    timestamp_ns: int = 2_000_000_000,
    sequence_healthy: bool = True,
    ask_quantity: float = 1.0,
) -> BookSnapshot:
    return BookSnapshot(
        instrument="BTCUSDT",
        exchange_ts_ns=timestamp_ns - 1_000_000,
        receive_ts_ns=timestamp_ns,
        sequence_id="1",
        source_id="binance-depth",
        bids=(BookLevel(99.0, 1.0), BookLevel(98.0, 2.0)),
        asks=(BookLevel(100.0, ask_quantity), BookLevel(101.0, 2.0)),
        sequence_healthy=sequence_healthy,
    )


def _order(
    order_id: str,
    side: OrderSide,
    quantity: float,
    timestamp_ns: int = 2_000_000_000,
    reduce_only: bool = False,
    leverage: float = 1.0,
) -> OrderRequest:
    return OrderRequest(
        order_id=order_id,
        decision_ts_ns=timestamp_ns,
        instrument="BTCUSDT",
        strategy_id="test-strategy",
        side=side,
        quantity=quantity,
        leverage=leverage,
        reduce_only=reduce_only,
    )


def main() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "binance_paper.duckdb"
        limits = RiskLimits(max_notional=100_000.0, max_correlated_exposure=100_000.0)
        engine = BinancePaperEngine(
            db_path=path,
            starting_capital=10_000.0,
            taker_fee_bps=5.0,
            max_slippage_bps=200.0,
            risk_limits=limits,
        )
        assert engine.kill_switch
        rejected = engine.submit_market(_order("blocked", OrderSide.BUY, 1.0), _book())
        assert rejected.status is FillStatus.REJECTED
        assert rejected.reason_codes == ("kill_switch",)

        engine.set_paper_enabled(True, "test")
        buy = engine.submit_market(_order("buy", OrderSide.BUY, 2.0), _book())
        assert buy.status is FillStatus.FILLED
        assert abs(buy.average_price - 100.5) < 1e-12
        assert abs(engine.position.quantity - 2.0) < 1e-12
        assert abs(engine.position.average_entry - 100.5) < 1e-12
        assert abs(buy.fee - 0.1005) < 1e-12

        close_book = BookSnapshot(
            instrument="BTCUSDT",
            exchange_ts_ns=2_999_000_000,
            receive_ts_ns=3_000_000_000,
            sequence_id="2",
            source_id="binance-depth",
            bids=(BookLevel(102.0, 2.0),),
            asks=(BookLevel(103.0, 2.0),),
        )
        close = engine.submit_market(
            _order("close", OrderSide.SELL, 2.0, 3_000_000_000, True),
            close_book,
        )
        assert close.status is FillStatus.FILLED
        assert abs(close.realized_pnl_gross - 3.0) < 1e-12
        assert engine.position.side == "FLAT"
        assert abs(engine.position.realized_pnl_gross - 3.0) < 1e-12
        expected_cash = 10_000.0 + 3.0 - buy.fee - close.fee
        assert abs(engine.position.cash_balance - expected_cash) < 1e-12

        partial = engine.submit_market(
            _order("partial", OrderSide.BUY, 4.0, 4_000_000_000),
            _book(4_000_000_000, ask_quantity=0.5),
        )
        assert partial.status is FillStatus.PARTIAL
        assert abs(partial.filled_quantity - 2.5) < 1e-12
        assert partial.reason_codes == ("insufficient_depth",)

        funding = engine.apply_funding("funding-1", 5_000_000_000, 100.0, 0.001)
        assert abs(funding + 0.25) < 1e-12
        assert engine.apply_funding("funding-1", 5_000_000_000, 100.0, 0.001) == 0.0
        try:
            engine.apply_funding("funding-1", 5_000_000_000, 100.0, 0.002)
            raise AssertionError("funding_id collision was accepted")
        except ValueError:
            pass

        duplicate = engine.submit_market(
            _order("partial", OrderSide.BUY, 4.0, 4_000_000_000),
            _book(4_000_000_000, ask_quantity=0.5),
        )
        assert duplicate == partial

        stale = engine.submit_market(
            _order("stale", OrderSide.BUY, 0.1, 10_000_000_000),
            _book(4_000_000_000),
        )
        assert stale.status is FillStatus.REJECTED
        assert "stale_feed" in stale.reason_codes

        restarted = BinancePaperEngine(
            db_path=path,
            starting_capital=10_000.0,
            taker_fee_bps=5.0,
            max_slippage_bps=200.0,
            risk_limits=limits,
        )
        assert restarted.position_known
        assert abs(restarted.position.quantity - engine.position.quantity) < 1e-12
        assert abs(restarted.position.cash_balance - engine.position.cash_balance) < 1e-12
        assert restarted.position.leverage == engine.position.leverage

        leveraged_path = Path(tmp) / "leveraged.duckdb"
        leveraged = BinancePaperEngine(
            db_path=leveraged_path,
            starting_capital=10_000.0,
            max_slippage_bps=200.0,
            risk_limits=limits,
        )
        leveraged.set_paper_enabled(True, "test")
        leveraged.submit_market(
            _order("leveraged-buy", OrderSide.BUY, 1.0, leverage=2.0),
            _book(),
        )
        account = leveraged.account(100.0, persist=False)
        assert account["leverage"] == 2.0
        assert abs(account["initial_margin"] - 50.0) < 1e-12
        leveraged_restart = BinancePaperEngine(
            db_path=leveraged_path,
            starting_capital=10_000.0,
            max_slippage_bps=200.0,
            risk_limits=limits,
        )
        assert leveraged_restart.position.leverage == 2.0

        loss_path = Path(tmp) / "loss-limit.duckdb"
        loss_limits = RiskLimits(
            max_notional=100_000.0,
            max_correlated_exposure=100_000.0,
            max_daily_loss=0.5,
            max_weekly_loss=100.0,
        )
        loss_engine = BinancePaperEngine(
            db_path=loss_path,
            starting_capital=10_000.0,
            max_slippage_bps=200.0,
            risk_limits=loss_limits,
        )
        loss_engine.set_paper_enabled(True, "test")
        loss_engine.submit_market(
            _order("loss-open", OrderSide.BUY, 0.1),
            _book(),
        )
        loss_book = BookSnapshot(
            instrument="BTCUSDT",
            exchange_ts_ns=2_999_000_000,
            receive_ts_ns=3_000_000_000,
            sequence_id="2",
            source_id="binance-depth",
            bids=(BookLevel(90.0, 1.0),),
            asks=(BookLevel(91.0, 1.0),),
        )
        loss_engine.submit_market(
            _order("loss-close", OrderSide.SELL, 0.1, 3_000_000_000, True),
            loss_book,
        )
        loss_blocked = loss_engine.submit_market(
            _order("loss-blocked", OrderSide.BUY, 0.1, 4_000_000_000),
            _book(4_000_000_000),
        )
        assert loss_blocked.status is FillStatus.REJECTED
        assert "daily_loss_limit" in loss_blocked.reason_codes

        with duckdb.connect(str(path)) as con:
            con.execute(
                "UPDATE paper_positions SET quantity = quantity + 1 "
                "WHERE instrument = 'BTCUSDT'"
            )
        corrupted = BinancePaperEngine(
            db_path=path,
            starting_capital=10_000.0,
            risk_limits=limits,
        )
        assert not corrupted.position_known
        corrupted.set_paper_enabled(True, "test")
        unknown = corrupted.submit_market(
            _order("unknown", OrderSide.BUY, 0.1, 11_000_000_000),
            _book(11_000_000_000),
        )
        assert "unknown_position" in unknown.reason_codes

        tables = set(
            duckdb.connect(str(path))
            .execute("SHOW TABLES")
            .fetchdf()["name"]
            .tolist()
        )
        assert tables == {
            "paper_equity_snapshots",
            "paper_funding",
            "paper_meta",
            "paper_orders",
            "paper_positions",
        }

    print("binance paper engine: ALL PASS")


if __name__ == "__main__":
    main()
