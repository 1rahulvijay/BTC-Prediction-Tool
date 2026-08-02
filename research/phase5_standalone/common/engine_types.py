"""Types shared by Phase 5 experiment engines."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import FrozenProtocol


@dataclass(frozen=True, slots=True)
class EngineContext:
    protocol: FrozenProtocol
    data_dir: Path
    maximum_rows: int
    seed: int
    cost_multiplier: float
    split_args: dict[str, str | None]
    dry_run: bool


@dataclass(slots=True)
class EngineResult:
    status: str
    summary: str
    diagnostics: dict[str, Any]
    economics: dict[str, Any]
    reasons: list[str]
    data_identity: dict[str, Any]
    causal_summary: dict[str, Any]
    split_manifest: dict[str, Any] | None = None
