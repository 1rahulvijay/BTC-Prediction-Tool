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
    parser = argparse.ArgumentParser(
        description="Reconstruct V2 evidence from this host's own L2 recorder."
    )
    parser.add_argument("--evidence-run-id")
    parser.add_argument(
        "--recorder-db",
        type=Path,
        default=DATA / "execution_layer.duckdb",
    )
    parser.add_argument("--ledger-db", type=Path, default=DB_PATH)
    parser.add_argument(
        "--legacy-dataset",
        type=Path,
        help="explicit compatibility-only V1 resolver; never writes Ledger V2 evidence",
    )
    args = parser.parse_args()
    if args.legacy_dataset:
        print(json.dumps(
            resolve_from_dataset(
                args.legacy_dataset.resolve(), args.ledger_db.resolve()
            ),
            indent=2,
        ))
        return 0
    if not args.evidence_run_id:
        parser.error("--evidence-run-id is required for V2 reconstruction")
    from .l2_outcome_reconstruction import reconstruct_v2_outcomes

    print(json.dumps(
        reconstruct_v2_outcomes(
            evidence_run_id=args.evidence_run_id,
            recorder_db=args.recorder_db.resolve(),
            ledger_db=args.ledger_db.resolve(),
        ),
        indent=2,
    ))
    return 0


# ===================================================================================
# V2 RESOLVER - the only writer of complete_trade_outcomes_v2
# ===================================================================================
# The legacy resolver reads complete_trade_forecasts and writes complete_trade_outcomes, so the
# V2 outcome table would have stayed empty forever and the forward evaluator would have had
# nothing to score. This resolver reads V2 predictions and writes V2 outcomes, and it refuses
# anything whose settlement is not officially sourced.


def resolve_v2(
    settled_rounds: dict[str, dict[str, Any]],
    conn: Any = None,
    *,
    evidence_run_id: str | None = None,
    test_only: bool = False,
) -> dict[str, Any]:
    """TEST-FIXTURE resolver only; production must use own-L2 reconstruction.

    `settled_rounds` maps round_id -> {resolution_source, settled_side, plan_net,
    stress_1000ms_plan_net, candidate_pnls, entry_filled, entry_vwap, plan_exit_kind,
    plan_holding_s}. Rounds whose resolution_source is not on the frozen allowlist are SKIPPED
    with a reason rather than written, because an unofficial outcome is not ground truth."""
    if not test_only:
        raise RuntimeError(
            "caller-supplied V2 economics are prohibited; use "
            "reconstruct_v2_outcomes() against the own L2 recorder"
        )
    from .trade_forecast_logger import (
        connect,
        ensure_v2_schema,
        log_outcome_v2,
        read_forward_rows,
        read_resolved_outcomes,
    )
    from .trade_schema import OFFICIAL_RESOLUTION_SOURCES

    own = conn is None
    conn = conn or connect()
    written, skipped = 0, []
    try:
        ensure_v2_schema(conn)
        rows = read_forward_rows(conn, evidence_run_id)
        already = set(read_resolved_outcomes(conn, include_test_fixtures=True))
        for row in rows:
            fid = row["forecast_id"]
            if fid in already:
                continue
            settled = settled_rounds.get(row.get("round_id"))
            if not settled:
                skipped.append((fid, "no_settlement"))
                continue
            source = str(settled.get("resolution_source") or "")
            if source not in OFFICIAL_RESOLUTION_SOURCES:
                skipped.append((fid, f"unofficial_source:{source or 'missing'}"))
                continue
            log_outcome_v2({
                "forecast_id": fid,
                "round_id": row.get("round_id"),
                "outcome_schema_version": "TEST_FIXTURE_V1",
                "resolved_at_s": float(settled.get("resolved_at_s") or time.time()),
                "resolution_source": source,
                "reconstruction_source": "TEST_FIXTURE",
                "source_recording_sha256": "t" * 64,
                "settled_side": settled.get("settled_side"),
                "entry_filled": settled.get("entry_filled"),
                "entry_vwap": settled.get("entry_vwap"),
                "entry_snapshot_ts_s": settled.get("entry_snapshot_ts_s"),
                "entry_latency_ms": settled.get("entry_latency_ms"),
                "plan_net": settled.get("plan_net"),
                "plan_exit_kind": settled.get("plan_exit_kind"),
                "plan_holding_s": settled.get("plan_holding_s"),
                "stress_1000ms_plan_net": settled.get("stress_1000ms_plan_net"),
                "stress_entry_vwap": settled.get("stress_entry_vwap"),
                # The matched-random control needs the SAME-CHECKPOINT alternatives the policy
                # could have taken. A pool containing later checkpoints would hand the random
                # control opportunities that did not exist when the trade was made.
                "candidate_pnls_json": json.dumps(settled.get("candidate_pnls") or []),
            }, conn)
            written += 1
        return {"written": written, "skipped": len(skipped),
                "skip_reasons": skipped[:10], "predictions": len(rows)}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
