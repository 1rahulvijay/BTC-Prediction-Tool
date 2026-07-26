"""Frozen declarations and validation for COMPLETE_TRADE_FORECAST_V1."""
from __future__ import annotations

import hashlib
import json
from typing import Any


CONFIG_VERSION = "2026-07-26-complete-trade-m0-v2"
MODE = "SHADOW_PILOT_ONLY"

HORIZONS = (5, 15)
ENTRY_CHECKPOINTS_S = {
    5: (240, 180, 120, 90, 60, 30),
    15: (720, 600, 480, 360, 240, 180, 120, 90, 60, 30),
}
FUTURE_OFFSETS_S = (5, 10, 15, 30, 60, 120)

# Entry checkpoints go down to 30s while FUTURE_OFFSETS_S reaches 120s, so most offsets are
# unreachable at the late checkpoints. A target beyond expiry is not a hard target - the contract
# has settled and the information could never have been traded on.
MAX_ENTRY_CHECKPOINT_S = max(max(v) for v in ENTRY_CHECKPOINTS_S.values())


def target_offset_valid(offset_seconds: float, seconds_left: float) -> bool:
    """True when a future target at `offset_seconds` lands before this round expires.

    The single definition of target validity, shared by the dataset builder (which NULLs invalid
    targets) and by serving (which must not display or act on an offset the round cannot reach).
    Keeping one function means the two can never drift apart, which is the failure that let
    post-expiry BTC information into training in the first place."""
    return float(offset_seconds) <= float(seconds_left)


# Settlement provenance. FROZEN ALLOWLIST rather than a `LIKE 'official:%'` prefix match: the
# settlement parquet stores bare venue values ('polymarket_clob', 'polymarket_gamma') and the
# `official:` prefix is applied downstream in database.py, so a prefix match silently selects
# ZERO rows and yields an empty dataset that still looks well-formed. Both forms are accepted so
# the same gate works against either source.
OFFICIAL_RESOLUTION_SOURCES = (
    "polymarket_clob",
    "polymarket_gamma",
    "official:polymarket_clob",
    "official:polymarket_gamma",
)
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
QUANTITIES = (1, 5, 10, 25, 50, 100)
ENTRY_LATENCY_MS = 500
# A quote "survived" if arrival is no worse than the decision VWAP plus this tolerance. One tick.
# Anything looser would let a materially worse fill count as survival; anything tighter would call
# ordinary sub-tick rounding a failure.
QUOTE_SURVIVAL_TOLERANCE = 0.01
M0_STRESS_LATENCY_MS = 1000
MAX_DECISION_BOOK_AGE_S = 5.0
MAX_BTC_OBSERVATION_AGE_S = 10.0
MAX_FUTURE_OBSERVATION_LAG_S = 10.0
# How far a lookback observation may sit from its intended point before the derived value stops
# being what its name says. A "30s return" computed against a 6-minute-old print is mislabelled,
# not merely noisy, so it becomes NULL and invalidates the candidate.
MAX_LOOKBACK_ERROR_S = 5.0
# Relative barriers keep the event definition stable as BTC's dollar price changes.
# Approximately $30 at $60k for 5m and $60 at $60k for 15m.
BTC_TOUCH_BPS = {5: 5.0, 15: 10.0}

EXIT_PLANS = (
    "HOLD_TO_SETTLEMENT",
    "TAKE_1C",
    "TAKE_3C",
    "TAKE_5C",
    "TAKE_3C_OR_STOP_3C",
    "TIME_EXIT_15S",
    "TIME_EXIT_30S",
    "TIME_EXIT_60S",
    "BREAK_EVEN_LOCK_AFTER_3C",
)

# ===================================================================================
# COMPLETE_TRADE_M0_V2 - THE SINGLE SOURCE OF TRUTH
# ===================================================================================
# Every executable gate is generated from this object and folded into policy_hash(), so the code
# and PREREG_COMPLETE_TRADE_M0_V2.md cannot drift apart. Previously the prereg demanded 1,000
# forward rounds and 8 forward weeks while the code enforced 500 rounds and 2 test weeks - the
# document and the gate disagreed, and the gate is what actually runs.
M0_V2 = {
    "prereg": "PREREG_COMPLETE_TRADE_M0_V2.md",
    "prereg_sha256": "138616d3893c5034bddd29be562f73c452e16f570af67ffcb1adda209df793a5",
    "clarification_001_sha256": "920352412cb715d1786e45f97eec5460d1fc09b216015507eb9a200221107996",
    # Buckets are DIAGNOSTIC ONLY under clarification 001; the executable rule is an
    # absolute frozen threshold with causal first-qualifying entry.
    "bucket_role": "DIAGNOSTIC_ONLY",
    "matched_random_pool": "SAME_CHECKPOINT_CANDIDATES_ONLY",
    "min_selected_trades": 100,
    "frozen_at": "2026-07-26",
    # Epoch seconds for the freeze boundary. Forward evidence must post-date this.
    "frozen_at_s": 1785110400.0,
    "primary_plan": "TAKE_3C_OR_STOP_3C",
    "score_label": "plan_take_3c_or_stop_3c_profitable",
    "realized_column": "plan_take_3c_or_stop_3c_net",
    "independent_unit": "round_id",
    # Causal: walk checkpoints earliest -> latest, take the FIRST candidate clearing an absolute
    # threshold frozen before the evidence period. Never the round-wide maximum (hindsight), and
    # never a quantile of the evaluation period's own score distribution.
    "checkpoint_policy": "FIRST_QUALIFYING_EARLIEST_TO_LATEST",
    "threshold_source": "CALIBRATION_ONLY_FROZEN_BEFORE_EVIDENCE",
    "target_entry_rate": 0.20,
    # Promotion thresholds - these are the numbers the preregistration states.
    "min_forward_rounds": 1000,
    "min_forward_weeks": 8,
    "min_profit_factor": 1.20,
    "day_block_lb_min": 0.0,
    "max_hour_profit_share": 0.50,
    "max_week_profit_share": 0.50,
    "matched_random_control": True,
    "multiplicity_procedure": "benjamini_hochberg",
    "multiplicity_q": 0.10,
    "require_latency_stress_survival": True,
    "stress_latency_ms": 1000,
    "buckets": 5,
    # Forward isolation: promotion evidence must post-date BOTH freezes.
    "require_zero_pre_freeze_rows": True,
    "require_single_model_hash": True,
    "require_single_policy_hash": True,
}

# Executable gate, generated from M0_V2 so the two can never disagree.
PROMOTION_GATE = {
    "min_independent_rounds": M0_V2["min_forward_rounds"],
    "min_calendar_weeks": M0_V2["min_forward_weeks"],
    "m0_quantiles": M0_V2["buckets"],
    "m0_q5_day_block_lb_min": M0_V2["day_block_lb_min"],
    "m0_q5_minus_q3_min": 0.005,
    "m0_require_broad_monotonicity": True,
    "m0_require_week_stability": True,
    "m0_require_fee_and_latency_survival": M0_V2["require_latency_stress_survival"],
    "m0_min_test_weeks": M0_V2["min_forward_weeks"],
    "m0_max_single_hour_share": M0_V2["max_hour_profit_share"],
    "m0_max_single_week_share": M0_V2["max_week_profit_share"],
    "m0_min_profit_factor": M0_V2["min_profit_factor"],
    "m0_require_matched_random_control": M0_V2["matched_random_control"],
    "m0_multiplicity_procedure": M0_V2["multiplicity_procedure"],
    "m0_multiplicity_q": M0_V2["multiplicity_q"],
}

FEATURE_COLUMNS = (
    "horizon",
    "seconds_left",
    "seconds_elapsed",
    "requested_qty",
    "side_up",
    "side_is_leader",
    "current_btc",
    "anchor_price",
    "distance_usd_side",
    "distance_bps_side",
    "abs_distance_bps",
    "btc_return_5s_bps",
    "btc_return_15s_bps",
    "btc_return_30s_bps",
    "btc_return_60s_bps",
    "btc_vol_60s_pct",
    "p_hold_side",
    "own_bid",
    "own_ask",
    "own_spread",
    "own_bid_size",
    "own_ask_size",
    "own_bid_depth",
    "own_ask_depth",
    "own_bid_levels",
    "own_ask_levels",
    "opp_bid",
    "opp_ask",
    "opp_spread",
    "opp_bid_size",
    "opp_ask_size",
    "contract_bid_velocity_5s",
    "contract_bid_velocity_15s",
    "contract_bid_velocity_30s",
    "btc_share_sensitivity_30s",
    "top_imbalance",
    "depth_imbalance",
    "decision_quote_age_s",
)

# Generic BTC path head uses one canonical UP-side/1-share row per checkpoint.
# Quantity and trade-side orientation are intentionally excluded.
BTC_FEATURE_COLUMNS = (
    "horizon",
    "seconds_left",
    "seconds_elapsed",
    "current_btc",
    "anchor_price",
    "distance_usd_side",
    "distance_bps_side",
    "abs_distance_bps",
    "btc_return_5s_bps",
    "btc_return_15s_bps",
    "btc_return_30s_bps",
    "btc_return_60s_bps",
    "btc_vol_60s_pct",
    "p_hold_side",
    "own_bid",
    "own_ask",
    "own_spread",
    "own_bid_size",
    "own_ask_size",
    "own_bid_depth",
    "own_ask_depth",
    "opp_bid",
    "opp_ask",
    "opp_spread",
    "top_imbalance",
    "depth_imbalance",
    "decision_quote_age_s",
)

CLASSIFICATION_TARGETS = (
    "entry_complete",
    "label_ever_profitable",
    "label_stays_profitable_to_settlement",
    "label_lockable_1c",
    "label_take_1c_before_stop_3c",
    "label_take_3c_before_stop_3c",
    "label_take_5c_before_stop_5c",
    "label_settlement_win",
    # Exact per-plan economics: sign of the plan's realized net PnL. This is the head M0 ranks on;
    # the barrier-event labels above remain available as diagnostics.
    "plan_take_3c_or_stop_3c_profitable",
    "plan_hold_to_settlement_profitable",
)
CROSSING_TARGETS = tuple(
    f"label_{event}_by_{offset}s"
    for offset in FUTURE_OFFSETS_S
    for event in ("break_even", "target_3c", "stop_3c")
)

SHARE_SUMMARY_TARGETS = (
    "actual_mfe",
    "actual_mae",
    "actual_first_profitable_s",
)


def frozen_config() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "mode": MODE,
        "horizons": list(HORIZONS),
        "entry_checkpoints_s": {
            str(key): list(value) for key, value in ENTRY_CHECKPOINTS_S.items()
        },
        "future_offsets_s": list(FUTURE_OFFSETS_S),
        "quantiles": list(QUANTILES),
        "quantities": list(QUANTITIES),
        "entry_latency_ms": ENTRY_LATENCY_MS,
        "m0_stress_latency_ms": M0_STRESS_LATENCY_MS,
        "max_decision_book_age_s": MAX_DECISION_BOOK_AGE_S,
        "max_btc_observation_age_s": MAX_BTC_OBSERVATION_AGE_S,
        "max_future_observation_lag_s": MAX_FUTURE_OBSERVATION_LAG_S,
        "btc_touch_bps": {str(key): value for key, value in BTC_TOUCH_BPS.items()},
        "exit_plans": list(EXIT_PLANS),
        "promotion_gate": PROMOTION_GATE,
        "m0_v2": M0_V2,
        "feature_columns": list(FEATURE_COLUMNS),
        "btc_feature_columns": list(BTC_FEATURE_COLUMNS),
        "classification_targets": list(CLASSIFICATION_TARGETS),
        "crossing_targets": list(CROSSING_TARGETS),
        "share_summary_targets": list(SHARE_SUMMARY_TARGETS),
    }


def policy_hash() -> str:
    raw = json.dumps(frozen_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_candidate(candidate: dict[str, Any]) -> list[str]:
    """Return all fail-closed reasons for a canonical candidate snapshot."""
    reasons: list[str] = []
    if int(candidate.get("horizon") or 0) not in HORIZONS:
        reasons.append("unsupported_horizon")
    if str(candidate.get("side") or "") not in ("UP", "DOWN"):
        reasons.append("invalid_side")
    if float(candidate.get("requested_qty") or 0.0) <= 0.0:
        reasons.append("invalid_quantity")
    if float(candidate.get("seconds_left") or -1.0) < 0.0:
        reasons.append("round_expired")
    if not candidate.get("round_id"):
        reasons.append("missing_round_id")
    if not candidate.get("decision_ts_ns"):
        reasons.append("missing_decision_timestamp")
    for field in ("anchor_price", "current_btc", "own_bid", "own_ask"):
        value = candidate.get(field)
        try:
            valid = value is not None and float(value) > 0.0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            reasons.append(f"missing_{field}")
    try:
        if float(candidate.get("own_bid")) > float(candidate.get("own_ask")):
            reasons.append("crossed_book")
    except (TypeError, ValueError):
        pass
    if float(candidate.get("decision_quote_age_s") or 0.0) > MAX_DECISION_BOOK_AGE_S:
        reasons.append("stale_decision_book")
    return reasons


def selftest() -> None:
    assert len(policy_hash()) == 64
    valid = {
        "horizon": 5,
        "side": "UP",
        "requested_qty": 10,
        "seconds_left": 60,
        "round_id": "round",
        "decision_ts_ns": 1,
        "anchor_price": 100_000,
        "current_btc": 100_020,
        "own_bid": 0.55,
        "own_ask": 0.56,
        "decision_quote_age_s": 0.1,
    }
    assert validate_candidate(valid) == []
    assert "crossed_book" in validate_candidate({**valid, "own_bid": 0.60})
    print("trade_schema self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
