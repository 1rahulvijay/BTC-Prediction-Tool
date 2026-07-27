"""Strategy identity and lifecycle; no venue economics live here."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from threading import RLock


class StrategyMode(StrEnum):
    DISABLED = "DISABLED"
    RESEARCH = "RESEARCH"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_id: str
    mechanism: str
    venue: str
    instrument: str
    feature_schema_sha256: str
    protocol_sha256: str
    mode: StrategyMode = StrategyMode.RESEARCH

    @property
    def identity_sha256(self) -> str:
        raw = json.dumps(
            {
                **asdict(self),
                "mode": self.mode.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class StrategyRegistry:
    def __init__(self) -> None:
        self._items: dict[str, StrategyDefinition] = {}
        self._lock = RLock()

    def register(self, definition: StrategyDefinition) -> None:
        with self._lock:
            existing = self._items.get(definition.strategy_id)
            if existing is not None and existing != definition:
                raise ValueError("strategy_id collision with different definition")
            self._items[definition.strategy_id] = definition

    def get(self, strategy_id: str) -> StrategyDefinition:
        with self._lock:
            return self._items[strategy_id]

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                key: {
                    **asdict(value),
                    "mode": value.mode.value,
                    "identity_sha256": value.identity_sha256,
                }
                for key, value in self._items.items()
            }
