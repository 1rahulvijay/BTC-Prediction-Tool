"""Canonical, immutable market events shared by venue-specific domains."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping


class EventHealth(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    GAP = "GAP"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    venue: str
    instrument: str
    event_type: str
    exchange_ts_ns: int
    receive_ts_ns: int
    sequence_id: str
    source_id: str
    recording_session_id: str
    payload: Mapping[str, Any]
    health: EventHealth = EventHealth.HEALTHY

    def __post_init__(self) -> None:
        for name in (
            "venue",
            "instrument",
            "event_type",
            "sequence_id",
            "source_id",
            "recording_session_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.exchange_ts_ns <= 0 or self.receive_ts_ns <= 0:
            raise ValueError("event timestamps must be positive nanoseconds")
        if self.receive_ts_ns < self.exchange_ts_ns:
            raise ValueError("receive_ts_ns cannot precede exchange_ts_ns")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")

    @property
    def latency_ms(self) -> float:
        return (self.receive_ts_ns - self.exchange_ts_ns) / 1_000_000.0

    def canonical_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["health"] = self.health.value
        value["payload"] = dict(self.payload)
        return value

    def sha256(self) -> str:
        raw = json.dumps(
            self.canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
