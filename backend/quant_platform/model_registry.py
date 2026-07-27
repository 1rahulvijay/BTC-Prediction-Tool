"""Immutable model-bundle registrations and explicit active pointers."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class ModelBundleIdentity:
    model_name: str
    bundle_id: str
    bundle_sha256: str
    promoted_at_s: float
    code_sha256: str
    dataset_sha256: str
    feature_schema_sha256: str
    policy_sha256: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "promoted_at_s":
                if float(value) <= 0:
                    raise ValueError("promoted_at_s must be positive")
            elif not str(value).strip():
                raise ValueError(f"{name} must be non-empty")


class ModelRegistry:
    def __init__(self) -> None:
        self._bundles: dict[str, ModelBundleIdentity] = {}
        self._active: dict[str, str] = {}
        self._lock = RLock()

    def register(self, identity: ModelBundleIdentity) -> None:
        with self._lock:
            existing = self._bundles.get(identity.bundle_id)
            if existing is not None and existing != identity:
                raise ValueError("bundle_id collision with different immutable identity")
            self._bundles[identity.bundle_id] = identity

    def activate(self, model_name: str, bundle_id: str) -> None:
        with self._lock:
            identity = self._bundles.get(bundle_id)
            if identity is None:
                raise KeyError(f"unknown bundle_id: {bundle_id}")
            if identity.model_name != model_name:
                raise ValueError("bundle model_name does not match active slot")
            self._active[model_name] = bundle_id

    def active(self, model_name: str) -> ModelBundleIdentity | None:
        with self._lock:
            bundle_id = self._active.get(model_name)
            return self._bundles.get(bundle_id) if bundle_id else None

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                name: asdict(self._bundles[bundle_id])
                for name, bundle_id in self._active.items()
            }
