#!/usr/bin/env python
"""Validate an EVENT_EXECUTION_AND_ANCHOR_CROSSING_V1 result bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_ROOT = ROOT / "data" / "research" / "event_execution_v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def newest_run(root: Path) -> Path:
    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and path.name != "prerequisite_event_heads"
        and (path / "results.json").exists()
    )
    if not candidates:
        raise FileNotFoundError(f"no result runs under {root}")
    return candidates[-1]


def check(name: str, condition: bool, details: Any = "") -> dict[str, Any]:
    return {"check": name, "passed": bool(condition), "details": details}


def validate(run_dir: Path) -> list[dict[str, Any]]:
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    snapshot = json.loads(
        (run_dir / "frozen_protocol_snapshot.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((run_dir / "input_manifest.json").read_text(encoding="utf-8"))
    split = json.loads((run_dir / "split_manifest.json").read_text(encoding="utf-8"))
    metrics = pd.read_csv(run_dir / "experiment_metrics.csv")
    expected_ids = [f"E{index:02d}" for index in range(1, 11)]
    checks = [
        check("protocol snapshot unchanged", snapshot == protocol),
        check("protocol id", results["protocol_id"] == protocol["protocol_id"]),
        check(
            "exact experiment ids", metrics["experiment_id"].tolist() == expected_ids
        ),
        check("exact result count", results["result_count"] == 10),
        check("no production promotions", results["promoted"] == []),
        check(
            "only repricing research gates passed",
            results["research_gate_passed"] == ["E07", "E08"],
        ),
        check(
            "development precedes locked",
            split["development_max"] < split["locked_min"],
        ),
        check(
            "development roles separated",
            set(split["development_roles"]) == {"fit", "calibration"},
        ),
        check(
            "execution candidate artifact",
            (run_dir / "execution_policy_candidates.parquet").exists(),
        ),
        check("execution slices artifact", (run_dir / "execution_slices.csv").exists()),
        check(
            "contract lead-lag artifact", (run_dir / "contract_lead_lag.csv").exists()
        ),
        check(
            "incremental prediction artifact",
            (run_dir / "locked_incremental_predictions.parquet").exists(),
        ),
        check("btc proxy artifact", (run_dir / "btc_proxy_trades.parquet").exists()),
        check("model artifacts", len(list((run_dir / "models").glob("*.joblib"))) == 6),
    ]
    manifest_valid = True
    for item in manifest["inputs"]:
        path = Path(item["path"])
        manifest_valid &= (
            path.exists()
            and path.stat().st_size == item["bytes"]
            and sha256_file(path) == item["sha256"]
        )
    checks.append(check("input hashes and sizes", manifest_valid))

    result_by_id = {item["experiment_id"]: item for item in results["results"]}
    promotion_logic_valid = True
    for experiment_id in expected_ids:
        item = result_by_id[experiment_id]
        if item["family"] in {"crossing", "contract_repricing"}:
            promotion_logic_valid &= (
                item["research_gate_passed"] == all(item["gate_checks"].values())
                and item["promoted"] is False
            )
        elif item["family"] == "btc_proxy":
            promotion_logic_valid &= item["promoted"] is False
        elif item["family"] == "execution" and experiment_id != "E01":
            promotion_logic_valid &= (
                item["research_gate_passed"] == all(item["gate_checks"].values())
                and item["promoted"] is False
            )
        else:
            promotion_logic_valid &= item["promoted"] is False
    checks.append(
        check(
            "research gates reproduce and production stays blocked",
            promotion_logic_valid,
        )
    )

    candidate_rows = pd.read_parquet(run_dir / "execution_policy_candidates.parquet")
    policy_counts = candidate_rows.groupby("policy")["condition_id"].nunique()
    checks.append(
        check(
            "execution policies use same original candidates",
            policy_counts.nunique() == 1 and len(policy_counts) == 3,
            policy_counts.to_dict(),
        )
    )
    btc = pd.read_parquet(run_dir / "btc_proxy_trades.parquet")
    checks.append(
        check(
            "btc policies are horizons 5 and 15", set(btc["horizon_seconds"]) == {5, 15}
        )
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    run_dir = args.run_dir or newest_run(args.root)
    checks = validate(run_dir)
    for item in checks:
        print(
            f"{'PASS' if item['passed'] else 'FAIL'}  {item['check']} "
            f"{item['details'] if item['details'] != '' else ''}"
        )
    report = {"run_dir": str(run_dir), "checks": checks}
    (run_dir / "validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0 if all(item["passed"] for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
