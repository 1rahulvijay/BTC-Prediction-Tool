"""Deterministic unit and real-pilot integration checks for the shadow lane."""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import btc_path_serving, execution_serving, live_forecaster, share_path_serving
from .model_common import artifact_issues, load_verified_dataset
from .scenario_engine import evaluate_plans
from .trade_forecast_logger import connect, status
from . import trade_forecast_logger
from .trade_labels import evaluate_exit_plan, required_exit_bid
from .trade_plan_optimizer import choose_trade
from .trade_schema import BTC_FEATURE_COLUMNS, FEATURE_COLUMNS, QUANTITIES


ROOT = Path(__file__).resolve().parents[2]
VALIDATION = (
    ROOT / "data" / "research" / "complete_trade_forecast" / "validation_dataset.parquet"
)
VALIDATION_MODELS = VALIDATION.parent / "validation_models"


def run() -> None:
    assert abs(float(required_exit_bid(0.62) or 0.0) - 0.6523623501) < 1e-8
    lock = evaluate_exit_plan(
        "BREAK_EVEN_LOCK_AFTER_3C",
        [1.0, 2.0, 3.0],
        [0.03, 0.02, -0.01],
        -0.5,
    )
    assert lock["exit_kind"] == "BREAK_EVEN_FLOOR" and lock["holding_s"] == 3.0
    paths = {
        str(offset): {key: value for key, value in zip(
            ("q10", "q25", "q50", "q75", "q90"),
            (0.50, 0.55, 0.60, 0.65, 0.70),
        )}
        for offset in (5, 10, 15, 30, 60, 120)
    }
    plan_results = evaluate_plans(
        entry_vwap=0.55,
        share_path=paths,
        p_settlement_win=0.70,
    )
    assert plan_results and all("profit_factor" in value for value in plan_results.values())
    assert choose_trade(
        [],
        data_healthy=True,
        evidence_promotable=False,
    )["action"] == "NO_TRADE"

    if VALIDATION.is_file():
        frame, manifest = load_verified_dataset(VALIDATION)
        assert manifest["promotable"] is False
        assert set(QUANTITIES).issubset(set(frame["requested_qty"].astype(int).unique()))
        assert (frame["entry_complete"] == 0).any()
        assert "share_ask_logit_delta_30s" in frame.columns
        assert "label_break_even_by_30s" in frame.columns

        paths_by_name = {
            "share": (VALIDATION_MODELS / "share.pkl", FEATURE_COLUMNS),
            "btc": (VALIDATION_MODELS / "btc.pkl", BTC_FEATURE_COLUMNS),
            "execution": (VALIDATION_MODELS / "execution.pkl", FEATURE_COLUMNS),
        }
        for name, (path, columns) in paths_by_name.items():
            issues = artifact_issues(path, expected_feature_columns=columns)
            assert issues[1] == [], f"{name} artifact rejected: {issues[1]}"

        sample = frame.dropna(subset=list(FEATURE_COLUMNS)).iloc[0]
        features = {column: float(sample[column]) for column in FEATURE_COLUMNS}
        share_path_serving.MODEL_PATH = paths_by_name["share"][0]
        execution_serving.MODEL_PATH = paths_by_name["execution"][0]
        btc_path_serving.MODEL_PATH = paths_by_name["btc"][0]
        share_path_serving.load_model(force=True)
        execution_serving.load_model(force=True)
        btc_path_serving.load_model(force=True)
        share_score = share_path_serving.score_candidate(
            int(sample["horizon"]),
            features,
        )
        execution_score = execution_serving.score(int(sample["horizon"]), features)
        btc_features = {column: float(sample[column]) for column in BTC_FEATURE_COLUMNS}
        btc_score = btc_path_serving.score(int(sample["horizon"]), btc_features)
        assert share_score["status"] == "PILOT_ESTIMATE_NOT_ACTIONABLE"
        assert share_score["path"] and share_score["ask_path"]
        assert share_score["summary"] and share_score["crossing_path"]
        assert execution_score["status"] == "PILOT_ESTIMATE_NOT_ACTIONABLE"
        assert execution_score["entry_slippage"] and execution_score["capacity"]
        assert btc_score["status"] == "PILOT_ESTIMATE_NOT_ACTIONABLE"

    with tempfile.TemporaryDirectory() as directory:
        old_path = trade_forecast_logger.DB_PATH
        trade_forecast_logger.DB_PATH = Path(directory) / "ledger.duckdb"
        try:
            live_forecaster._LOGGED.clear()
            candidates = []
            evaluations = []
            for side in ("UP", "DOWN"):
                for quantity in QUANTITIES:
                    candidates.append(
                        {
                            "side": side,
                            "requested_qty": quantity,
                            "features": {column: 1.0 for column in FEATURE_COLUMNS},
                            "share_forecast": {"events": {}, "summary": {}, "path": {}},
                        }
                    )
                    evaluations.append(
                        {
                            "side": side,
                            "requested_qty": float(quantity),
                            "action": "NO_TRADE",
                            "reason_codes": ["test"],
                        }
                    )
            live_forecaster._logger_rows(
                {
                    "id": "test-round",
                    "horizon": 5,
                    "seconds_left": 60,
                    "price_to_beat": 100_000,
                    "current_price": 100_010,
                },
                {
                    "candidates_raw": candidates,
                    "decision": {"candidates": evaluations},
                    "btc_forecast": {"path": {}},
                },
                1_000,
            )
            conn = connect(trade_forecast_logger.DB_PATH)
            counts = status(conn)
            conn.close()
            assert counts["complete_trade_forecasts"] == 2 * len(QUANTITIES)
        finally:
            trade_forecast_logger.DB_PATH = old_path
            live_forecaster._LOGGED.clear()

    print("complete-trade forecast integration: ALL PASS")


if __name__ == "__main__":
    run()
