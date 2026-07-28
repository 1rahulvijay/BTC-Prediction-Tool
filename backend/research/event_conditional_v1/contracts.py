"""BINANCE_EVENT_CONDITIONAL_PROFIT_V1 - typed contracts and frozen-protocol loader.

Everything downstream speaks these types. There is no free-form string decision API:
an action is an Action, a fill standard is a FillStandard, and a missing input produces
DataQuality.MISSING rather than a zero.

PROFIT_CAMPAIGN_V1 is frozen and is never imported, altered, or rerun from here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = ROOT / "data" / "research" / "event_conditional_v1"


class Family(str, Enum):
    LIQUIDATION_CONTINUATION = "LIQUIDATION_CONTINUATION"
    LIQUIDATION_EXHAUSTION = "LIQUIDATION_EXHAUSTION"
    CROSS_VENUE_LEAD_LAG = "CROSS_VENUE_LEAD_LAG"
    FUNDING_BASIS_OI = "FUNDING_BASIS_OI"


class Action(str, Enum):
    MAKER_LONG = "MAKER_LONG"
    MAKER_SHORT = "MAKER_SHORT"
    TAKER_LONG = "TAKER_LONG"
    TAKER_SHORT = "TAKER_SHORT"
    WAIT = "WAIT"

    @property
    def is_maker(self) -> bool:
        return self in (Action.MAKER_LONG, Action.MAKER_SHORT)

    @property
    def is_long(self) -> bool:
        return self in (Action.MAKER_LONG, Action.TAKER_LONG)


class FillStandard(str, Enum):
    """How a maker fill was established. Only two of these can support promotion."""
    TOUCH_PROXY = "TOUCH_PROXY"          # diagnostic only - never promotable
    TRADE_THROUGH = "TRADE_THROUGH"      # minimum credible
    QUEUE_ESTIMATED = "QUEUE_ESTIMATED"  # promotion quality


class DataQuality(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    GAP_SEGMENTED = "GAP_SEGMENTED"      # candidate sits across a recorder gap
    SEQUENCE_BROKEN = "SEQUENCE_BROKEN"


class Reason(str, Enum):
    NO_EVENT = "NO_EVENT"
    MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"
    STALE_INPUT = "STALE_INPUT"
    ACROSS_RECORDER_GAP = "ACROSS_RECORDER_GAP"
    HORIZON_NOT_ADMISSIBLE = "HORIZON_NOT_ADMISSIBLE"
    BELOW_VIABILITY_FLOOR = "BELOW_VIABILITY_FLOOR"
    NO_POSITIVE_ACTION = "NO_POSITIVE_ACTION"
    TOUCH_PROXY_ONLY = "TOUCH_PROXY_ONLY"


@dataclass(frozen=True, slots=True)
class Protocol:
    raw: dict[str, Any]

    @property
    def protocol_id(self) -> str:
        return self.raw["protocol_id"]

    @property
    def families(self) -> tuple[Family, ...]:
        return tuple(Family(f) for f in self.raw["alpha_families"])

    @property
    def taker_round_trip_bps(self) -> float:
        return float(self.raw["execution"]["taker_round_trip_bps"])

    @property
    def maker_round_trip_bps(self) -> float:
        return float(self.raw["execution"]["maker_round_trip_bps"])

    @property
    def viability_floor(self) -> float:
        return float(self.raw["horizon_viability_gate"]["minimum_clearance_lb95"])

    @property
    def clearance_points(self) -> dict[str, dict[str, float]]:
        """Point-estimate clearance rates. Reporting only - admission uses LB95."""
        return (self.raw["horizon_viability_gate"]["measured_evidence"]
                ["clearance_point_by_horizon_s"])

    def admissible_horizons(self, maker: bool) -> tuple[int, ...]:
        key = "maker" if maker else "taker"
        return tuple(int(h) for h in self.raw["admissible_horizons_seconds"][key])

    def selected_horizons(self, maker: bool) -> tuple[int, ...]:
        """The capped grid actually used. A subset of admissible_horizons."""
        key = "maker" if maker else "taker"
        return tuple(int(h) for h in self.raw["selected_horizons_seconds"][key])

    def sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(self.raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_protocol(path: Path | None = None) -> Protocol:
    with open(path or PROTOCOL_PATH, encoding="utf-8") as fh:
        return Protocol(json.load(fh))


@dataclass(slots=True)
class EventCandidate:
    """One detected economic event. Detection is causal: it may use only data at or
    before `ts_ms`, and carries the provenance needed to prove that later."""
    family: Family
    ts_ms: int
    symbol: str = "BTCUSDT"
    features: dict[str, float] = field(default_factory=dict)
    required_inputs: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    data_quality: DataQuality = DataQuality.OK
    segment_id: str = ""            # recorder-continuity segment; never spans a gap
    reasons: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.data_quality == DataQuality.OK and not self.missing_inputs


@dataclass(slots=True)
class ActionOutcome:
    """Executable economics of ONE action on ONE event at ONE horizon."""
    action: Action
    horizon_s: int
    filled: bool
    fill_standard: FillStandard | None
    entry_price: float | None
    exit_price: float | None
    quantity: float
    notional_usd: float
    gross_pnl_usd: float
    fee_usd: float
    slippage_usd: float
    net_pnl_usd: float
    holding_time_s: float
    adverse_selection_bps: float = 0.0
    missed_fill_opportunity_usd: float = 0.0
    # SAFETY DEFAULT: not promotable until something proves it is. A new fill standard, an
    # unfinished execution path, or a forgotten assignment must fail CLOSED. Promotion is
    # an earned property, never an inherited one.
    promotable: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        d["fill_standard"] = self.fill_standard.value if self.fill_standard else None
        return d


@dataclass(slots=True)
class EventDecision:
    """The campaign's output for one event: every action priced, one selected.

    WAIT is the default and is always present with net PnL exactly 0.0, so
    'no action beat doing nothing' is a first-class, recorded outcome rather
    than an absence of rows.
    """
    candidate: EventCandidate
    outcomes: list[ActionOutcome]
    selected: Action = Action.WAIT
    selection_reason: str = Reason.NO_POSITIVE_ACTION.value

    def __post_init__(self) -> None:
        """The canonical WAIT row is ENFORCED, not merely documented.

        Without this, a decision could omit WAIT and the ledger would silently lose the
        only unbiased benchmark every action must beat. Absence must fail closed.
        """
        waits = [o for o in self.outcomes if o.action is Action.WAIT]
        if len(waits) != 1:
            raise ValueError(
                f"EventDecision requires exactly ONE WAIT outcome, found {len(waits)}. "
                f"WAIT is the benchmark; a decision without it cannot be scored."
            )
        w = waits[0]
        bad = [
            f"filled={w.filled}" if w.filled else "",
            f"quantity={w.quantity}" if w.quantity != 0.0 else "",
            f"gross={w.gross_pnl_usd}" if w.gross_pnl_usd != 0.0 else "",
            f"fee={w.fee_usd}" if w.fee_usd != 0.0 else "",
            f"slippage={w.slippage_usd}" if w.slippage_usd != 0.0 else "",
            f"net={w.net_pnl_usd}" if w.net_pnl_usd != 0.0 else "",
            "promotable=True" if w.promotable else "",
        ]
        bad = [b for b in bad if b]
        if bad:
            raise ValueError(
                "the WAIT outcome must be exactly zero and non-promotable; got " + ", ".join(bad)
            )
        if self.selected is not Action.WAIT and self.selected not in {o.action for o in self.outcomes}:
            raise ValueError(f"selected={self.selected.value} has no priced outcome")

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.candidate.family.value,
            "ts_ms": self.candidate.ts_ms,
            "segment_id": self.candidate.segment_id,
            "data_quality": self.candidate.data_quality.value,
            "missing_inputs": list(self.candidate.missing_inputs),
            "selected": self.selected.value,
            "selection_reason": self.selection_reason,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }
