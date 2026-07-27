"""Restart-safe Binance linear-futures paper engine."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
from threading import RLock

from backend.quant_platform.risk_engine import (
    OrderIntent,
    RiskAction,
    RiskEngine,
    RiskLimits,
    RiskState,
)

from .execution import walk_book
from .store import BinancePaperStore, apply_position_fill
from .types import (
    BookSnapshot,
    ExecutionResult,
    FillStatus,
    OrderRequest,
    OrderSide,
    PositionState,
)


DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "binance_paper.duckdb"


class BinancePaperEngine:
    def __init__(
        self,
        instrument: str = "BTCUSDT",
        db_path: str | Path = DEFAULT_DB,
        starting_capital: float = 10_000.0,
        taker_fee_bps: float = 5.0,
        max_slippage_bps: float = 20.0,
        maintenance_margin_rate: float = 0.005,
        risk_limits: RiskLimits | None = None,
    ):
        numeric = (
            starting_capital,
            taker_fee_bps,
            max_slippage_bps,
            maintenance_margin_rate,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("engine configuration must be finite")
        if starting_capital <= 0 or taker_fee_bps < 0 or max_slippage_bps < 0:
            raise ValueError("invalid paper engine configuration")
        self.instrument = instrument
        self.starting_capital = starting_capital
        self.taker_fee_bps = taker_fee_bps
        self.max_slippage_bps = max_slippage_bps
        self.maintenance_margin_rate = maintenance_margin_rate
        self.store = BinancePaperStore(db_path, starting_capital)
        self.position = self.store.load_position(instrument, starting_capital)
        self.risk = RiskEngine(risk_limits)
        self.position_known = True
        self.kill_switch = True
        self.kill_reason = "startup_fail_closed"
        self._lock = RLock()
        self.reconcile()

    def set_paper_enabled(self, enabled: bool, reason: str) -> None:
        self.kill_switch = not enabled
        self.kill_reason = reason

    def reconcile(self, tolerance: float = 1e-8) -> tuple[bool, list[str]]:
        replayed = self.store.replay_position(self.instrument, self.starting_capital)
        reasons = []
        for name in (
            "quantity",
            "average_entry",
            "realized_pnl_gross",
            "fees_paid",
            "funding_pnl",
            "cash_balance",
        ):
            if abs(float(getattr(replayed, name)) - float(getattr(self.position, name))) > tolerance:
                reasons.append(f"position_mismatch:{name}")
        self.position_known = not reasons
        return self.position_known, reasons

    def _risk_state(
        self, request: OrderRequest, book: BookSnapshot, mark_price: float
    ) -> RiskState:
        age_ms = max(0.0, (request.decision_ts_ns - book.receive_ts_ns) / 1_000_000.0)
        return RiskState(
            kill_switch=self.kill_switch,
            position_known=self.position_known,
            model_available=request.model_available,
            feed_age_ms=age_ms,
            sequence_healthy=book.sequence_healthy,
            daily_pnl=0.0,
            weekly_pnl=0.0,
            open_notional=abs(self.position.quantity) * mark_price,
            correlated_exposure=abs(self.position.quantity) * mark_price,
        )

    def submit_market(
        self,
        request: OrderRequest,
        book: BookSnapshot,
        mark_price: float | None = None,
    ) -> ExecutionResult:
        with self._lock:
            existing = self.store.load_order(request.order_id)
            if existing is not None:
                existing_sha, result = existing
                if existing_sha != request.request_sha256:
                    raise ValueError("order_id collision with a different request")
                return result
            if request.instrument != self.instrument or book.instrument != self.instrument:
                return self._reject(request, book.receive_ts_ns, ("instrument_mismatch",))
            mark = float(mark_price or (book.bids[0].price + book.asks[0].price) / 2)
            if not math.isfinite(mark) or mark <= 0:
                return self._reject(request, book.receive_ts_ns, ("invalid_mark_price",))

            quantity = request.quantity
            reasons: list[str] = []
            reduce_only_capped = False
            if request.reduce_only:
                if abs(self.position.quantity) <= 1e-12:
                    reasons.append("reduce_only_without_position")
                elif self.position.quantity > 0 and request.side is not OrderSide.SELL:
                    reasons.append("reduce_only_wrong_side")
                elif self.position.quantity < 0 and request.side is not OrderSide.BUY:
                    reasons.append("reduce_only_wrong_side")
                capped_quantity = min(quantity, abs(self.position.quantity))
                reduce_only_capped = capped_quantity + 1e-12 < quantity
                quantity = capped_quantity
            if reasons or quantity <= 0:
                return self._reject(request, book.receive_ts_ns, tuple(reasons))

            best = min(level.price for level in book.asks) if request.side is OrderSide.BUY else max(
                level.price for level in book.bids
            )
            intent = OrderIntent(
                venue="BINANCE_PAPER",
                instrument=request.instrument,
                strategy_id=request.strategy_id,
                notional=quantity * best,
                leverage=request.leverage,
                reduce_only=request.reduce_only,
            )
            decision = self.risk.evaluate(intent, self._risk_state(request, book, mark))
            if decision.action is RiskAction.BLOCK:
                return self._reject(request, book.receive_ts_ns, decision.reasons)

            depth_fill = walk_book(
                book,
                request.side,
                quantity,
                max_slippage_bps=self.max_slippage_bps,
            )
            if depth_fill.filled_quantity <= 0 or depth_fill.average_price is None:
                return self._reject(request, book.receive_ts_ns, ("no_executable_depth",))
            fee = depth_fill.notional * self.taker_fee_bps / 10_000.0
            status = (
                FillStatus.FILLED
                if depth_fill.complete and not reduce_only_capped
                else FillStatus.PARTIAL
            )
            fill_reasons = []
            if not depth_fill.complete:
                fill_reasons.append("insufficient_depth")
            if reduce_only_capped:
                fill_reasons.append("reduce_only_capped_to_position")
            result = ExecutionResult(
                order_id=request.order_id,
                request_sha256=request.request_sha256,
                status=status,
                requested_quantity=request.quantity,
                filled_quantity=depth_fill.filled_quantity,
                average_price=depth_fill.average_price,
                filled_notional=depth_fill.notional,
                fee=fee,
                fill_ts_ns=max(request.decision_ts_ns, book.receive_ts_ns),
                reason_codes=tuple(fill_reasons),
            )
            next_position = PositionState(**asdict(self.position))
            apply_position_fill(
                next_position,
                request.side,
                result.filled_quantity,
                result.average_price,
                fee,
                result.fill_ts_ns,
            )
            self.store.commit_order(request, result, next_position)
            self.position = next_position
            return result

    def _reject(
        self, request: OrderRequest, fill_ts_ns: int, reasons: tuple[str, ...]
    ) -> ExecutionResult:
        result = ExecutionResult(
            order_id=request.order_id,
            request_sha256=request.request_sha256,
            status=FillStatus.REJECTED,
            requested_quantity=request.quantity,
            filled_quantity=0.0,
            average_price=None,
            filled_notional=0.0,
            fee=0.0,
            fill_ts_ns=max(request.decision_ts_ns, fill_ts_ns),
            reason_codes=reasons or ("rejected",),
        )
        self.store.commit_order(request, result, self.position)
        return result

    def apply_funding(
        self,
        funding_id: str,
        timestamp_ns: int,
        mark_price: float,
        funding_rate: float,
    ) -> float:
        if (
            not funding_id
            or timestamp_ns <= 0
            or not math.isfinite(mark_price)
            or mark_price <= 0
            or not math.isfinite(funding_rate)
        ):
            raise ValueError("invalid funding event")
        with self._lock:
            direction = 1.0 if self.position.quantity > 0 else -1.0
            payment = (
                -direction * abs(self.position.quantity) * mark_price * funding_rate
                if self.position.quantity
                else 0.0
            )
            next_position = PositionState(**asdict(self.position))
            next_position.funding_pnl += payment
            next_position.cash_balance += payment
            next_position.updated_at_ns = max(next_position.updated_at_ns, timestamp_ns)
            inserted = self.store.commit_funding(
                funding_id,
                next_position,
                timestamp_ns,
                mark_price,
                funding_rate,
                payment,
            )
            if inserted:
                self.position = next_position
                return payment
            return 0.0

    def account(
        self,
        mark_price: float,
        leverage: float = 1.0,
        timestamp_ns: int | None = None,
        persist: bool = True,
    ) -> dict:
        if (
            not math.isfinite(mark_price)
            or mark_price <= 0
            or not math.isfinite(leverage)
            or leverage <= 0
        ):
            raise ValueError("mark_price and leverage must be finite and positive")
        quantity = self.position.quantity
        unrealized = (
            quantity * (mark_price - self.position.average_entry)
            if quantity
            else 0.0
        )
        notional = abs(quantity) * mark_price
        initial_margin = notional / leverage
        equity = self.position.cash_balance + unrealized
        available = equity - initial_margin
        liquidation_price = None
        if quantity > 0:
            liquidation_price = self.position.average_entry * (
                1.0 - 1.0 / leverage + self.maintenance_margin_rate
            )
        elif quantity < 0:
            liquidation_price = self.position.average_entry * (
                1.0 + 1.0 / leverage - self.maintenance_margin_rate
            )
        if liquidation_price is not None:
            liquidation_price = max(0.0, liquidation_price)
        values = {
            "timestamp_ns": timestamp_ns or max(1, self.position.updated_at_ns),
            "instrument": self.instrument,
            "mark_price": mark_price,
            "position_side": self.position.side,
            "position_quantity": quantity,
            "average_entry": self.position.average_entry,
            "realized_pnl_gross": self.position.realized_pnl_gross,
            "fees_paid": self.position.fees_paid,
            "funding_pnl": self.position.funding_pnl,
            "unrealized_pnl": unrealized,
            "equity": equity,
            "initial_margin": initial_margin,
            "available_balance": available,
            "liquidation_price": liquidation_price,
            "position_known": self.position_known,
            "paper_enabled": not self.kill_switch,
            "kill_reason": self.kill_reason,
        }
        if persist:
            self.store.append_equity(values)
        return values
