"""Frozen contracts and shared paths for PROFIT_CAMPAIGN_V1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "research" / "profit_campaign_v1"
DEFAULT_INPUT_ROOT = ROOT / "data" / "profit_campaign_inputs"
DEFAULT_BINANCE_ARCHIVE = ROOT / "Kaggle Data" / "archive (5).zip"
DEFAULT_FUNDING_ARCHIVE = ROOT / "Kaggle Data" / "archive (4).zip"

CAMPAIGN_IDS = (
    "BINANCE_COST_AWARE_NET_PNL_V1",
    "BINANCE_DYNAMIC_EXIT_V1",
)


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Protocol:
    raw: dict[str, Any]
    sha256: str

    @classmethod
    def load(cls, path: Path = PROTOCOL_PATH) -> "Protocol":
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        if raw.get("research_only") is not True:
            raise ValueError("profit campaign must remain research-only")
        if tuple(raw.get("campaigns", ())) != CAMPAIGN_IDS:
            raise ValueError("frozen campaign identity mismatch")
        if raw.get("prohibitions") is None:
            raise ValueError("frozen protocol is missing prohibitions")
        return cls(raw=raw, sha256=hashlib.sha256(raw_bytes).hexdigest())

    @property
    def horizons(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.raw["horizons_seconds"])

    @property
    def latencies(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.raw["latencies_ms"])

    @property
    def capital_sizes(self) -> tuple[float, ...]:
        return tuple(float(value) for value in self.raw["capital_usd"])

    @property
    def primary_latency_ms(self) -> int:
        return int(self.raw["primary_latency_ms"])

    @property
    def primary_capital_usd(self) -> float:
        return float(self.raw["primary_capital_usd"])

    @property
    def fee_bps(self) -> float:
        return float(self.raw["execution"]["taker_fee_bps_per_leg"])

    @property
    def impact_bps(self) -> float:
        return float(self.raw["execution"]["additional_impact_bps_per_leg"])

    @property
    def decision_interval_seconds(self) -> int:
        return int(self.raw["decision_interval_seconds"])

    @property
    def random_seed(self) -> int:
        return int(self.raw["validation"]["random_seed"])


def protocol_manifest(protocol: Protocol, source_paths: list[Path]) -> dict[str, Any]:
    sources = []
    for path in source_paths:
        if path.exists():
            sources.append(
                {
                    "path": str(path.resolve()),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        else:
            sources.append(
                {
                    "path": str(path.resolve()),
                    "bytes": None,
                    "sha256": None,
                }
            )
    return {
        "protocol_id": protocol.raw["protocol_id"],
        "protocol_sha256": protocol.sha256,
        "implementation_sha256": implementation_sha256(),
        "research_only": True,
        "sources": sources,
    }
