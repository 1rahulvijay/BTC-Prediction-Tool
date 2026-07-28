"""Canonical target catalog for every requested evidence adapter."""

from __future__ import annotations

from dataclasses import dataclass

from backend.quant_platform.forecast_ledger import EvidenceKind
from backend.quant_platform.model_roles import ModelRole, TargetContract


@dataclass(frozen=True, slots=True)
class TargetSpec:
    adapter_id: str
    source_campaign: str
    source_head: str
    model_id: str
    model_version: str
    contract: TargetContract
    evidence_kind: EvidenceKind = EvidenceKind.FORWARD
    adapter_implemented: bool = True
    static_blocker: str = ""


def _contract(
    target_name: str,
    role: ModelRole,
    venue: str,
    instrument: str,
    horizon_seconds: int,
    outcome_semantics: str,
) -> TargetContract:
    return TargetContract(
        target_name=target_name,
        role=role,
        venue=venue,
        instrument=instrument,
        horizon_seconds=horizon_seconds,
        outcome_semantics=outcome_semantics,
    )


POLY_1H_SETTLEMENT = _contract(
    "polymarket_btc_1h_up_settlement",
    ModelRole.SETTLEMENT,
    "POLYMARKET",
    "BTC_UP_DOWN_1H",
    3600,
    "1 when finalized Binance BTCUSDT 1h close is greater than or equal "
    "to its open and Polymarket resolves to the same side",
)


def _repricing(side: str, horizon: int) -> TargetContract:
    return _contract(
        f"polymarket_{side.lower()}_ask_worsens_1c_within_{horizon}s",
        ModelRole.REPRICING,
        "POLYMARKET",
        "BTC_UP_DOWN_5M_SELECTED_TOKEN",
        horizon,
        f"1 when the selected {side} token ask at the first valid "
        f"{horizon}s checkpoint is at least 0.01 above its decision ask",
    )


def _binance_event(name: str, role: ModelRole, horizon: int, semantics: str):
    return _contract(
        f"binance_spot_{name}_{horizon}s",
        role,
        "BINANCE_SPOT",
        "BTCUSDT",
        horizon,
        semantics,
    )


TARGET_SPECS: tuple[TargetSpec, ...] = (
    TargetSpec(
        "poly_1h_market_prior",
        "POLY_1H_DIGITAL_FAIR_VALUE_V1",
        "p_a_market",
        "poly-1h-market-prior",
        "analytic-v1",
        POLY_1H_SETTLEMENT,
    ),
    TargetSpec(
        "poly_1h_distance_time",
        "POLY_1H_DIGITAL_FAIR_VALUE_V1",
        "p_b_distance_time",
        "poly-1h-distance-time",
        "analytic-v1",
        POLY_1H_SETTLEMENT,
    ),
    TargetSpec(
        "poly_1h_volatility_mixture",
        "POLY_1H_DIGITAL_FAIR_VALUE_V1",
        "p_c_volatility_mixture",
        "poly-1h-volatility-mixture",
        "analytic-v1",
        POLY_1H_SETTLEMENT,
    ),
    *tuple(
        TargetSpec(
            f"repricing_{side.lower()}_5s_{family}",
            "POLYMARKET_REPRICING_SHADOW_V1",
            (
                f"{side.lower()}_baseline_worsening_probability"
                if family == "baseline"
                else f"{side.lower()}_worsening_probability"
            ),
            f"repricing-{side.lower()}-{family}",
            "contract-v1",
            _repricing(side, 5),
        )
        for side in ("UP", "DOWN")
        for family in ("baseline", "evidence")
    ),
    *tuple(
        TargetSpec(
            f"repricing_{side.lower()}_15s_evidence",
            "POLYMARKET_REPRICING_SHADOW_V1",
            f"{side.lower()}_worsening_probability_15s",
            f"repricing-{side.lower()}-evidence-15s",
            "not-built",
            _repricing(side, 15),
            adapter_implemented=False,
            static_blocker="SOURCE_HEAD_NOT_IMPLEMENTED",
        )
        for side in ("UP", "DOWN")
    ),
    TargetSpec(
        "poly_maker_fill_2s",
        "POLYMARKET_REPRICING_SHADOW_V1",
        "maker_fill_within_2s",
        "poly-maker-fill-2s",
        "not-built",
        _contract(
            "polymarket_maker_fill_within_2s",
            ModelRole.FILL,
            "POLYMARKET",
            "BTC_UP_DOWN_5M_SELECTED_TOKEN",
            2,
            "1 under the frozen queue-authoritative maker fill definition",
        ),
        adapter_implemented=False,
        static_blocker="QUEUE_AUTHORITATIVE_FORECAST_NOT_IMPLEMENTED",
    ),
    TargetSpec(
        "poly_maker_fill_5s",
        "POLYMARKET_REPRICING_SHADOW_V1",
        "maker_fill_within_5s",
        "poly-maker-fill-5s",
        "not-built",
        _contract(
            "polymarket_maker_fill_within_5s",
            ModelRole.FILL,
            "POLYMARKET",
            "BTC_UP_DOWN_5M_SELECTED_TOKEN",
            5,
            "1 under the frozen queue-authoritative maker fill definition",
        ),
        adapter_implemented=False,
        static_blocker="QUEUE_AUTHORITATIVE_FORECAST_NOT_IMPLEMENTED",
    ),
    TargetSpec(
        "poly_maker_toxicity_5s",
        "POLYMARKET_REPRICING_SHADOW_V1",
        "maker_adverse_selection_5s",
        "poly-maker-toxicity-5s",
        "not-built",
        _contract(
            "polymarket_maker_adverse_selection_5s",
            ModelRole.TOXICITY,
            "POLYMARKET",
            "BTC_UP_DOWN_5M_SELECTED_TOKEN",
            5,
            "signed markout after a defensible maker fill is adverse at 5s",
        ),
        adapter_implemented=False,
        static_blocker="POST_FILL_TOXICITY_FORECAST_NOT_IMPLEMENTED",
    ),
    TargetSpec(
        "poly_taker_entry_cost",
        "POLYMARKET_REPRICING_SHADOW_V1",
        "taker_entry_cost",
        "poly-taker-cost",
        "not-built",
        _contract(
            "polymarket_taker_entry_cost_per_share",
            ModelRole.COST,
            "POLYMARKET",
            "BTC_UP_DOWN_5M_SELECTED_TOKEN",
            5,
            "realized taker VWAP plus fees minus decision midpoint per share",
        ),
        adapter_implemented=False,
        static_blocker="TARGET_SPEC_DEFINED_FORECAST_NOT_IMPLEMENTED",
    ),
    *tuple(
        spec
        for horizon in (5, 15)
        for spec in (
            TargetSpec(
                f"binance_event_direction_{horizon}s",
                "BINANCE_MAKER_CONVERSION_V1",
                f"p_direction_{horizon}",
                f"binance-event-direction-{horizon}s",
                "event-bundle-v1",
                _binance_event(
                    "upper_barrier_before_lower",
                    ModelRole.DIRECTION_BARRIER,
                    horizon,
                    "1 when the positive spot barrier is touched before the "
                    "negative barrier; ambiguous and no-touch paths are unresolved",
                ),
            ),
            TargetSpec(
                f"binance_event_movement_{horizon}s",
                "BINANCE_MAKER_CONVERSION_V1",
                f"p_movement_{horizon}",
                f"binance-event-movement-{horizon}s",
                "event-bundle-v1",
                _binance_event(
                    "either_barrier_touched",
                    ModelRole.MAGNITUDE,
                    horizon,
                    "1 when either frozen spot barrier is touched",
                ),
            ),
            TargetSpec(
                f"binance_event_roundtrip_{horizon}s",
                "BINANCE_MAKER_CONVERSION_V1",
                f"p_roundtrip_{horizon}",
                f"binance-event-roundtrip-{horizon}s",
                "event-bundle-v1",
                _binance_event(
                    "both_barriers_touched",
                    ModelRole.MAGNITUDE,
                    horizon,
                    "1 when both frozen positive and negative spot barriers "
                    "are touched",
                ),
            ),
        )
    ),
    *tuple(
        TargetSpec(
            f"binance_maker_{name}_{horizon}s",
            "BINANCE_MAKER_CONVERSION_V1",
            name,
            f"binance-maker-{name}-{horizon}s",
            "not-built",
            _contract(
                f"binance_usdm_{name}_{horizon}s",
                role,
                "BINANCE_USDM",
                "BTCUSDT_PERP",
                horizon,
                semantics,
            ),
            adapter_implemented=False,
            static_blocker=blocker,
        )
        for horizon in (5, 15)
        for name, role, semantics, blocker in (
            (
                "entry_fill_probability",
                ModelRole.FILL,
                "fraction of requested entry quantity filled by the frozen "
                "queue-aware simulator",
                "FILL_OUTCOMES_EXIST_SPECIALIST_FORECAST_NOT_IMPLEMENTED",
            ),
            (
                "entry_slippage_bps",
                ModelRole.COST,
                "realized signed entry slippage in basis points",
                "COST_OUTCOMES_EXIST_SPECIALIST_FORECAST_NOT_IMPLEMENTED",
            ),
            (
                "net_return_bps",
                ModelRole.RETURN,
                "after-fee route net return in basis points",
                "NET_RETURN_OUTCOMES_EXIST_SPECIALIST_FORECAST_NOT_IMPLEMENTED",
            ),
        )
    ),
    *tuple(
        TargetSpec(
            f"binance_paper_{name}",
            "BINANCE_PAPER",
            source_head,
            f"binance-paper-{name}",
            "not-built",
            _contract(
                f"binance_paper_{name}",
                role,
                "BINANCE_USDM_PAPER",
                "BTCUSDT_PERP",
                300,
                semantics,
            ),
            adapter_implemented=False,
            static_blocker=blocker,
        )
        for name, source_head, role, semantics, blocker in (
            (
                "direction",
                "strategy_confidence",
                ModelRole.DIRECTION_BARRIER,
                "profitable side at the frozen 300s evaluation horizon",
                "RULE_SCORE_IS_NOT_A_CALIBRATED_DIRECTION_PROBABILITY",
            ),
            (
                "movement_magnitude",
                "strategy_score",
                ModelRole.MAGNITUDE,
                "absolute 300s move in basis points",
                "TARGET_SPECIFIC_MAGNITUDE_FORECAST_NOT_IMPLEMENTED",
            ),
            (
                "fill_probability",
                "paper_fill_simulator",
                ModelRole.FILL,
                "fraction of requested quantity filled",
                "FILL_OUTCOME_EXISTS_PROBABILITY_FORECAST_NOT_IMPLEMENTED",
            ),
            (
                "entry_slippage",
                "paper_fill_simulator",
                ModelRole.COST,
                "entry slippage in USD",
                "COST_OUTCOME_EXISTS_FORECAST_NOT_IMPLEMENTED",
            ),
            (
                "holding_time",
                "maximum_holding_seconds",
                ModelRole.CARRY,
                "realized holding time in seconds",
                "HOLDING_TIME_LIMIT_IS_NOT_A_DISTRIBUTION_FORECAST",
            ),
            (
                "net_return",
                "expected_net_pnl_usd",
                ModelRole.RETURN,
                "after-fee paper trade net PnL in USD",
                "COMPLETE_MODEL_PROVENANCE_AND_CALIBRATION_UNAVAILABLE",
            ),
        )
    ),
)


SPEC_BY_ID = {spec.adapter_id: spec for spec in TARGET_SPECS}
if len(SPEC_BY_ID) != len(TARGET_SPECS):
    raise RuntimeError("forecast adapter IDs must be unique")


__all__ = [
    "POLY_1H_SETTLEMENT",
    "SPEC_BY_ID",
    "TARGET_SPECS",
    "TargetSpec",
]
