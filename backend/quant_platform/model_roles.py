"""Explicit prediction-target ownership for specialist models and ensembles."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from threading import RLock


class ModelRole(StrEnum):
    SETTLEMENT = "SETTLEMENT"
    REPRICING = "REPRICING"
    DIRECTION_BARRIER = "DIRECTION_BARRIER"
    MAGNITUDE = "MAGNITUDE"
    FILL = "FILL"
    TOXICITY = "TOXICITY"
    COST = "COST"
    RETURN = "RETURN"
    CARRY = "CARRY"
    REGIME = "REGIME"
    RELIABILITY = "RELIABILITY"


@dataclass(frozen=True, slots=True)
class TargetContract:
    """The economic object a model predicts.

    Models may share an ensemble only when this entire contract matches. This
    prevents, for example, a five-second direction forecast from voting on a
    one-hour settlement outcome.
    """

    target_name: str
    role: ModelRole
    venue: str
    instrument: str
    horizon_seconds: int
    outcome_semantics: str

    def __post_init__(self) -> None:
        for name in ("target_name", "venue", "instrument", "outcome_semantics"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")

    @property
    def key(self) -> str:
        raw = json.dumps(
            {
                **asdict(self),
                "role": self.role.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelRoleDefinition:
    model_id: str
    model_version: str
    contract: TargetContract
    allowed_uses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.model_id.strip() or not self.model_version.strip():
            raise ValueError("model_id and model_version are required")
        if not self.allowed_uses or any(not item.strip() for item in self.allowed_uses):
            raise ValueError("allowed_uses must contain non-empty values")


class ModelRoleRegistry:
    """Immutable registrations with strict ensemble compatibility checks."""

    def __init__(self) -> None:
        self._items: dict[str, ModelRoleDefinition] = {}
        self._lock = RLock()

    def register(self, definition: ModelRoleDefinition) -> None:
        key = f"{definition.model_id}:{definition.model_version}"
        with self._lock:
            existing = self._items.get(key)
            if existing is not None and existing != definition:
                raise ValueError("model identity collision with different role")
            self._items[key] = definition

    def get(self, model_id: str, model_version: str) -> ModelRoleDefinition:
        with self._lock:
            return self._items[f"{model_id}:{model_version}"]

    def require_compatible(
        self,
        members: list[tuple[str, str]],
        use: str,
    ) -> TargetContract:
        if not members:
            raise ValueError("an ensemble requires at least one model")
        definitions = [self.get(*member) for member in members]
        contracts = {item.contract for item in definitions}
        if len(contracts) != 1:
            raise ValueError("target_mismatch: models cannot share this ensemble")
        denied = [
            item.model_id for item in definitions if use not in item.allowed_uses
        ]
        if denied:
            raise ValueError(f"use_not_allowed:{','.join(sorted(denied))}")
        return definitions[0].contract

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                key: {
                    "model_id": item.model_id,
                    "model_version": item.model_version,
                    "contract_key": item.contract.key,
                    "contract": {
                        **asdict(item.contract),
                        "role": item.contract.role.value,
                    },
                    "allowed_uses": list(item.allowed_uses),
                }
                for key, item in self._items.items()
            }
