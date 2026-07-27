"""Feature-schema identity and fail-closed validation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping


@dataclass(frozen=True, slots=True)
class FeatureContract:
    name: str
    version: str
    features: tuple[str, ...]
    optional_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.version or not self.features:
            raise ValueError("name, version, and required features are mandatory")
        if len(set(self.features)) != len(self.features):
            raise ValueError("required features must be unique")
        overlap = set(self.features).intersection(self.optional_features)
        if overlap:
            raise ValueError(f"required/optional features overlap: {sorted(overlap)}")

    @property
    def sha256(self) -> str:
        raw = json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "features": self.features,
                "optional_features": self.optional_features,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def validate(
        self, values: Mapping[str, object], reject_unknown: bool = False
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        for name in self.features:
            if name not in values:
                reasons.append(f"missing:{name}")
                continue
            try:
                if not math.isfinite(float(values[name])):
                    reasons.append(f"non_finite:{name}")
            except (TypeError, ValueError):
                reasons.append(f"non_numeric:{name}")
        if reject_unknown:
            known = set(self.features).union(self.optional_features)
            for name in values:
                if name not in known:
                    reasons.append(f"unknown:{name}")
        return not reasons, reasons
