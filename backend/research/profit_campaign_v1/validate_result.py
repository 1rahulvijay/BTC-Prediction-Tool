"""Fail-closed artifact validator for a PROFIT_CAMPAIGN_V1 run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .contracts import Protocol, implementation_sha256


COST_ID = "BINANCE_COST_AWARE_NET_PNL_V1"
EXIT_ID = "BINANCE_DYNAMIC_EXIT_V1"
TOLERANCE = 1e-8


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reconcile_trade_accounting(frame: pd.DataFrame, label: str) -> None:
    required = {
        "gross_pnl_usd",
        "fee_usd",
        "impact_reserve_usd",
        "funding_usd",
        "net_pnl_usd",
    }
    _require(required <= set(frame.columns), f"{label} missing accounting columns")
    expected = (
        frame["gross_pnl_usd"].astype(float)
        - frame["fee_usd"].astype(float)
        - frame["impact_reserve_usd"].astype(float)
        + frame["funding_usd"].astype(float)
    )
    error = np.abs(expected - frame["net_pnl_usd"].astype(float))
    _require(
        bool((error <= TOLERANCE).all()),
        f"{label} accounting mismatch max_error={float(error.max())}",
    )


def _validate_non_overlap(frame: pd.DataFrame, label: str) -> None:
    if frame.empty:
        return
    group_columns = [
        column
        for column in ("role", "horizon_seconds", "policy")
        if column in frame
    ]
    for keys, group in frame.groupby(group_columns, sort=False):
        ordered = group.sort_values(["entry_ts_ns", "exit_ts_ns"])
        entry = ordered["entry_ts_ns"].to_numpy(np.int64)
        exit_ts = ordered["exit_ts_ns"].to_numpy(np.int64)
        if len(ordered) > 1:
            _require(
                bool(np.all(entry[1:] >= exit_ts[:-1])),
                f"{label} overlapping exposures for {keys}",
            )


def _validate_selector(predictions: pd.DataFrame) -> None:
    reserve = predictions["uncertainty_reserve_usd"].to_numpy(float)
    long_q20 = predictions["long_net_q20"].to_numpy(float)
    short_q20 = predictions["short_net_q20"].to_numpy(float)
    expected = np.full(len(predictions), "WAIT", dtype=object)
    expected[(long_q20 > short_q20) & (long_q20 > reserve)] = "LONG"
    expected[(short_q20 > long_q20) & (short_q20 > reserve)] = "SHORT"
    actual = predictions["selector_action"].astype(str).to_numpy(object)
    _require(
        bool(np.array_equal(expected, actual)),
        "saved selector actions disagree with the frozen q20 rule",
    )


def _validate_protocol_snapshot(run_dir: Path, protocol: Protocol) -> None:
    snapshot = run_dir / "frozen_protocol.json"
    _require(snapshot.exists(), "missing frozen protocol snapshot")
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    _require(
        snapshot_hash == protocol.sha256,
        "run protocol snapshot differs from the repository protocol",
    )


def validate_result(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    protocol = Protocol.load()
    code_hash = implementation_sha256()
    _validate_protocol_snapshot(run_dir, protocol)
    manifest = _read_json(run_dir / "manifest.json")
    _require(manifest.get("research_only") is True, "manifest is not research-only")
    _require(
        manifest.get("protocol_sha256") == protocol.sha256,
        "manifest protocol hash mismatch",
    )
    _require(
        manifest.get("implementation_sha256") == code_hash,
        "manifest implementation hash mismatch",
    )

    cost_dir = run_dir / COST_ID
    exit_dir = run_dir / EXIT_ID
    cost_summary = _read_json(cost_dir / "summary.json")
    exit_summary = _read_json(exit_dir / "summary.json")
    for summary in (cost_summary, exit_summary):
        _require(
            summary.get("production_permissions_changed") is False,
            f"{summary.get('campaign_id')} changed production permissions",
        )
        _require(
            summary.get("protocol_sha256") == protocol.sha256,
            f"{summary.get('campaign_id')} protocol hash mismatch",
        )
        _require(
            summary.get("implementation_sha256") == code_hash,
            f"{summary.get('campaign_id')} implementation hash mismatch",
        )

    cost_predictions = pd.read_parquet(cost_dir / "model_predictions.parquet")
    cost_trades = pd.read_csv(cost_dir / "policy_trades.csv")
    exit_trades = pd.read_csv(exit_dir / "exit_policy_trades.csv")
    _validate_selector(cost_predictions)
    _reconcile_trade_accounting(cost_trades, COST_ID)
    _reconcile_trade_accounting(exit_trades, EXIT_ID)
    _validate_non_overlap(cost_trades, COST_ID)
    _validate_non_overlap(exit_trades, EXIT_ID)

    model = exit_trades[
        (exit_trades["role"] == "untouched_test")
        & (exit_trades["policy"] == "MODEL_INCREMENTAL_EV")
    ]
    maximum_hold = exit_trades[
        (exit_trades["role"] == "untouched_test")
        & (exit_trades["policy"] == "MAXIMUM_HOLD")
    ]
    _require(
        set(model["path_id"]) == set(maximum_hold["path_id"]),
        "dynamic-exit paired comparison uses different path denominators",
    )
    reported_delta = float(
        exit_summary["model_incremental_ev"][
            "paired_pnl_delta_vs_hold_usd"
        ]
    )
    actual_delta = float(
        model["net_pnl_usd"].sum() - maximum_hold["net_pnl_usd"].sum()
    )
    _require(
        abs(actual_delta - reported_delta) <= TOLERANCE,
        "dynamic-exit paired delta does not reconcile",
    )

    registry_path = run_dir / "trial_registry.jsonl"
    rows = [
        json.loads(line)
        for line in registry_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _require(bool(rows), "trial registry is empty")
    _require(
        all(row.get("protocol_sha256") == protocol.sha256 for row in rows),
        "trial registry contains another protocol",
    )
    _require(
        all(row.get("implementation_sha256") == code_hash for row in rows),
        "trial registry contains stale implementation rows",
    )
    _require(
        len({row["trial_id"] for row in rows}) == len(rows),
        "trial registry contains duplicate trial IDs",
    )

    output = {
        "status": "PASS",
        "research_only": True,
        "protocol_sha256": protocol.sha256,
        "implementation_sha256": code_hash,
        "registered_trials": len(rows),
        "cost_trade_rows_reconciled": len(cost_trades),
        "exit_trade_rows_reconciled": len(exit_trades),
        "production_permissions_changed": False,
    }
    (run_dir / "validation.json").write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    args = parser.parse_args()
    result = validate_result(Path(args.run_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
