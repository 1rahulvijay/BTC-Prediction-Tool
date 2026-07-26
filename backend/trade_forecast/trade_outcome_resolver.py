"""Attach immutable realized dataset outcomes to previously logged shadow forecasts."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .trade_forecast_logger import DB_PATH, connect, init_schema, log_outcome
from .trade_labels import required_exit_bid, taker_fee


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)


def classify_failure(forecast: dict[str, Any], actual: dict[str, Any]) -> tuple[str, dict]:
    details: dict[str, Any] = {}
    predicted_entry = forecast.get("predicted_entry_vwap")
    actual_entry = actual.get("actual_entry_vwap")
    if predicted_entry is not None and actual_entry is not None:
        details["entry_vwap_error"] = float(actual_entry) - float(predicted_entry)
    predicted = forecast.get("expected_pnl")
    realized = actual.get("actual_net_pnl")
    if predicted is not None and realized is not None:
        details["pnl_error"] = float(realized) - float(predicted)
    if not bool(actual.get("entry_complete", 0)):
        component = "ENTRY_LIQUIDITY_WRONG"
    elif details.get("entry_vwap_error", 0.0) > 0.01:
        component = "ENTRY_SLIPPAGE_WRONG"
    elif realized is not None and predicted is not None and float(predicted) > 0 >= float(realized):
        component = "EXIT_OR_PATH_WRONG"
    elif forecast.get("side") != actual.get("settlement_side"):
        component = "SIDE_WRONG"
    else:
        component = "WITHIN_EXPECTED_VARIATION"
    return component, details


def resolve_from_dataset(
    dataset_path: Path = DEFAULT_DATASET,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    if not dataset_path.is_file():
        raise FileNotFoundError(dataset_path)
    data = pd.read_parquet(dataset_path)
    conn = connect(db_path)
    init_schema(conn)
    columns = [description[0] for description in conn.execute(
        """
        SELECT f.* FROM complete_trade_forecasts f
        LEFT JOIN complete_trade_outcomes o USING(forecast_id)
        WHERE o.forecast_id IS NULL
        """
    ).description]
    records = [
        dict(zip(columns, row))
        for row in conn.execute(
            """
            SELECT f.* FROM complete_trade_forecasts f
            LEFT JOIN complete_trade_outcomes o USING(forecast_id)
            WHERE o.forecast_id IS NULL
            """
        ).fetchall()
    ]
    resolved = unmatched = 0
    for forecast in records:
        candidates = data[
            (data["round_id"].astype(str) == str(forecast["round_id"]))
            & (data["horizon"].astype(int) == int(forecast["horizon"]))
            & (data["side"].astype(str) == str(forecast["side"]))
            & (data["requested_qty"].astype(float) == float(forecast["requested_qty"]))
        ].copy()
        if candidates.empty:
            unmatched += 1
            continue
        candidates["time_error"] = (
            candidates["decision_ts"].astype(float)
            - float(forecast["decision_ts"]) / 1000.0
        ).abs()
        actual = candidates.sort_values("time_error").iloc[0].to_dict()
        if float(actual["time_error"]) > 2.0:
            unmatched += 1
            continue
        plan = str(forecast.get("recommended_exit_plan") or "HOLD_TO_SETTLEMENT").lower()
        actual_net = actual.get(f"plan_{plan}_net")
        actual_hold = actual.get(f"plan_{plan}_holding_s")
        exit_kind = actual.get(f"plan_{plan}_exit_kind")
        actual_record = {
            **actual,
            "actual_net_pnl": actual_net,
        }
        component, details = classify_failure(forecast, actual_record)
        actual_entry = actual.get("actual_entry_vwap")
        if (
            actual_entry is not None
            and actual_net is not None
            and str(exit_kind) != "SETTLE"
        ):
            actual_exit = required_exit_bid(
                float(actual_entry),
                float(actual_net),
            )
            actual_exit_fee = (
                taker_fee(float(actual_exit)) if actual_exit is not None else None
            )
        else:
            actual_exit = (
                1.0
                if str(exit_kind) == "SETTLE"
                and actual.get("settlement_side") == forecast.get("side")
                else 0.0 if str(exit_kind) == "SETTLE" else None
            )
            actual_exit_fee = 0.0 if str(exit_kind) == "SETTLE" else None
        outcome = {
            "forecast_id": forecast["forecast_id"],
            "actual_entry_vwap": actual.get("actual_entry_vwap"),
            "actual_entry_latency": actual.get("actual_entry_latency_ms"),
            "actual_entry_fee": actual.get("actual_entry_fee"),
            "actual_first_profitable_s": actual.get("actual_first_profitable_s"),
            "actual_mfe": actual.get("actual_mfe"),
            "actual_mae": actual.get("actual_mae"),
            "actual_exit_vwap": actual_exit,
            "actual_exit_fee": actual_exit_fee,
            "actual_holding_s": actual_hold,
            "actual_net_pnl": actual_net,
            "predicted_error": details.get("pnl_error"),
            "target_hit": str(exit_kind) == "TARGET",
            "stop_hit": str(exit_kind) == "STOP",
            "settlement_outcome": actual.get("settlement_side"),
            "official_resolution_source": actual.get("resolution_source"),
            "failure_component": component,
            "error_details": details,
            "resolved_at": int(time.time() * 1000),
        }
        log_outcome(outcome, conn)
        resolved += 1
    conn.close()
    return {"pending": len(records), "resolved": resolved, "unmatched": unmatched}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    print(json.dumps(resolve_from_dataset(args.dataset.resolve(), args.db.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
