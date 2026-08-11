"""Orchestration for the isolated, default-off Binance paper engine."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, replace
import math
import threading
import time
from typing import Any

from .config import EngineConfig
import logging

from .post_fill_geometry import geometry as _post_fill_geometry
from .fill_simulator import BinancePaperFillSimulator

_LOG = logging.getLogger(__name__)
from .governor import (
    CapitalPreservationGovernor,
    GovernorAccountState,
)
from .market_adapter import BinancePaperMarketAdapter
from .metrics import all_metrics, strategy_metrics
from .persistence import BinancePaperPersistence
from .portfolio import BinancePaperPortfolio
from .risk_engine import BinancePaperRiskEngine
from .schemas import Action, DataQuality, MarketSnapshot, PositionSide, StrategyDecision
from .strategy_base import canonical_hash
from .strategy_registry import StrategyRegistry


@dataclass
class PendingIntent:
    order_id: str
    signal_id: str
    strategy_id: str
    side: PositionSide
    operation: str
    quantity: float
    requested_notional_usd: float
    decision_ts_ms: int
    arrival_ts_ms: int
    decision: StrategyDecision | None = None
    position_id: str | None = None
    exit_reason: str | None = None
    reversal_decision: StrategyDecision | None = None


class _MaintenanceJournal:
    """Bounded, counted, non-blocking sink for writes with NO decision consequence.

    `_emit` in data_ingestion calls every book callback SYNCHRONOUSLY inside the websocket read
    loop, so any DuckDB write in `on_book` can delay consumption of the very feed whose freshness
    this engine depends on (scan-4 item 4.16).

    THE SPLIT, AND WHY IT IS NOT "MOVE ALL DB WORK".

    The audit's remedy was to enqueue an immutable event and let a worker own evaluation and all
    DuckDB writes. Most of `on_book`'s work CANNOT move:

        _process_pending      the code deliberately keeps fill latency faithful to the live
                              quote stream; deferring it would trade a correctness property
                              of a paper engine for throughput
        portfolio.mark        the exit triggers below read the marked position
        apply_funding         changes realised P&L, which the governor then reads
        _governor_decision    decides can_open / must_flatten
        _queue_*_exits        risk exits must be queued promptly, not on a worker's schedule

    What is left is genuinely deferrable: the AUDIT LOG and the EQUITY TELEMETRY. Nothing reads
    them back to make a decision, and they are the writes that grow without bound.

    Drops are COUNTED, never silent - the feed_writer precedent in this repository. A journal
    that quietly discards is worse than one that blocks, because the gap is invisible.
    """

    def __init__(self, maxlen: int = 4096):
        self._q: deque = deque(maxlen=maxlen)
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self.dropped = 0
        self.written = 0
        self.errors = 0

    def start(self) -> "_MaintenanceJournal":
        with self._cv:
            if self._thread is not None:
                return self
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run, name="paper-maintenance-journal", daemon=True)
            self._thread.start()
        return self

    def submit(self, fn) -> None:
        """Never blocks the caller. A full queue drops the OLDEST and counts it."""
        with self._cv:
            if self._stopping:
                self.dropped += 1
                return
            if len(self._q) == self._q.maxlen:
                self.dropped += 1          # deque discards the oldest on append
            self._q.append(fn)
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._q and not self._stopping:
                    self._cv.wait(timeout=1.0)
                if self._stopping and not self._q:
                    return
                fn = self._q.popleft() if self._q else None
            if fn is None:
                continue
            try:
                fn()
                self.written += 1
            except Exception as exc:
                self.errors += 1
                _LOG.warning("[PAPER] maintenance journal write failed: %s", exc)

    def stop(self, timeout: float = 5.0) -> dict:
        """Drain and join, so shutdown does not abandon queued audit rows."""
        with self._cv:
            self._stopping = True
            self._cv.notify_all()
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)
        with self._cv:
            abandoned = len(self._q)
            self._q.clear()
        return {"written": self.written, "dropped": self.dropped,
                "errors": self.errors, "abandoned_at_shutdown": abandoned}


class BinancePaperService:
    """Public market observer plus explicitly started paper-strategy service."""

    def __init__(
        self,
        futures_client,
        derivatives_provider=None,
        model_context_provider=None,
        *,
        config: EngineConfig | None = None,
        persistence: BinancePaperPersistence | None = None,
    ):
        self.config = config or EngineConfig.from_env()
        self.adapter = BinancePaperMarketAdapter(
            futures_client,
            derivatives_provider,
            self.config,
            model_context_provider=model_context_provider,
        )
        self.registry = StrategyRegistry()
        self.persistence = persistence
        self.portfolio = BinancePaperPortfolio(persistence) if persistence else None
        self.risk_engine = BinancePaperRiskEngine(self.config)
        self.governor = CapitalPreservationGovernor(
            latency_ms=self.config.latency_ms,
            quote_stale_ms=self.config.quote_stale_ms,
        )
        self.fill_simulator = BinancePaperFillSimulator(self.config)
        self.runtime_active = False
        self.initialized = False
        self.running = False
        self.latest_snapshot: MarketSnapshot | None = None
        self.latest_decisions: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, PendingIntent] = {}
        #: Positions whose entry economics died between decision and fill. Drained
        #: after the commit, because the position must exist before it can be unwound.
        self._post_fill_invalidated: list[tuple[str, str]] = []
        self._last_post_fill_geometry: dict | None = None
        self._lock = threading.RLock()
        self._last_evaluation_ms = 0
        self._last_portfolio_update_ms = 0
        self._last_funding_time_ms: int | None = None
        self._last_equity_snapshot_ms = 0
        self._integrity_error: str | None = None
        #: 4.16. Audit/telemetry writes leave the websocket callback thread. Only
        #: writes with NO decision consequence go here - see _MaintenanceJournal.
        self.journal = _MaintenanceJournal()

    def initialize(self) -> None:
        with self._lock:
            if self.initialized:
                return
            if self.persistence is None:
                self.persistence = BinancePaperPersistence(self.config.db_path)
                self.portfolio = BinancePaperPortfolio(self.persistence)
            self.registry.persist_defaults(
                self.persistence, self.config.starting_cash_usd
            )
            self.registry.load(self.persistence)
            self.persistence.reconcile_or_raise()
            self._integrity_error = None
            cancelled = self.persistence.cancel_orphan_pending_orders()
            self.persistence.append_event(
                "RECOVERY",
                "Binance paper state recovered",
                details={
                    "open_positions": len(self.persistence.open_positions()),
                    "cancelled_pending_orders": cancelled,
                    "hard_enabled": self.config.hard_enabled,
                },
            )
            self.journal.start()
            self.initialized = True

    def shutdown(self) -> None:
        with self._lock:
            self.running = False
            self.runtime_active = False
            # Drain BEFORE closing the connection: queued audit rows are written through the
            # same persistence object, and abandoning them is the gap this journal exists to
            # make visible rather than create.
            drain = self.journal.stop()
            if drain.get("dropped") or drain.get("abandoned_at_shutdown") or drain.get("errors"):
                _LOG.warning("[PAPER] maintenance journal drain: %s", drain)
            if self.persistence is not None:
                self.persistence.append_event(
                    "SHUTDOWN", "Binance paper service stopped", details=drain)
                self.persistence.close()
            self.initialized = False

    async def run(self) -> None:
        self.running = True
        while self.running:
            try:
                with self._lock:
                    if self.config.hard_enabled and self.runtime_active:
                        snapshot = self.adapter.snapshot()
                        if snapshot is not None:
                            self.latest_snapshot = snapshot
                            self._evaluate(snapshot)
            except Exception as exc:
                self._integrity_error = f"{type(exc).__name__}: {exc}"
                if self.persistence is not None:
                    self.persistence.append_event(
                        "SERVICE_ERROR",
                        "Binance paper evaluation failed",
                        severity="ERROR",
                        details={"error": f"{type(exc).__name__}: {exc}"},
                    )
                self.runtime_active = False
            await asyncio.sleep(self.config.evaluation_interval_ms / 1000.0)

    def on_book(self, book: dict) -> None:
        """Receive the existing futures client's typed book callback."""
        self.adapter.ingest_book(book)
        snapshot = self.adapter.snapshot(int(book["received_at_ms"]))
        with self._lock:
            self.latest_snapshot = snapshot
            if not self.initialized or snapshot is None:
                return

            # bookTicker can publish many times per second. Keep pending-order latency
            # faithful to the live quote stream, but bound DuckDB portfolio work to the
            # configured sampling cadence so this observer cannot block feed ingestion.
            # Pending EXITS remain executable in CLOSE_ONLY mode. `_process_pending` rejects
            # entry intents when the environment gate is off, so processing the queue here
            # cannot accidentally open risk while allowing existing inventory to close.
            if self._pending:
                self._process_pending(snapshot)
            funding_due = (
                snapshot.funding_time_ms is not None
                and snapshot.funding_time_ms != self._last_funding_time_ms
            )
            # CLOSE_ONLY, not FROZEN.
            #
            # This returned early whenever `hard_enabled` was false, skipping portfolio
            # marking, funding application and the automatic risk exits. A persisted open
            # position recovered after a restart with the flag switched off therefore became
            # inventory the engine neither maintained nor would let the operator close
            # (close_position raised PermissionError on the same flag).
            #
            # Entry authority and position MAINTENANCE are different powers. With the gate off
            # there are no new entries - `_evaluate` is still gated in run() - but an existing
            # position is still marked, still charged funding, and can still exit.
            _has_inventory = bool(self.portfolio and self.persistence
                                  and self.persistence.open_positions())
            if not self.config.hard_enabled and not _has_inventory:
                return
            if (
                not funding_due
                and snapshot.received_at_ms - self._last_portfolio_update_ms
                < self.config.sample_interval_ms
            ):
                return

            self._last_portfolio_update_ms = snapshot.received_at_ms
            self.portfolio.mark(snapshot)
            funding_events = self.portfolio.apply_funding(snapshot)
            if funding_due:
                self._last_funding_time_ms = snapshot.funding_time_ms
            # AUDIT LOG, deferred. The funding itself was already applied above - this is the
            # record of it, and nothing reads it back to make a decision.
            for event in funding_events:
                self.journal.submit(
                    lambda ev=event: self.persistence.append_event(
                        "FUNDING_APPLIED",
                        "Observed Binance funding applied to paper position",
                        strategy_id=ev["strategy_id"],
                        details=ev,
                    )
                )
            governor = self._governor_decision(snapshot)
            if not governor.can_open:
                self._cancel_pending_entries(
                    reason=f"capital_governor:{governor.mode.value.lower()}"
                )
            if governor.must_flatten and snapshot.feed_health is DataQuality.HEALTHY:
                self._queue_governor_exits(snapshot, governor.reason_codes)
            self._queue_triggered_exits(snapshot)
            if (
                snapshot.received_at_ms - self._last_equity_snapshot_ms
                >= 10_000
            ):
                # EQUITY TELEMETRY, deferred. Pure reporting, on a 10s cadence, and the
                # largest recurring write on this thread.
                self.journal.submit(
                    lambda ts=snapshot.received_at_ms:
                        self.persistence.append_equity_snapshots(ts)
                )
                self._last_equity_snapshot_ms = snapshot.received_at_ms

    def start_engine(self) -> dict[str, Any]:
        self._require_initialized()
        if not self.config.hard_enabled:
            raise PermissionError(
                "BTC_ENABLE_BINANCE_PAPER=1 is required before the UI can start the engine"
            )
        with self._lock:
            self.runtime_active = True
        self.persistence.append_event("ENGINE_START", "Binance paper strategies started")
        return self.status()

    def pause_engine(self) -> dict[str, Any]:
        self._require_initialized()
        with self._lock:
            self.runtime_active = False
            cancelled = self._cancel_pending_entries(
                reason="engine_paused_before_arrival"
            )
        self.persistence.append_event(
            "ENGINE_PAUSE",
            "Binance paper strategies paused",
            details={"cancelled_pending_entries": cancelled},
        )
        return self.status()

    def _require_initialized(self) -> None:
        if not self.initialized or self.persistence is None or self.portfolio is None:
            raise RuntimeError("Binance paper service is not initialized")

    def _evaluate(self, snapshot: MarketSnapshot) -> None:
        if snapshot.received_at_ms == self._last_evaluation_ms:
            return
        self._last_evaluation_ms = snapshot.received_at_ms
        for strategy in self.registry.all():
            if not self.registry.is_enabled(strategy.strategy_id):
                continue
            decision = strategy.decide(snapshot)
            self.latest_decisions[strategy.strategy_id] = decision.to_dict()
            is_new = self.persistence.record_signal(decision)
            if not is_new:
                continue
            if decision.action not in (Action.OPEN_LONG, Action.OPEN_SHORT):
                continue
            current = self.portfolio.position_for(strategy.strategy_id)
            if current is not None and current["side"] != decision.side.value:
                self._queue_exit(
                    current,
                    snapshot,
                    signal_id=decision.signal_id,
                    exit_reason="OPPOSING_SIGNAL",
                    reversal_decision=decision,
                )
                continue
            # ONE ENTRY IN FLIGHT PER STRATEGY.
            #
            # The check above only asks whether a position is already OPEN. It does not ask
            # whether an entry for this strategy is already PENDING. Evaluation runs on every
            # book tick and the configured arrival latency is hundreds of milliseconds, so
            # several distinct OPEN signals could queue before the first one filled - and
            # `_process_pending` re-checked the governor and price limits but not whether an
            # earlier intent had just opened the strategy's only allowed position. The second
            # fill then collided with portfolio.open().
            if self._pending_entry_for(strategy.strategy_id) is not None:
                _LOG.info(
                    "[PAPER] %s entry suppressed: an entry is already pending (signal %s)",
                    strategy.strategy_id, decision.signal_id)
                continue
            self._queue_entry(decision, snapshot, signal_already_seen=False)

    def _pending_entry_for(self, strategy_id: str):
        """The in-flight ENTRY intent for this strategy, or None.

        `_evaluate` asked only whether a position was already OPEN. Evaluation runs on every
        book tick while arrival latency is hundreds of milliseconds, so several OPEN signals
        could queue before the first filled, and `_process_pending` re-checked the governor and
        price limits but never whether an earlier intent had already taken the strategy's one
        allowed slot. Checked at BOTH queue time and immediately before the fill, because the
        window between them is exactly where the collision lived.
        """
        for intent in self._pending.values():
            if intent.operation == "ENTRY" and intent.strategy_id == strategy_id:
                return intent
        return None

    def _order_id(self, signal_id: str, operation: str, suffix: str = "") -> str:
        return canonical_hash(
            {"signal_id": signal_id, "operation": operation, "suffix": suffix}
        )

    def _queue_entry(
        self,
        decision: StrategyDecision,
        snapshot: MarketSnapshot,
        *,
        signal_already_seen: bool,
    ) -> None:
        strategy = self.registry.get(decision.strategy_id)
        current = self.portfolio.position_for(decision.strategy_id)
        account = self.persistence.account(decision.strategy_id)
        governor = self._governor_decision(snapshot)
        order_id = self._order_id(decision.signal_id, "ENTRY")
        if not governor.can_open:
            reference = (
                snapshot.best_ask
                if decision.side is PositionSide.LONG
                else snapshot.best_bid
            )
            requested_quantity = (
                decision.requested_notional_usd / reference if reference > 0 else 0.0
            )
            reasons = tuple(
                f"capital_governor:{reason}"
                for reason in (
                    governor.reason_codes or (governor.mode.value.lower(),)
                )
            )
            self.persistence.append_order_event(
                order_id=order_id,
                signal_id=decision.signal_id,
                strategy_id=decision.strategy_id,
                operation="ENTRY",
                side=decision.side.value,
                requested_quantity=requested_quantity,
                requested_notional_usd=decision.requested_notional_usd,
                status="RISK_BLOCKED",
                decision_ts_ms=decision.timestamp_ms,
                simulated_send_ts_ms=decision.timestamp_ms,
                simulated_arrival_ts_ms=decision.timestamp_ms + self.config.latency_ms,
                rejection_reason=",".join(reasons),
            )
            self.latest_decisions[decision.strategy_id] = {
                **decision.to_dict(),
                "action": Action.RISK_BLOCKED.value,
                "reason_codes": list(reasons),
                "capital_governor": governor.to_dict(),
            }
            return
        effective_decision = (
            replace(
                decision,
                requested_notional_usd=(
                    decision.requested_notional_usd * governor.size_multiplier
                ),
            )
            if governor.size_multiplier < 1.0
            else decision
        )
        risk_result = self.risk_engine.evaluate_entry(
            decision=effective_decision,
            snapshot=snapshot,
            account=account,
            open_position=current,
            risk=strategy.risk,
            persistence=self.persistence,
            runtime_active=self.runtime_active,
            strategy_enabled=self.registry.is_enabled(decision.strategy_id),
            signal_already_seen=signal_already_seen,
            now_ms=snapshot.received_at_ms,
        )
        reference = (
            snapshot.best_ask
            if effective_decision.side is PositionSide.LONG
            else snapshot.best_bid
        )
        requested_quantity = (
            effective_decision.requested_notional_usd / reference
            if reference > 0
            else 0.0
        )
        if not risk_result.approved:
            self.persistence.append_order_event(
                order_id=order_id,
                signal_id=decision.signal_id,
                strategy_id=decision.strategy_id,
                operation="ENTRY",
                side=effective_decision.side.value,
                requested_quantity=requested_quantity,
                requested_notional_usd=effective_decision.requested_notional_usd,
                status="RISK_BLOCKED",
                decision_ts_ms=decision.timestamp_ms,
                simulated_send_ts_ms=decision.timestamp_ms,
                simulated_arrival_ts_ms=decision.timestamp_ms + self.config.latency_ms,
                rejection_reason=",".join(risk_result.reason_codes),
            )
            self.latest_decisions[decision.strategy_id] = {
                **decision.to_dict(),
                "action": Action.RISK_BLOCKED.value,
                "reason_codes": list(risk_result.reason_codes),
                "capital_governor": governor.to_dict(),
            }
            return
        intent = PendingIntent(
            order_id=order_id,
            signal_id=decision.signal_id,
            strategy_id=decision.strategy_id,
            side=effective_decision.side,
            operation="ENTRY",
            quantity=risk_result.approved_quantity,
            requested_notional_usd=risk_result.approved_notional_usd,
            decision_ts_ms=decision.timestamp_ms,
            arrival_ts_ms=decision.timestamp_ms + self.config.latency_ms,
            decision=effective_decision,
        )
        self._pending[order_id] = intent
        self.persistence.append_order_event(
            order_id=order_id,
            signal_id=decision.signal_id,
            strategy_id=decision.strategy_id,
            operation="ENTRY",
            side=decision.side.value,
            requested_quantity=intent.quantity,
            requested_notional_usd=intent.requested_notional_usd,
            status="PENDING",
            decision_ts_ms=intent.decision_ts_ms,
            simulated_send_ts_ms=intent.decision_ts_ms,
            simulated_arrival_ts_ms=intent.arrival_ts_ms,
        )

    def _queue_exit(
        self,
        position: dict,
        snapshot: MarketSnapshot,
        *,
        signal_id: str,
        exit_reason: str,
        reversal_decision: StrategyDecision | None = None,
    ) -> str:
        for pending in self._pending.values():
            if (
                pending.operation == "EXIT"
                and pending.position_id == position["position_id"]
            ):
                return pending.order_id
        suffix = str(position["position_id"])
        order_id = self._order_id(signal_id, "EXIT", suffix)
        if order_id in self._pending:
            return order_id
        side = PositionSide(position["side"])
        intent = PendingIntent(
            order_id=order_id,
            signal_id=signal_id,
            strategy_id=position["strategy_id"],
            side=side,
            operation="EXIT",
            quantity=float(position["quantity"]),
            requested_notional_usd=float(position["quantity"]) * snapshot.mark_price,
            decision_ts_ms=snapshot.received_at_ms,
            arrival_ts_ms=snapshot.received_at_ms + self.config.latency_ms,
            position_id=position["position_id"],
            exit_reason=exit_reason,
            reversal_decision=reversal_decision,
        )
        self._pending[order_id] = intent
        self.persistence.append_order_event(
            order_id=order_id,
            signal_id=signal_id,
            strategy_id=position["strategy_id"],
            operation="EXIT",
            side=side.value,
            requested_quantity=intent.quantity,
            requested_notional_usd=intent.requested_notional_usd,
            status="PENDING",
            decision_ts_ms=intent.decision_ts_ms,
            simulated_send_ts_ms=intent.decision_ts_ms,
            simulated_arrival_ts_ms=intent.arrival_ts_ms,
        )
        return order_id

    def _process_pending(self, snapshot: MarketSnapshot) -> None:
        due = [
            intent
            for intent in self._pending.values()
            if snapshot.received_at_ms >= intent.arrival_ts_ms
        ]
        due.sort(key=lambda item: (item.arrival_ts_ms, item.operation))
        for intent in due:
            if intent.operation == "ENTRY" and not self.config.hard_enabled:
                self._cancel_pending_intent(
                    intent,
                    "engine_environment_gate_disabled_before_arrival",
                )
                continue
            if intent.operation == "ENTRY" and (
                not self.runtime_active
                or not self.registry.is_enabled(intent.strategy_id)
            ):
                self._cancel_pending_intent(
                    intent,
                    "entry_inactive_before_arrival",
                )
                continue
            # An EARLIER pending entry may have opened this strategy's position while this one
            # was in flight. Queue-time exclusion cannot see that; only the arrival check can.
            if intent.operation == "ENTRY" and self.portfolio.position_for(
                    intent.strategy_id) is not None:
                self._cancel_pending_intent(
                    intent,
                    "position_opened_before_arrival",
                )
                continue
            if intent.operation == "ENTRY":
                governor = self._governor_decision(snapshot)
                if not governor.can_open:
                    self._cancel_pending_intent(
                        intent,
                        f"capital_governor_before_arrival:{governor.mode.value.lower()}",
                    )
                    continue
                invalid_reason = self._entry_arrival_invalid_reason(intent, snapshot)
                if invalid_reason:
                    self._cancel_pending_intent(intent, invalid_reason)
                    continue
            fill = self.fill_simulator.simulate(
                signal_id=intent.signal_id,
                order_id=intent.order_id,
                strategy_id=intent.strategy_id,
                side=intent.side,
                operation=intent.operation,
                requested_quantity=intent.quantity,
                decision_ts_ms=intent.decision_ts_ms,
                snapshot=snapshot,
                require_full=True,
            )
            status = "FILLED" if fill.filled_quantity > 0 else "REJECTED"
            with self.persistence.transaction() as connection:
                self.persistence.append_fill(fill, connection=connection)
                self.persistence.append_order_event(
                    order_id=intent.order_id,
                    signal_id=intent.signal_id,
                    strategy_id=intent.strategy_id,
                    operation=intent.operation,
                    side=intent.side.value,
                    requested_quantity=intent.quantity,
                    requested_notional_usd=intent.requested_notional_usd,
                    status=status,
                    decision_ts_ms=intent.decision_ts_ms,
                    simulated_send_ts_ms=intent.decision_ts_ms,
                    simulated_arrival_ts_ms=intent.arrival_ts_ms,
                    rejection_reason=fill.rejection_reason,
                    connection=connection,
                )
                if fill.filled_quantity > 0:
                    strategy = self.registry.get(intent.strategy_id)
                    if intent.operation == "ENTRY":
                        # POST-FILL GEOMETRY. The stop and target were set as offsets from
                        # the decision-time mark, but the entry happened at the ask (or bid)
                        # plus slippage and latency drift. Distances measured from the real
                        # fill are what the position actually has.
                        #
                        # A FILL CANNOT BE UN-FILLED. The first version of this appended a
                        # REJECTED_POST_FILL order event after FILLED, which is not a valid
                        # OrderState and has no permitted transition out of FILLED - so it
                        # raised, rolled the whole transaction back (erasing a fill that had
                        # already happened), left the intent pending, and retried on every
                        # book update. An infinite loop, and the paper selftest never saw it
                        # because it only ever exercised the admissible branch.
                        #
                        # The position opens. If the economics are dead it is UNWOUND through
                        # the same exit path stops and targets use, paying a second spread,
                        # a second fee and a second latency - which is what actually happens.
                        geo = _post_fill_geometry(
                            side=intent.side.value,
                            fill_price=fill.average_fill_price,
                            decided_entry=intent.decision.decision_mark_price,
                            stop_price=intent.decision.stop_price,
                            target_price=intent.decision.take_profit_price,
                            # FEES AND SLIPPAGE. This charged fees only, while the exit
                            # simulator also applies slippage_bps - so a target could clear
                            # the post-fill check and then not clear the real exit cost.
                            round_trip_bps=self.config.round_trip_cost_bps,
                        )
                        self._last_post_fill_geometry = geo
                        if not geo.get("admissible"):
                            _LOG.warning(
                                "[POST-FILL] %s entry economics invalidated at the fill: %s "
                                "- opening then unwinding; the unwind cost is real and is "
                                "charged to the strategy.",
                                intent.order_id, geo.get("reason"))
                            self._post_fill_invalidated.append(
                                (intent.strategy_id, str(geo.get("reason"))[:200]))
                        self.portfolio.open(
                            intent.decision,
                            fill,
                            strategy.risk.leverage,
                            connection=connection,
                        )
                    else:
                        self.portfolio.close(
                            intent.position_id,
                            fill,
                            intent.exit_reason or "MANUAL",
                            strategy.strategy_version,
                            connection=connection,
                        )
            del self._pending[intent.order_id]
            if fill.filled_quantity <= 0:
                continue

            # Unwind anything the post-fill check invalidated. Done AFTER the commit, so the
            # position it exits actually exists, and through _queue_exit so the unwind is a
            # real order with its own id, spread, fee and latency rather than a bookkeeping
            # erasure.
            while self._post_fill_invalidated:
                strategy_id, reason = self._post_fill_invalidated.pop(0)
                for position in self.persistence.open_positions(strategy_id):
                    try:
                        self._queue_exit(
                            position, snapshot,
                            signal_id=f"postfill-{position['position_id']}",
                            exit_reason="POST_FILL_ECONOMICS_INVALID")
                        _LOG.warning("[POST-FILL] unwinding %s: %s",
                                     position["position_id"], reason)
                    except Exception as exc:
                        _LOG.error("[POST-FILL] unwind could not be queued for %s: %s",
                                   position["position_id"], exc)
            if (
                intent.operation == "EXIT"
                and intent.reversal_decision is not None
            ):
                # A close and a reversal are two market interactions. Reusing the original
                # decision timestamp made the second order's arrival time already elapsed.
                # Re-evaluate on the post-exit snapshot, require the thesis to persist, then
                # stamp a fresh decision so the new entry pays a complete latency leg.
                requested_side = intent.reversal_decision.side
                strategy = self.registry.get(intent.strategy_id)
                fresh = strategy.decide(snapshot)
                if (
                    fresh.action in (Action.OPEN_LONG, Action.OPEN_SHORT)
                    and fresh.side == requested_side
                ):
                    ttl_ms = (
                        max(0, int(fresh.valid_until_ms) - int(fresh.timestamp_ms))
                        if fresh.valid_until_ms is not None else None
                    )
                    fresh = replace(
                        fresh,
                        timestamp_ms=snapshot.received_at_ms,
                        valid_until_ms=(
                            snapshot.received_at_ms + ttl_ms
                            if ttl_ms is not None else None
                        ),
                    )
                    self.persistence.record_signal(fresh)
                    self._queue_entry(
                        fresh,
                        snapshot,
                        signal_already_seen=False,
                    )
                else:
                    self.persistence.append_event(
                        "REVERSAL_CANCELLED",
                        "Opposing thesis did not persist after the close fill",
                        strategy_id=intent.strategy_id,
                        details={
                            "requested_side": (
                                requested_side.value if requested_side is not None else None
                            ),
                            "fresh_action": fresh.action.value,
                            "fresh_side": fresh.side.value if fresh.side is not None else None,
                        },
                    )

    def _entry_arrival_invalid_reason(
        self, intent: PendingIntent, snapshot: MarketSnapshot
    ) -> str | None:
        decision = intent.decision
        if decision is None:
            return "entry_decision_missing"
        if (
            decision.valid_until_ms is not None
            and snapshot.received_at_ms > decision.valid_until_ms
        ):
            return "signal_expired_before_arrival"
        slippage = self.config.slippage_bps / 10_000.0
        expected_price = (
            snapshot.best_ask * (1.0 + slippage)
            if intent.side is PositionSide.LONG
            else snapshot.best_bid * (1.0 - slippage)
        )
        if not math.isfinite(expected_price) or expected_price <= 0:
            return "entry_price_invalid_before_arrival"
        if (
            intent.side is PositionSide.LONG
            and decision.maximum_entry_price is not None
            and expected_price > decision.maximum_entry_price
        ):
            return "maximum_entry_price_breached_before_arrival"
        if (
            intent.side is PositionSide.SHORT
            and decision.minimum_entry_price is not None
            and expected_price < decision.minimum_entry_price
        ):
            return "minimum_entry_price_breached_before_arrival"
        return None

    def _cancel_pending_intent(self, intent: PendingIntent, reason: str) -> None:
        self.persistence.append_order_event(
            order_id=intent.order_id,
            signal_id=intent.signal_id,
            strategy_id=intent.strategy_id,
            operation=intent.operation,
            side=intent.side.value,
            requested_quantity=intent.quantity,
            requested_notional_usd=intent.requested_notional_usd,
            status="CANCELLED",
            decision_ts_ms=intent.decision_ts_ms,
            simulated_send_ts_ms=intent.decision_ts_ms,
            simulated_arrival_ts_ms=intent.arrival_ts_ms,
            rejection_reason=reason,
        )
        self._pending.pop(intent.order_id, None)

    def _cancel_pending_entries(
        self,
        *,
        strategy_id: str | None = None,
        reason: str,
    ) -> int:
        pending = [
            intent
            for intent in self._pending.values()
            if intent.operation == "ENTRY"
            and (strategy_id is None or intent.strategy_id == strategy_id)
        ]
        for intent in pending:
            self._cancel_pending_intent(intent, reason)
        return len(pending)

    def _queue_triggered_exits(self, snapshot: MarketSnapshot) -> None:
        pending_positions = {
            intent.position_id
            for intent in self._pending.values()
            if intent.operation == "EXIT"
        }
        for position in self.persistence.open_positions():
            if position["position_id"] in pending_positions:
                continue
            strategy = self.registry.get(position["strategy_id"])
            # ONE exit path, with precedence enforced inside portfolio.exit_reason:
            # STOP / TAKE_PROFIT (prices the book actually reached) beat the strategy's thesis
            # check, which beats MAX_HOLD. Calling position_exit_reason FIRST - as this did -
            # let a strategy opinion relabel a position that had genuinely been stopped out.
            #
            # The portfolio is an injectable seam and is absent in some unit contexts; without
            # it there are no static levels to respect, so the thesis check stands alone.
            if self.portfolio is not None:
                reason = self.portfolio.exit_reason(
                    position, snapshot, strategy,
                    maintenance_margin_rate=self.config.maintenance_margin_rate,
                )
            else:
                reason = strategy.position_exit_reason(position, snapshot)
            if reason:
                signal_id = canonical_hash(
                    {
                        "position_id": position["position_id"],
                        "reason": reason,
                        "decision_ts_ms": snapshot.received_at_ms,
                    }
                )
                self._queue_exit(
                    position,
                    snapshot,
                    signal_id=signal_id,
                    exit_reason=reason,
                )

    def _queue_governor_exits(
        self, snapshot: MarketSnapshot, reason_codes: tuple[str, ...]
    ) -> None:
        for position in self.persistence.open_positions():
            signal_id = canonical_hash(
                {
                    "position_id": position["position_id"],
                    "reason": "GOVERNOR_EMERGENCY_FLATTEN",
                    "reason_codes": reason_codes,
                    "decision_ts_ms": snapshot.received_at_ms,
                }
            )
            self._queue_exit(
                position,
                snapshot,
                signal_id=signal_id,
                exit_reason="GOVERNOR_EMERGENCY_FLATTEN",
            )

    def _governor_decision(self, snapshot: MarketSnapshot | None = None):
        now_ms = (
            snapshot.received_at_ms
            if snapshot is not None
            else int(time.time() * 1000)
        )
        if not self.initialized or self.persistence is None:
            return self.governor.evaluate(
                snapshot=snapshot,
                accounts=(),
                integrity_error=self._integrity_error or "service_not_initialized",
                now_ms=now_ms,
            )
        day_start_ms = (now_ms // 86_400_000) * 86_400_000
        day_index = now_ms // 86_400_000
        monday_day_index = day_index - ((day_index + 3) % 7)
        week_start_ms = monday_day_index * 86_400_000
        states = []
        for strategy in self.registry.all():
            account = self.persistence.account(strategy.strategy_id)
            states.append(
                GovernorAccountState(
                    strategy_id=strategy.strategy_id,
                    starting_cash_usd=float(account["starting_cash_usd"]),
                    equity_usd=float(account["equity_usd"]),
                    peak_equity_usd=float(account["peak_equity_usd"]),
                    daily_net_pnl_usd=self.persistence.daily_net_pnl(
                        strategy.strategy_id, day_start_ms
                    ),
                    weekly_net_pnl_usd=self.persistence.net_pnl_since(
                        strategy.strategy_id, week_start_ms
                    ),
                    risk=strategy.risk.clamped(),
                )
            )
        pending_ages = (
            now_ms - intent.decision_ts_ms for intent in self._pending.values()
        )
        return self.governor.evaluate(
            snapshot=snapshot,
            accounts=states,
            pending_ages_ms=pending_ages,
            integrity_error=self._integrity_error,
            now_ms=now_ms,
        )

    def close_position(self, position_id: str, *, confirm: bool) -> dict[str, Any]:
        self._require_initialized()
        if not confirm:
            raise ValueError("confirm=true is required")
        # DELIBERATELY NOT GATED on hard_enabled. Refusing to close while the environment is
        # disabled is what turned "disabled" into "your position is stuck". Closing REDUCES
        # exposure; the gate exists to stop new risk, not to trap existing risk.
        
        with self._lock:
            position = self.persistence.position(position_id)
            if position["status"] != "OPEN":
                raise ValueError("position is not open")
            snapshot = self.adapter.snapshot()
            if snapshot is None or snapshot.feed_health is not DataQuality.HEALTHY:
                raise RuntimeError("fresh perpetual book is required to close a position")
            order_id = self._queue_exit(
                position,
                snapshot,
                signal_id=canonical_hash(
                    {
                        "manual_close": position_id,
                        "timestamp_ms": snapshot.received_at_ms,
                    }
                ),
                exit_reason="MANUAL",
            )
        return {"status": "PENDING", "order_id": order_id, "position_id": position_id}

    def close_all(self, *, confirm: bool) -> dict[str, Any]:
        self._require_initialized()
        if not confirm:
            raise ValueError("confirm=true is required")
        order_ids = []
        with self._lock:
            for position in list(self.persistence.open_positions()):
                order_ids.append(
                    self.close_position(position["position_id"], confirm=True)[
                        "order_id"
                    ]
                )
        return {"status": "PENDING", "order_ids": order_ids}

    def update_strategy(
        self,
        strategy_id: str,
        *,
        enabled: bool | None = None,
        risk_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_initialized()
        with self._lock:
            result = self.registry.update(
                self.persistence,
                strategy_id,
                enabled=enabled,
                risk_patch=risk_patch,
            )
            if enabled is False:
                self._cancel_pending_entries(
                    strategy_id=strategy_id,
                    reason="strategy_disabled_before_arrival",
                )
            return result

    def market_status(self) -> dict[str, Any]:
        snapshot = self.adapter.snapshot()
        health = self.adapter.futures_client.health_snapshot()
        if snapshot is None:
            return {
                "status": "NO_DATA",
                "symbol": "BTCUSDT",
                "required_feed": "binance_futures_ws_bookTicker",
                "book_message_count": health["book_message_count"],
                "book_age_ms": health["book_age_ms"],
                "agg_trade_message_count": health["agg_trade_message_count"],
                "agg_trade_age_ms": health["agg_trade_age_ms"],
            }
        return {
            "status": snapshot.feed_health.value,
            **snapshot.to_dict(),
            "book_message_count": health["book_message_count"],
            "book_age_ms": health["book_age_ms"],
            "agg_trade_message_count": health["agg_trade_message_count"],
            "last_completed_perp_cvd_bar_ts_ms": health[
                "last_completed_perp_cvd_bar_ts_ms"
            ],
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = (
                "PAPER_ENGINE_DISABLED"
                if not self.config.hard_enabled
                else ("RUNNING" if self.runtime_active else "PAUSED")
            )
            return {
                "paper_only": True,
                "real_orders_disabled": True,
                "hard_gate_enabled": self.config.hard_enabled,
                "runtime_state": state,
                "initialized": self.initialized,
                "database_path": str(self.config.db_path),
                "market": self.market_status(),
                "pending_order_count": len(self._pending),
                "capital_governor": self._governor_decision(
                    self.adapter.snapshot()
                ).to_dict(),
            }

    def strategy_statuses(self) -> list[dict[str, Any]]:
        self._require_initialized()
        snapshot = self.adapter.snapshot()
        governor = self._governor_decision(snapshot)
        positions = {
            row["strategy_id"]: row for row in self.persistence.open_positions()
        }
        accounts = {row["strategy_id"]: row for row in self.persistence.accounts()}
        result = []
        for description in self.registry.descriptions():
            strategy_id = description["strategy_id"]
            availability = snapshot.feature_availability if snapshot else {}
            required = description["required_inputs"]
            missing = [name for name in required if not availability.get(name, False)]
            result.append(
                {
                    **description,
                    "available_inputs": [
                        name for name in required if availability.get(name, False)
                    ],
                    "missing_inputs": missing,
                    "inactive_reason": (
                        "Paper engine disabled"
                        if not self.config.hard_enabled
                        else (
                            "Strategy disabled"
                            if not description["enabled"]
                            else (
                                "Missing required inputs"
                                if missing
                                else (
                                    "Engine paused"
                                    if not self.runtime_active
                                    else (
                                        f"Capital governor: {governor.mode.value}"
                                        if not governor.can_open
                                        else None
                                    )
                                )
                            )
                        )
                    ),
                    "data_ages": {
                        "book_age_ms": snapshot.feed_age_ms if snapshot else None,
                        "agg_trade_age_ms": (
                            snapshot.agg_trade_age_ms if snapshot else None
                        ),
                    },
                    "latest_decision": self.latest_decisions.get(strategy_id),
                    "account": accounts[strategy_id],
                    "position": positions.get(strategy_id),
                    "metrics": strategy_metrics(self.persistence, strategy_id),
                    "capital_governor": governor.to_dict(),
                }
            )
        return result

    def accounts(self) -> list[dict[str, Any]]:
        self._require_initialized()
        positions = {
            row["strategy_id"]: row for row in self.persistence.open_positions()
        }
        output = []
        for account in self.persistence.accounts():
            position = positions.get(account["strategy_id"])
            notional = (
                float(position["quantity"]) * float(position["last_mark_price"])
                if position
                else 0.0
            )
            output.append(
                {
                    **account,
                    "gross_exposure_usd": notional,
                    "long_exposure_usd": (
                        notional if position and position["side"] == "LONG" else 0.0
                    ),
                    "short_exposure_usd": (
                        notional if position and position["side"] == "SHORT" else 0.0
                    ),
                }
            )
        return output

    def positions(self) -> list[dict[str, Any]]:
        self._require_initialized()
        now = int(time.time() * 1000)
        return [
            {
                **row,
                "notional_usd": float(row["quantity"]) * float(row["last_mark_price"]),
                "holding_seconds": max(0.0, (now - int(row["opened_at_ms"])) / 1000.0),
            }
            for row in self.persistence.open_positions()
        ]

    def orders(self, limit: int = 100) -> list[dict[str, Any]]:
        self._require_initialized()
        return self.persistence.latest_orders(limit)

    def fills(self, limit: int = 100) -> list[dict[str, Any]]:
        self._require_initialized()
        return self.persistence.fills(limit)

    def funding_events(self, limit: int = 100) -> list[dict[str, Any]]:
        self._require_initialized()
        return self.persistence.funding_events(limit)

    def trades(self, limit: int = 100) -> list[dict[str, Any]]:
        self._require_initialized()
        return self.persistence.trades(limit)

    def metrics(self) -> dict[str, Any]:
        self._require_initialized()
        return all_metrics(self.persistence)

    def equity(self, strategy_id: str | None = None, limit: int = 1000):
        self._require_initialized()
        return self.persistence.equity_snapshots(limit, strategy_id)

    def events(self, limit: int = 100):
        self._require_initialized()
        return self.persistence.events(limit)
