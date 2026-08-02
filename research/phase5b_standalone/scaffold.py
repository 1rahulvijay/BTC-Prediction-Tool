"""Generate frozen standalone packages for Phase 5B experiments 43-88."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5_standalone.common.protocol import protocol_hash

BASE_FEATURES = [
    "rv_15m", "rv_30m", "rv_60m", "rv_term", "count_accel_5m", "vol_accel",
    "vpin_15m", "vpin_30m", "compression_ratio", "range_15m", "shock_magnitude",
    "cvd_change", "cvd_1m", "cvd_5m", "large_trade_delta",
    "large_trade_imbalance", "funding_velocity", "cvd_divergence",
    "perp_spot_basis_bps", "vol_spot", "vol_perp",
]
BTC_CONTRACT = {
    "source": "btc_matrix", "timestamp": "ts_ms",
    "required_columns": ["close", "ret_5m", "future_abs_move_5m", *BASE_FEATURES,
                         "trade_count", "volume"],
    "causal_rule": "features are available at ts_ms; ret_5m/future_abs_move_5m are labels only",
}
MODEL_CONTRACT = {
    "source": "analytics_table", "table": "model_predictions", "timestamp": "timestamp",
    "required_columns": ["model", "horizon", "direction", "actual_direction", "hit", "resolved"],
    "where": "resolved = true",
    "causal_rule": "resolved outcome is label-only; votes share exact prediction timestamp and horizon",
}
PM_FEATURES = ["distance_bps", "seconds_left", "vol_60s_pct", "p_hold_cur",
               "p_hold_up", "p_hold_down", "up_spread", "down_spread",
               "up_top_ask_size", "down_top_ask_size", "up_d1", "down_d1"]
PM_CONTRACT = {
    "source": "poly_checkpoints", "timestamp": "snapshot_ts",
    "required_columns": [
        "slug", "condition_id", "horizon", "anchor_ts", "checkpoint_s",
        "checkpoint_age_s", "eligible", "seconds_left", "anchor_price", "btc_price",
        "distance_bps", "vol_60s_pct", "p_hold_cur", "p_hold_up", "p_hold_down",
        "current_side", "up_bid", "up_ask", "up_mid", "up_spread", "up_top_ask_size",
        "up_d1", "down_bid", "down_ask", "down_mid", "down_spread",
        "down_top_ask_size", "down_d1", "settled_side", "up_win", "down_win",
    ],
    "causal_rule": "atomic checkpoint row with checkpoint_age_s >= 0; settlement is label-only",
}
EVENT_CONTRACT = {
    "source": "multi_venue_events", "timestamp": "recv_ts",
    "required_columns": ["price", "size", "side", "venue", "stream", "bid", "ask"],
    "where": "price IS NOT NULL",
    "causal_rule": "local receive timestamp orders public events; five-day minimum span applies",
}
L2_CONTRACT = {
    "source": "binance_l2", "timestamp": "ts_ms",
    "required_columns": ["mid", "microprice", "spread_bps", "obi_20", "obi_near",
                         "bid_usd", "ask_usd", "depth_slope", "ofi"],
    "causal_rule": "top-of-book snapshots only; no queue-position or passive-fill claim",
}
PM_UPDATE_CONTRACT = {
    "source": "polymarket_l2_updates", "timestamp": "recv_ts_ns",
    "required_columns": ["asset_id", "side", "price", "previous_size", "new_size",
                         "best_bid", "best_ask", "applied"],
    "where": "applied = true",
    "causal_rule": "sequenced applied L2 updates ordered by local receive timestamp",
}
FORWARD_CONTRACT = {
    "source": "analytics_table", "table": "forward_ev_ledger", "timestamp": "timestamp",
    "required_columns": ["horizon", "entry_price", "expected_move", "confidence",
                         "raw_direction", "final_direction", "trade_verdict", "action",
                         "notional_usd", "fee_bps", "slippage_bps", "no_trade_reasons_json",
                         "resolved", "actual_move", "actual_direction", "direction_hit",
                         "gross_pnl_usd", "fees_usd", "slippage_usd", "net_pnl_usd",
                         "avoided_loss_usd", "opportunity_cost_usd"],
    "causal_rule": "decision fields precede resolution; outcome and PnL columns are labels only",
}
RULE_CONTRACT = {
    "source": "analytics_table", "table": "rule_paper_trades", "timestamp": "ts",
    "required_columns": ["round_id", "rule", "horizon", "side", "ask", "bid", "fee",
                         "spread", "depth", "action", "outcome", "pnl", "settled_ts",
                         "btc_entry", "btc_exit", "exit_gross", "exit_fee", "exit_reason",
                         "state_json", "settlement_source"],
    "causal_rule": "paper records only; no real-order or fill claim",
}
EPISODE_CONTRACT = {
    "source": "multi_venue_episodes", "timestamp": "episode_start",
    "required_columns": ["episode_end", "stream_counts", "streams_live", "streams_required",
                         "max_ws_age_ms", "max_rest_age_ms", "reconnects", "qualifying",
                         "exclusion_reason"],
    "causal_rule": "recorder-health episodes; market outcomes require a separate causal join",
}

DEFAULT_GATES = {
    "minimum_rows": 500,
    "minimum_test_actions": 100,
    "minimum_profit_factor": 1.2,
    "minimum_day_blocks": 10,
    "minimum_week_blocks": 4,
    "maximum_day_concentration": 0.35,
    "require_positive_day_lcb": True,
    "require_positive_week_lcb": True,
    "require_positive_1_5x_cost_stress": True,
    "capital_authority": False,
}


def spec(number: int, directory: str, question: str, engine: str, *, contract=None,
         method=None, controls=None, venue="RESEARCH") -> dict:
    return {
        "number": number,
        "directory": directory,
        "protocol_version": "phase5b.v1",
        "experiment_id": f"P5B_{number:02d}_{directory.removeprefix('test_').upper()}",
        "question": question,
        "engine": engine,
        "data_contract": contract or {},
        "method": method or {},
        "controls": controls or ["no_trade", "chronological_baseline", "matched_control"],
        "cost_model": {
            "venue": venue,
            "binance_round_trip_bps": 9.0 if venue == "BINANCE" else None,
            "polymarket_fee": "0.07*p*(1-p) rounded to 5 decimals" if venue == "POLYMARKET" else None,
            "stress_multipliers": [1.0, 1.5, 2.0],
        },
        "promotion_gates": dict(DEFAULT_GATES),
        "capital_authority": False,
    }


def blocked(number: int, directory: str, question: str, artifact: str, reason: str,
            *, required_columns=None) -> dict:
    return spec(number, directory, question, "readiness", method={
        "required_artifacts": [artifact],
        "required_columns": required_columns or [],
        "blocked_reason": reason,
    })


SPECS = [
    blocked(43, "test_forecast_revision_path", "Does the forecast-revision path add information?",
            "data/research/forecast_revision_paths.parquet",
            "per-checkpoint forecasts are not joined to immutable model release and settlement"),
    blocked(44, "test_forecast_revision_overshoot", "Do forecast jumps overshoot and retrace?",
            "data/research/forecast_revision_paths.parquet",
            "causal 10-point revision and 30/60/120-second retracement paths are not preserved"),
    blocked(45, "test_forecast_stability_vs_accuracy", "Are stable forecasts more valuable?",
            "data/research/forecast_revision_paths.parquet",
            "settlement-joined revision stability and executable entry prices are unavailable"),
    spec(46, "test_minority_model_correctness", "When is the minority ensemble side correct?",
         "ensemble_audit", contract=MODEL_CONTRACT, method={"mode": "minority_correctness"}),
    spec(47, "test_shared_information_false_consensus", "How independent are ensemble votes?",
         "ensemble_audit", contract=MODEL_CONTRACT, method={"mode": "false_consensus"}),
    spec(48, "test_time_to_expiry_calibration_surface", "How does calibration change with expiry?",
         "expiry_calibration", contract=PM_CONTRACT, method={"mode": "expiry_calibration"},
         venue="POLYMARKET"),
    blocked(49, "test_model_confidence_collapse_hazard", "Will strong confidence collapse shortly?",
            "data/research/forecast_revision_paths.parquet",
            "confidence-collapse labels require continuous per-model revisions"),
    blocked(50, "test_prediction_freshness_decay", "How quickly does each model output become stale?",
            "data/research/model_prediction_markouts.parquet",
            "1/5/15/30/60/120-second markouts are not stored for each immutable model output"),
    spec(51, "test_market_state_novelty_gate", "Can novelty improve an unchanged base strategy?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "novelty", "features": BASE_FEATURES}, venue="BINANCE"),
    spec(52, "test_local_sample_support", "How many comparable states support each forecast?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "local_support", "features": BASE_FEATURES}),
    spec(53, "test_feature_relationship_sign_stability", "Do feature signs survive environments?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "feature_sign_stability", "features": BASE_FEATURES}),
    spec(54, "test_worst_environment_model_selection", "Which model survives its weakest regime?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "worst_environment", "features": BASE_FEATURES}),
    spec(55, "test_feature_value_drift", "How does feature usefulness change by month?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "feature_drift", "features": BASE_FEATURES}),
    spec(56, "test_information_time_clock", "Does an information clock outperform clock time?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "information_clock", "features": BASE_FEATURES}),
    spec(57, "test_information_exhaustion", "Does intense activity predict exhaustion?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "information_exhaustion", "features": BASE_FEATURES}),
    spec(58, "test_event_burstiness_predictability", "Does event clustering predict bursts?",
         "event_research", contract=EVENT_CONTRACT, method={"mode": "burstiness"}),
    spec(59, "test_market_resilience_after_aggressive_flow", "How does L2 recover after flow?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "resilience"}, venue="BINANCE"),
    spec(60, "test_bid_ask_replenishment_asymmetry", "Which top-of-book side replenishes?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "replenishment"}),
    spec(61, "test_spread_shock_directional_asymmetry", "What follows a spread shock?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "spread_shock"}),
    spec(62, "test_microprice_markout", "Does microprice improve executable markout?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "microprice"}, venue="BINANCE"),
    spec(63, "test_buy_sell_impact_asymmetry", "Does signed flow have asymmetric impact?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "impact_asymmetry"}),
    spec(64, "test_toxic_flow_veto", "Can toxicity veto improve an unchanged policy?",
         "l2_research", contract=L2_CONTRACT, method={"mode": "toxic_veto"}, venue="BINANCE"),
    spec(65, "test_path_efficiency_ratio", "Does efficient path shape predict continuation?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "path_efficiency", "features": BASE_FEATURES}),
    spec(66, "test_trade_sign_entropy", "Does trade-sign entropy predict flow persistence?",
         "event_research", contract=EVENT_CONTRACT, method={"mode": "trade_sign_entropy"}),
    spec(67, "test_path_roughness_and_terminal_outcome", "Does path roughness add information?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "path_roughness", "features": BASE_FEATURES}),
    spec(68, "test_volatility_of_volatility_transition", "Does vol acceleration predict shocks?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "volatility_of_volatility", "features": BASE_FEATURES}),
    spec(69, "test_anchor_pinning_vs_escape", "Will BTC pin, cross or escape the anchor?",
         "pm_research", contract=PM_CONTRACT,
         method={"mode": "anchor_pinning", "features": PM_FEATURES}, venue="POLYMARKET"),
    spec(70, "test_probability_stickiness_near_extremes", "Are extreme probabilities sticky?",
         "pm_research", contract=PM_CONTRACT, method={"mode": "probability_stickiness"}),
    spec(71, "test_yes_no_complement_execution_asymmetry", "Which token expresses a view cheaper?",
         "pm_research", contract=PM_CONTRACT, method={"mode": "yes_no_asymmetry"},
         venue="POLYMARKET"),
    spec(72, "test_token_liquidity_asymmetry_persistence", "Does token liquidity persist?",
         "pm_research", contract=PM_CONTRACT, method={"mode": "liquidity_persistence"}),
    blocked(73, "test_polymarket_quote_lead_lag", "Which PM quote component reacts first to BTC?",
            "data/research/causal_btc_pm_l2_events.parquet",
            "no clock-admissible atomic BTC-event to paired-token L2 join exists"),
    spec(74, "test_polymarket_response_decomposition", "What causes Polymarket repricing?",
         "pm_l2_research", contract=PM_UPDATE_CONTRACT,
         method={"mode": "response_decomposition"}),
    blocked(75, "test_settlement_source_basis_hazard", "Can settlement-reference basis cross the anchor?",
            "data/research/settlement_reference_basis_paths.parquet",
            "continuous settlement-reference observations are unavailable"),
    spec(76, "test_sequential_value_of_information", "Is acting now better than waiting?",
         "pm_research", contract=PM_CONTRACT,
         method={"mode": "sequential_voi", "features": PM_FEATURES}, venue="POLYMARKET"),
    spec(77, "test_skip_reason_economic_value", "Which skip reasons avoid losses?",
         "system_research", contract=FORWARD_CONTRACT, method={"mode": "skip_reason_value"}),
    spec(78, "test_data_quality_conditioned_performance", "How does quality affect performance?",
         "pm_research", contract=PM_CONTRACT, method={"mode": "data_quality"}),
    spec(79, "test_model_error_taxonomy", "Why does each resolved decision fail?",
         "system_research", contract=FORWARD_CONTRACT, method={"mode": "error_taxonomy"}),
    spec(80, "test_pnl_source_attribution", "What generated paper PnL?",
         "system_research", contract=RULE_CONTRACT, method={"mode": "pnl_attribution"}),
    spec(81, "test_capital_efficiency", "What is PnL per capital-minute?",
         "system_research", contract=RULE_CONTRACT, method={"mode": "capital_efficiency"}),
    spec(82, "test_online_regime_discovery", "Do learned states beat hand-defined regimes?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "online_regime", "features": BASE_FEATURES}),
    spec(83, "test_state_transition_graph", "Are state transitions predictable?",
         "matrix_research", contract=BTC_CONTRACT,
         method={"mode": "state_transition", "features": BASE_FEATURES}),
    spec(84, "test_horizon_consistency", "Are forecasts logically consistent across horizons?",
         "system_research", method={"mode": "horizon_consistency"}),
    spec(85, "test_candidate_evidence_completeness", "Is candidate evidence complete?",
         "system_research", method={"mode": "candidate_completeness", "required_columns": [
             "ts_ms", "alpha_id", "action", "exact_prediction", "exact_quote", "exact_state",
             "counterfactual_action_values", "future_outcome", "cost", "net_pnl"]}),
    blocked(86, "test_counterfactual_action_arm_completeness", "Are all position action arms executable?",
            "data/research/open_position_action_paths.parquet",
            "HOLD/EXIT/REDUCE/SWITCH/LOCK are not recorded at one causal timestamp",
            required_columns=["ts_ms", "hold_value", "exit_value", "reduce_value",
                              "switch_value", "lock_value"]),
    spec(87, "test_recorder_gap_selection_bias", "Are recorder gaps selective?",
         "system_research", contract=EPISODE_CONTRACT, method={"mode": "gap_bias"}),
    blocked(88, "test_timestamp_uncertainty_stress", "Does edge survive timestamp uncertainty?",
            "data/research/phase5_candidate_latency_paths.parquet",
            "candidate markouts at 100/250/500ms and 1/2s pessimistic delays are unavailable",
            required_columns=["ts_ms", "net_pnl_100ms", "net_pnl_250ms", "net_pnl_500ms",
                              "net_pnl_1s", "net_pnl_2s"]),
]


RUN = '''from __future__ import annotations
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5b_standalone.common.runner import standalone_entry

if __name__ == "__main__":
    raise SystemExit(standalone_entry(__file__))
'''

SELFTEST = '''from __future__ import annotations
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5b_standalone.common.runner import standalone_entry

if __name__ == "__main__":
    raise SystemExit(standalone_entry(str(Path(__file__).with_name("run.py")), ["--selftest"]))
'''


def render_readme(item: dict) -> str:
    return f'''# {item["experiment_id"]}

## Question

{item["question"]}

## Frozen Contract

- Engine: `{item["engine"]}`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Four chronological partitions and purge gaps apply to modeled tests.
- Untouched results never select a model, threshold, feature or policy.
- Outputs are immutable; use a new output directory for every run.

## Run

```powershell
python research\\phase5b_standalone\\{item["directory"]}\\run.py `
  --data-dir data `
  --output data\\research\\phase5b_standalone\\{item["directory"]}\\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code and protocol validation, or `--dry-run` for a non-persisting data run.
'''


def main() -> int:
    if len(SPECS) != 46 or [item["number"] for item in SPECS] != list(range(43, 89)):
        raise AssertionError("Phase 5B specs must cover every experiment 43-88 exactly once")
    for item in SPECS:
        directory = ROOT / item["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        payload = {key: value for key, value in item.items() if key not in {"number", "directory"}}
        payload["protocol_sha256"] = protocol_hash(payload)
        (directory / "frozen_protocol.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        (directory / "run.py").write_text(RUN, encoding="utf-8", newline="\n")
        (directory / "selftest.py").write_text(SELFTEST, encoding="utf-8", newline="\n")
        (directory / "README.md").write_text(render_readme(item), encoding="utf-8", newline="\n")
    print(f"generated {len(SPECS)} Phase 5B standalone experiment packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
