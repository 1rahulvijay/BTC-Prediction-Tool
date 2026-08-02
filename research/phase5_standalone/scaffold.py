"""Generate the 42 frozen Phase 5 standalone entry-point packages.

This is a mechanical repository scaffold generator. Experiment logic stays in ``common``;
each generated directory owns an immutable protocol, CLI entry point, self-test and README.
"""
from __future__ import annotations

import json
from pathlib import Path

from common.protocol import protocol_hash


ROOT = Path(__file__).resolve().parent

BASE_FEATURES = [
    "rv_15m", "rv_60m", "rv_term", "vol_accel", "compression_ratio",
    "shock_magnitude", "cvd_change", "cvd_1m", "cvd_5m", "delta", "vpin",
    "large_trade_delta", "large_trade_imbalance", "funding_velocity",
    "cvd_spot", "cvd_perp", "cvd_divergence", "perp_spot_basis_bps",
    "vol_spot", "vol_perp",
]
BTC_CONTRACT = {
    "source": "btc_matrix", "timestamp": "ts_ms",
    "required_columns": [*BASE_FEATURES, "close", "future_direction_5m", "ret_5m",
                         "future_abs_move_5m"],
    "causal_rule": "features at ts_ms; future_* columns are labels only",
}
CROSSVENUE_FEATURES = ["coinbase", "bybit", "okx", "max_spread_bps",
                       "ret_binance", "ret_coinbase", "ret_bybit", "ret_okx"]
CROSSVENUE_CONTRACT = {
    "source": "crossvenue", "timestamp": "ts_ms",
    "required_columns": ["binance", *CROSSVENUE_FEATURES],
    "causal_rule": "all venue observations are from the same recorder snapshot",
}
EVENT_CONTRACT = {
    "source": "multi_venue_events", "timestamp": "recv_ts",
    "required_columns": ["price", "size", "side", "venue", "stream", "bid", "ask"],
    "causal_rule": "local receive time orders events; exchange timestamp is diagnostic only",
}
L2_CONTRACT = {
    "source": "binance_l2", "timestamp": "ts_ms",
    "required_columns": ["spread_bps", "obi_20", "obi_near", "bid_usd", "ask_usd",
                         "depth_slope", "ofi"],
    "causal_rule": "top-of-book snapshot only; no maker queue claim",
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
        "up_bid", "up_ask", "up_spread", "up_top_ask_size", "up_d1",
        "down_bid", "down_ask", "down_spread", "down_top_ask_size", "down_d1",
        "settled_side", "up_win", "down_win",
    ],
    "causal_rule": "one atomic recorder row; checkpoint_age_s >= 0; outcomes are labels only",
}
CANDIDATE_INPUT = "research/phase5_candidate_evidence.parquet"

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
BTC_METHOD = {"features": BASE_FEATURES, "target": "future_direction_5m",
              "return_column": "ret_5m", "return_unit": "usd",
              "holding_seconds": 300, "purge_rows": 5,
              "thresholds": [0.55, 0.60, 0.65, 0.70]}


def spec(number: int, name: str, question: str, engine: str, *, contract=None,
         method=None, controls=None, venue="RESEARCH") -> dict:
    return {
        "number": number,
        "directory": name,
        "protocol_version": "phase5.v1",
        "experiment_id": f"P5_{number:02d}_{name.removeprefix('test_').upper()}",
        "question": question,
        "engine": engine,
        "data_contract": contract or {},
        "method": method or {},
        "controls": controls or ["no_trade", "matched_random", "time_shifted_placebo"],
        "cost_model": {
            "venue": venue,
            "binance_round_trip_bps": 9.0 if venue == "BINANCE" else None,
            "polymarket_fee": "0.07*p*(1-p) rounded to 5 decimals" if venue == "POLYMARKET" else None,
            "stress_multipliers": [1.0, 1.5, 2.0],
        },
        "promotion_gates": dict(DEFAULT_GATES),
        "capital_authority": False,
    }


SPECS = [
    spec(1, "test_alpha_extractability_upper_bound",
         "Does the recorded market contain enough executable opportunity to justify more modeling?",
         "alpha_upper_bound", contract=BTC_CONTRACT, method=BTC_METHOD, venue="BINANCE"),
    spec(2, "test_history_live_transportability",
         "Is the historical training archive representative of the recent environment?",
         "history_transport", contract=BTC_CONTRACT,
         method={"features": BASE_FEATURES, "history_windows_days": [30, 60, 90, 120, 240, 400]}),
    spec(3, "test_feed_incremental_economic_value",
         "Which recorder feeds add unique economic information after costs?",
         "feed_ablation", contract=BTC_CONTRACT,
         method={**BTC_METHOD, "feature_groups": {
             "spot_flow": ["cvd_spot", "vol_spot"],
             "perpetual_flow": ["cvd_perp", "vol_perp", "funding_velocity"],
             "crossvenue": ["cvd_divergence", "perp_spot_basis_bps"],
             "toxicity": ["vpin", "large_trade_imbalance"],
             "volatility": ["rv_15m", "rv_60m", "rv_term", "shock_magnitude"],
         }}, venue="BINANCE"),
    spec(4, "test_oracle_current_model_disagreement",
         "Does current code improve upon the July 4 Oracle deployment on paired forward decisions?",
         "readiness", method={"required_artifacts": ["releases/ORACLE_2026_07_04/release_manifest.json",
                                                     "data/research/oracle_current_paired_predictions.parquet"],
                                      "blocked_reason": "the Oracle's overwritten central artifacts prevent paired prediction reconstruction"}),
    spec(5, "test_signal_context_sign_reversal",
         "Does an existing flow signal change economic sign across frozen contexts?",
         "signal_context", contract=BTC_CONTRACT, method=BTC_METHOD, venue="BINANCE"),
    spec(6, "test_dynamic_crypto_factor_residual",
         "Is BTC-specific residual movement more predictable than raw BTC movement?",
         "readiness", method={"required_artifacts": ["data/research/crypto_factor_panel.parquet"],
                              "blocked_reason": "no causal cross-asset crypto factor panel exists"}),
    spec(7, "test_factor_residual_mean_reversion_vs_continuation",
         "When does a BTC factor residual continue and when does it revert?",
         "readiness", method={"required_artifacts": ["data/research/crypto_factor_residual_events.parquet"],
                              "blocked_reason": "factor residual events must be built by experiment 06 first"}),
    spec(8, "test_cross_exchange_dislocation_decay",
         "When BTC venues disagree, which venue catches up and which reverses?",
         "crossvenue", contract=CROSSVENUE_CONTRACT,
         method={"features": CROSSVENUE_FEATURES}, venue="BINANCE"),
    spec(9, "test_spot_perpetual_basis_residual",
         "Does abnormal spot/perpetual basis predict continuation or reversal after costs?",
         "btc_signal", contract=BTC_CONTRACT,
         method={**BTC_METHOD, "features": ["perp_spot_basis_bps", "cvd_divergence",
                                             "funding_velocity", "rv_15m", "rv_term",
                                             "vpin", "vol_spot", "vol_perp"]}, venue="BINANCE"),
    spec(10, "test_synthetic_metaorder_segmentation",
         "Can public event data identify runs with more structure than random trade runs?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "segmentation"}),
    spec(11, "test_metaorder_continuation_head",
         "Once a probable parent order is detected, does executable impact continue?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "continuation"}, venue="BINANCE"),
    spec(12, "test_metaorder_exhaustion_reversal",
         "Can falling impact efficiency identify flow exhaustion and reversal?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "exhaustion"}, venue="BINANCE"),
    spec(13, "test_core_reaction_flow_overshoot",
         "Does reaction flow overshoot initiating flow enough to support a causal fade?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "overshoot"}, venue="BINANCE"),
    spec(14, "test_price_impact_efficiency_decay",
         "Does aggressive flow continue moving price efficiently?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "impact_decay"}, venue="BINANCE"),
    spec(15, "test_liquidity_withdrawal_hazard",
         "Is executable top-of-book liquidity about to disappear?",
         "l2_hazard", contract=L2_CONTRACT, method={"horizons_seconds": [5, 15, 30]}),
    spec(16, "test_event_language_surprise",
         "Can a self-supervised event model distinguish ordinary from surprising events?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "event_language"}),
    spec(17, "test_surprise_propagation",
         "Do surprising events propagate, get absorbed, or reverse across venues?",
         "event_flow", contract=EVENT_CONTRACT, method={"mode": "propagation"}, venue="BINANCE"),
    spec(18, "test_surprise_as_volatility_not_direction",
         "Do market surprises predict movement magnitude even when direction is weak?",
         "btc_magnitude", contract=BTC_CONTRACT,
         method={"features": BASE_FEATURES, "magnitude_column": "future_abs_move_5m",
                 "training_quantile": 0.75, "purge_rows": 5}),
    spec(19, "test_poly_probability_elasticity",
         "How much should executable Polymarket probability move for a BTC move?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "elasticity"}, venue="POLYMARKET"),
    spec(20, "test_poly_elasticity_residual_closure",
         "After a pricing-response residual appears, does the book catch up before costs?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "residual_closure"}, venue="POLYMARKET"),
    spec(21, "test_poly_deadline_convexity_surface",
         "Does executable probability sensitivity change correctly near expiry?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "deadline_convexity"}, venue="POLYMARKET"),
    spec(22, "test_poly_new_round_opening_inheritance",
         "Does a new market inherit stale executable pricing from the previous round?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "opening_inheritance"}, venue="POLYMARKET"),
    spec(23, "test_poly_round_boundary_resonance",
         "Are simultaneous 5m and 15m boundaries structurally different?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "boundary_resonance"}, venue="POLYMARKET"),
    spec(24, "test_poly_post_settlement_unwind",
         "Does settlement create predictable hedge unwinding in BTC or the next round?",
         "readiness", method={"required_artifacts": ["data/research/causal_settlement_unwind_join.parquet"],
                              "blocked_reason": "no canonical causal settlement-to-Binance event join exists"}),
    spec(25, "test_poly_cross_expiry_consistency",
         "Are simultaneous 5m and 15m contracts jointly inconsistent after executable costs?",
         "pm_cross_expiry", contract=PM_CONTRACT,
         method={"logic": "NO(high anchor) + YES(low anchor) guarantees at least $1"},
         controls=["no_trade", "stale_pair_exclusion", "partial_fill_stress"], venue="POLYMARKET"),
    spec(26, "test_poly_complete_set_lock_frequency",
         "How often can an existing open paper position be locked into guaranteed profit?",
         "readiness", method={"required_artifacts": ["data/research/open_position_quote_paths.parquet"],
                              "blocked_reason": "no canonical open-position plus opposite-side depth paths exist"},
         venue="POLYMARKET"),
    spec(27, "test_alpha_context_specialization",
         "In which frozen contexts is each candidate alpha economically active?",
         "candidate_audit", method={"mode": "context", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "alpha_id", "regime"]}),
    spec(28, "test_alpha_redundancy_and_unique_value",
         "Does a candidate add independent PnL rather than duplicate an incumbent?",
         "candidate_audit", method={"mode": "redundancy", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "alpha_id"]}),
    spec(29, "test_alpha_collision_interference",
         "What happens when multiple candidate alphas trigger simultaneously?",
         "candidate_audit", method={"mode": "collision", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "alpha_id"]}),
    spec(30, "test_alpha_decay_change_points",
         "Is a candidate temporarily weak or structurally dead?",
         "candidate_audit", method={"mode": "decay", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl"]}),
    spec(31, "test_alpha_regime_revival",
         "Does a retired alpha revive when its historical regime returns?",
         "candidate_audit", method={"mode": "revival", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "regime"]}),
    spec(32, "test_reward_transport",
         "What would historical gross alpha earn under current executable costs?",
         "candidate_audit", method={"mode": "reward_transport", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "gross_pnl", "current_cost"]}),
    spec(33, "test_enter_now_vs_wait",
         "Is it better to enter a Polymarket opportunity now or at the next checkpoint?",
         "pm_dynamic", contract=PM_CONTRACT, method={"features": PM_FEATURES, "mode": "enter_now_vs_wait"}, venue="POLYMARKET"),
    spec(34, "test_hold_exit_switch_lock_counterfactual",
         "Which executable next-checkpoint action has the best incremental value?",
         "readiness", method={"required_artifacts": ["data/research/open_position_action_paths.parquet"],
                              "blocked_reason": "HOLD/EXIT/REDUCE/SWITCH/LOCK requires one causal open-position population with every action arm"},
         venue="POLYMARKET"),
    spec(35, "test_conformal_net_ev_gate",
         "Does a positive lower confidence bound improve candidate economics?",
         "candidate_audit", method={"mode": "conformal", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "predicted_ev"]}),
    spec(36, "test_capacity_curve",
         "At what size does a passing alpha lose its lower-bound edge?",
         "l2_capacity", method={"sizes_usd": [10, 25, 50, 100, 250, 500, 1000, 2500, 5000]}),
    spec(37, "test_anytime_valid_forward_evidence",
         "Can a frozen candidate be monitored repeatedly without optional-stopping inflation?",
         "ledger", method={"confidence_sequence": "empirical Bernstein; forward outcomes only"}),
    spec(38, "test_placebo_timing",
         "Does a candidate weaken when its signal is shifted before or after the information event?",
         "candidate_audit", method={"mode": "placebo", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "market_return", "action", "cost"],
                                    "shifts_rows": [-60, -30, -10, 10, 30, 60]}),
    spec(39, "test_sign_and_label_randomization",
         "Does candidate performance collapse under sign and within-day randomization?",
         "candidate_audit", method={"mode": "randomization", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "market_return", "action", "cost"]}),
    spec(40, "test_parameter_neighborhood_stability",
         "Does candidate economics survive small declared parameter perturbations?",
         "candidate_audit", method={"mode": "neighborhood", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "configuration_id"]}),
    spec(41, "test_profit_concentration_and_event_dependence",
         "Is apparent profitability concentrated in one day, week, event, or handful of trades?",
         "candidate_audit", method={"mode": "concentration", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl"]}),
    spec(42, "test_negative_control_strategy",
         "Does the candidate beat an information-free strategy with matched exposure mechanics?",
         "candidate_audit", method={"mode": "negative_control", "candidate_input": CANDIDATE_INPUT,
                                    "required_columns": ["ts_ms", "net_pnl", "market_return", "action", "cost"]}),
]


RUN = '''from __future__ import annotations
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5_standalone.common.runner import standalone_entry

if __name__ == "__main__":
    raise SystemExit(standalone_entry(__file__))
'''

SELFTEST = '''from __future__ import annotations
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.phase5_standalone.common.runner import standalone_entry

if __name__ == "__main__":
    raise SystemExit(standalone_entry(str(Path(__file__).with_name("run.py")), ["--selftest"]))
'''


def render_readme(item: dict) -> str:
    return f'''# {item["experiment_id"]}

## Question

{item["question"]}

## Contract

- Engine: `{item["engine"]}`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\\Users\\rahul\\AppData\\Local\\Programs\\Python\\Python313\\python.exe' `
  research\\phase5_standalone\\{item["directory"]}\\run.py `
  --data-dir data `
  --output data\\research\\phase5_standalone\\{item["directory"]}\\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
'''


def main() -> int:
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
    print(f"generated {len(SPECS)} standalone experiment packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
