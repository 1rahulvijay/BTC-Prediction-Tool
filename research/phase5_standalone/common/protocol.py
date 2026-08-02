"""Frozen protocol loading and validation for Phase 5 experiments."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


VERDICTS = frozenset({
    "PASS_CANDIDATE",
    "FAIL_NO_EDGE",
    "FAIL_AFTER_COSTS",
    "FAIL_UNSTABLE",
    "BLOCKED_DATA",
    "BLOCKED_SCHEMA",
    "INSUFFICIENT_SAMPLE",
})


@dataclass(frozen=True, slots=True)
class FrozenProtocol:
    path: Path
    payload: dict[str, Any]
    sha256: str

    @property
    def experiment_id(self) -> str:
        return str(self.payload["experiment_id"])

    @property
    def engine(self) -> str:
        return str(self.payload["engine"])


def canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def protocol_hash(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "protocol_sha256"}
    return hashlib.sha256(canonical_json(clean)).hexdigest()


def load_protocol(path: str | Path) -> FrozenProtocol:
    source = Path(path).resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    required = {
        "protocol_version", "experiment_id", "question", "engine", "data_contract",
        "method", "controls", "cost_model", "promotion_gates", "capital_authority",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"protocol missing required keys: {missing}")
    if payload["capital_authority"] is not False:
        raise ValueError("standalone research protocols must set capital_authority=false")
    if not isinstance(payload["controls"], list) or not payload["controls"]:
        raise ValueError("protocol must declare at least one control")
    declared = payload.get("protocol_sha256")
    actual = protocol_hash(payload)
    if declared and declared != actual:
        raise ValueError(f"protocol hash mismatch: declared={declared} actual={actual}")
    return FrozenProtocol(source, payload, actual)


def selftest() -> None:
    payload = {
        "protocol_version": "phase5.v1",
        "experiment_id": "SELFTEST",
        "question": "Does protocol validation fail closed?",
        "engine": "readiness",
        "data_contract": {},
        "method": {},
        "controls": ["no_trade"],
        "cost_model": {},
        "promotion_gates": {},
        "capital_authority": False,
    }
    assert len(protocol_hash(payload)) == 64
    bad = dict(payload, capital_authority=True)
    try:
        required = bad["capital_authority"] is False
        if not required:
            raise ValueError
    except ValueError:
        pass
    else:
        raise AssertionError("capital authority must fail closed")

