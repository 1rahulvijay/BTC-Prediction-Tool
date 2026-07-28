"""The four alpha families - detection only, no direction claim.

A detector answers "did this economic event occur here?", never "which way will it
go?". Direction is a separate, later model, conditional on the movement-above-cost
gate. Keeping them apart is deliberate: PROFIT_CAMPAIGN_V1 asked one model to pick a
side at every 15s timestamp and the cost structure made ~97.5% of those timestamps
unwinnable before the model was consulted.

Every detector FAILS CLOSED. A missing input yields a candidate marked MISSING with
the input named. No detector substitutes a proxy, imputes zero, or drops the term.
"""
from __future__ import annotations

from .contracts import DataQuality, EventCandidate, Family

# Thresholds are preregistered here and are part of the frozen protocol surface.
# Changing one after the untouched period opens is prohibited.
LIQ_NOTIONAL_USD_MIN = 250_000.0     # "large" forced liquidation
FLOW_CONFIRM_RATIO = 1.5             # same-direction aggressive flow vs trailing median
DEPTH_DEPLETION_RATIO = 0.60         # opposing depth vs its trailing median
VOL_EXPANSION_RATIO = 1.25           # realized vol vs trailing median
IMPACT_DECAY_RATIO = 0.50            # exhaustion: impact per unit vs earlier in the burst
REPLENISH_RATIO = 1.20               # opposing book rebuilding
LEAD_LAG_MIN_BPS = 3.0               # other venue has moved this far
LAG_UNREPRICED_FRACTION = 0.50       # and perp has repriced less than this share of it
OI_DISLOCATION_PCT = 0.010           # 1% OI change over the window
BASIS_VELOCITY_BPS_MIN = 2.0


def _missing(required: dict[str, float | None]) -> tuple[str, ...]:
    return tuple(k for k, v in required.items() if v is None)


def _candidate(family: Family, ts_ms: int, required: dict[str, float | None],
               features: dict[str, float], fired: bool,
               reasons: tuple[str, ...] = ()) -> EventCandidate | None:
    missing = _missing(required)
    if missing:
        return EventCandidate(
            family=family, ts_ms=ts_ms, features=features,
            required_inputs=tuple(required), missing_inputs=missing,
            data_quality=DataQuality.MISSING,
            reasons=("MISSING_REQUIRED_INPUT",) + reasons,
        )
    if not fired:
        return None
    return EventCandidate(
        family=family, ts_ms=ts_ms, features=features,
        required_inputs=tuple(required), missing_inputs=(),
        data_quality=DataQuality.OK, reasons=reasons,
    )


def detect_liquidation_continuation(
    ts_ms: int, *, liq_notional_usd: float | None, liq_side: str | None,
    aggressive_flow_ratio: float | None, opposing_depth_ratio: float | None,
    vol_expansion_ratio: float | None,
) -> EventCandidate | None:
    """Large forced liquidation + same-direction aggressive flow + opposing depth
    depletion + widening volatility."""
    required = {
        "liq_notional_usd": liq_notional_usd,
        "liq_side": 1.0 if liq_side else None,
        "aggressive_flow_ratio": aggressive_flow_ratio,
        "opposing_depth_ratio": opposing_depth_ratio,
        "vol_expansion_ratio": vol_expansion_ratio,
    }
    fired = bool(
        liq_notional_usd is not None and liq_notional_usd >= LIQ_NOTIONAL_USD_MIN
        and aggressive_flow_ratio is not None and aggressive_flow_ratio >= FLOW_CONFIRM_RATIO
        and opposing_depth_ratio is not None and opposing_depth_ratio <= DEPTH_DEPLETION_RATIO
        and vol_expansion_ratio is not None and vol_expansion_ratio >= VOL_EXPANSION_RATIO
    )
    feats = {k: v for k, v in {
        "liq_notional_usd": liq_notional_usd, "aggressive_flow_ratio": aggressive_flow_ratio,
        "opposing_depth_ratio": opposing_depth_ratio, "vol_expansion_ratio": vol_expansion_ratio,
    }.items() if v is not None}
    return _candidate(Family.LIQUIDATION_CONTINUATION, ts_ms, required, feats, fired)


def detect_liquidation_exhaustion(
    ts_ms: int, *, liq_notional_usd: float | None, impact_decay_ratio: float | None,
    opposing_replenish_ratio: float | None, flow_decay_ratio: float | None,
) -> EventCandidate | None:
    """Large forced liquidation + diminishing price impact + opposing-book
    replenishment + aggressive-flow decay."""
    required = {
        "liq_notional_usd": liq_notional_usd,
        "impact_decay_ratio": impact_decay_ratio,
        "opposing_replenish_ratio": opposing_replenish_ratio,
        "flow_decay_ratio": flow_decay_ratio,
    }
    fired = bool(
        liq_notional_usd is not None and liq_notional_usd >= LIQ_NOTIONAL_USD_MIN
        and impact_decay_ratio is not None and impact_decay_ratio <= IMPACT_DECAY_RATIO
        and opposing_replenish_ratio is not None and opposing_replenish_ratio >= REPLENISH_RATIO
        and flow_decay_ratio is not None and flow_decay_ratio <= IMPACT_DECAY_RATIO
    )
    feats = {k: v for k, v in {
        "liq_notional_usd": liq_notional_usd, "impact_decay_ratio": impact_decay_ratio,
        "opposing_replenish_ratio": opposing_replenish_ratio, "flow_decay_ratio": flow_decay_ratio,
    }.items() if v is not None}
    return _candidate(Family.LIQUIDATION_EXHAUSTION, ts_ms, required, feats, fired)


def detect_cross_venue_lead_lag(
    ts_ms: int, *, leader_move_bps: float | None, perp_move_bps: float | None,
    leader_venue: str | None,
) -> EventCandidate | None:
    """Another venue has moved and the perp has not yet repriced the majority of it.

    Note the ordering requirement is enforced by the caller's causal windowing: both
    moves must be measured over windows ending at or before ts_ms.
    """
    required = {
        "leader_move_bps": leader_move_bps,
        "perp_move_bps": perp_move_bps,
        "leader_venue": 1.0 if leader_venue else None,
    }
    fired = False
    if leader_move_bps is not None and perp_move_bps is not None:
        same_dir = (leader_move_bps > 0) == (perp_move_bps >= 0)
        repriced = abs(perp_move_bps) / abs(leader_move_bps) if leader_move_bps else 1.0
        fired = (abs(leader_move_bps) >= LEAD_LAG_MIN_BPS
                 and (not same_dir or repriced < LAG_UNREPRICED_FRACTION))
    feats = {k: v for k, v in {
        "leader_move_bps": leader_move_bps, "perp_move_bps": perp_move_bps,
    }.items() if v is not None}
    return _candidate(Family.CROSS_VENUE_LEAD_LAG, ts_ms, required, feats, fired)


def detect_funding_basis_oi(
    ts_ms: int, *, price_change_bps: float | None, oi_change_pct: float | None,
    basis_velocity_bps: float | None, funding_rate: float | None,
    seconds_to_funding: float | None,
) -> EventCandidate | None:
    """Price/OI quadrant conditioned on basis velocity and funding proximity.

    The quadrant itself is a feature, not a signal: price-up/OI-up (new longs) and
    price-up/OI-down (short covering) are different economic states and are recorded
    separately so a later model can condition on them rather than average them.
    """
    required = {
        "price_change_bps": price_change_bps,
        "oi_change_pct": oi_change_pct,
        "basis_velocity_bps": basis_velocity_bps,
        "funding_rate": funding_rate,
        "seconds_to_funding": seconds_to_funding,
    }
    fired = bool(
        oi_change_pct is not None and abs(oi_change_pct) >= OI_DISLOCATION_PCT
        and basis_velocity_bps is not None and abs(basis_velocity_bps) >= BASIS_VELOCITY_BPS_MIN
    )
    feats: dict[str, float] = {}
    if price_change_bps is not None and oi_change_pct is not None:
        feats["quadrant"] = float(
            (1 if price_change_bps >= 0 else 0) * 2 + (1 if oi_change_pct >= 0 else 0)
        )  # 3=up/up 2=up/down 1=down/up 0=down/down
    for k, v in {
        "price_change_bps": price_change_bps, "oi_change_pct": oi_change_pct,
        "basis_velocity_bps": basis_velocity_bps, "funding_rate": funding_rate,
        "seconds_to_funding": seconds_to_funding,
    }.items():
        if v is not None:
            feats[k] = v
    return _candidate(Family.FUNDING_BASIS_OI, ts_ms, required, feats, fired)


DETECTORS = {
    Family.LIQUIDATION_CONTINUATION: detect_liquidation_continuation,
    Family.LIQUIDATION_EXHAUSTION: detect_liquidation_exhaustion,
    Family.CROSS_VENUE_LEAD_LAG: detect_cross_venue_lead_lag,
    Family.FUNDING_BASIS_OI: detect_funding_basis_oi,
}
