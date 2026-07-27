"""Sequence-aware feed health that fails closed on stale or incomplete data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Iterable

from .clocks import Clock, SystemClock
from .events import EventHealth, MarketEvent


@dataclass(slots=True)
class SourceHealth:
    source_id: str
    recording_session_id: str
    last_receive_ts_ns: int
    last_exchange_ts_ns: int
    last_sequence: int | None
    event_count: int = 0
    gap_count: int = 0
    out_of_order_count: int = 0
    source_degraded_count: int = 0
    status: EventHealth = EventHealth.HEALTHY


class FeedHealthMonitor:
    _SEVERITY = {
        EventHealth.HEALTHY: 0,
        EventHealth.DEGRADED: 1,
        EventHealth.GAP: 2,
        EventHealth.STALE: 3,
        EventHealth.INVALID: 4,
    }

    def __init__(self, stale_after_s: float = 5.0, clock: Clock | None = None):
        if stale_after_s <= 0:
            raise ValueError("stale_after_s must be positive")
        self.stale_after_ns = int(stale_after_s * 1_000_000_000)
        self.clock = clock or SystemClock()
        self._sources: dict[str, SourceHealth] = {}
        self._lock = RLock()

    @staticmethod
    def _numeric_sequence(value: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def record(self, event: MarketEvent) -> EventHealth:
        sequence = self._numeric_sequence(event.sequence_id)
        with self._lock:
            state = self._sources.get(event.source_id)
            is_new_session = (
                state is None
                or state.recording_session_id != event.recording_session_id
            )
            if is_new_session:
                state = SourceHealth(
                    source_id=event.source_id,
                    recording_session_id=event.recording_session_id,
                    last_receive_ts_ns=event.receive_ts_ns,
                    last_exchange_ts_ns=event.exchange_ts_ns,
                    last_sequence=sequence,
                )
                self._sources[event.source_id] = state

            status = event.health
            if not is_new_session and event.receive_ts_ns < state.last_receive_ts_ns:
                state.out_of_order_count += 1
                status = EventHealth.INVALID
            elif (
                not is_new_session
                and
                sequence is not None
                and state.last_sequence is not None
                and sequence <= state.last_sequence
            ):
                state.out_of_order_count += 1
                status = EventHealth.INVALID
            elif (
                not is_new_session
                and
                sequence is not None
                and state.last_sequence is not None
                and sequence > state.last_sequence + 1
            ):
                state.gap_count += sequence - state.last_sequence - 1
                status = EventHealth.GAP

            if not is_new_session and (
                self._SEVERITY[state.status] > self._SEVERITY[status]
            ):
                status = state.status

            state.event_count += 1
            if event.health is not EventHealth.HEALTHY:
                state.source_degraded_count += 1
            state.last_receive_ts_ns = max(
                state.last_receive_ts_ns, event.receive_ts_ns
            )
            state.last_exchange_ts_ns = max(
                state.last_exchange_ts_ns, event.exchange_ts_ns
            )
            if sequence is not None and (
                state.last_sequence is None or sequence > state.last_sequence
            ):
                state.last_sequence = sequence
            state.status = status
            return status

    def snapshot(self) -> dict[str, dict]:
        now_ns = self.clock.time_ns()
        with self._lock:
            result: dict[str, dict] = {}
            for source_id, state in self._sources.items():
                row = asdict(state)
                age_ns = max(0, now_ns - state.last_receive_ts_ns)
                row["age_ms"] = age_ns / 1_000_000.0
                status = state.status
                if age_ns > self.stale_after_ns:
                    status = EventHealth.STALE
                row["status"] = status.value
                result[source_id] = row
            return result

    def healthy(self, required_sources: Iterable[str]) -> tuple[bool, list[str]]:
        snapshot = self.snapshot()
        reasons: list[str] = []
        for source_id in required_sources:
            row = snapshot.get(source_id)
            if row is None:
                reasons.append(f"missing_source:{source_id}")
            elif row["status"] != EventHealth.HEALTHY.value:
                reasons.append(f"{source_id}:{row['status'].lower()}")
        return not reasons, reasons
