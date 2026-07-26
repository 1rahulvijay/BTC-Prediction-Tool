"""Print a compact evidence and artifact-integrity report for the shadow lane."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .model_common import artifact_issues
from .trade_forecast_logger import DB_PATH, connect, status as ledger_status
from .trade_schema import BTC_FEATURE_COLUMNS, FEATURE_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)
ARTIFACTS = {
    "share": DATA / "saved_models" / "complete_trade_share_path.pkl",
    "btc": DATA / "saved_models" / "complete_trade_btc_path.pkl",
    "execution": DATA / "saved_models" / "complete_trade_execution_heads.pkl",
}


def _selected_families(value: Any) -> list[str]:
    selected: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("selected_family"):
                selected.add(str(item["selected_family"]))
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return sorted(selected)


def build_report(dataset_path: Path, db_path: Path) -> dict[str, Any]:
    manifest_path = dataset_path.with_suffix(".manifest.json")
    dataset_manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as handle:
            dataset_manifest = json.load(handle)
    models = {}
    for name, path in ARTIFACTS.items():
        manifest, issues = artifact_issues(
            path,
            expected_feature_columns=(
                BTC_FEATURE_COLUMNS if name == "btc" else FEATURE_COLUMNS
            ),
        )
        models[name] = {
            "artifact": str(path),
            "valid": not issues,
            "issues": issues,
            "training_status": manifest.get("training_status"),
            "input_promotable": manifest.get("input_promotable"),
            "m0_passed": manifest.get("m0_passed"),
            "selected_families": _selected_families(
                manifest.get("metrics") or {}
            ),
        }
    try:
        conn = connect(db_path)
        try:
            ledger = ledger_status(conn)
        finally:
            conn.close()
    except Exception as exc:
        ledger = {"error": f"{type(exc).__name__}: {exc}"}
    share = models["share"]
    actionable = bool(
        dataset_manifest.get("promotable")
        and share.get("valid")
        and share.get("m0_passed")
        and all(model.get("valid") for model in models.values())
    )
    return {
        "mode": "SHADOW_PILOT_ONLY",
        "actionable": actionable,
        "dataset": {
            "path": str(dataset_path),
            "status": dataset_manifest.get("status") or "MISSING",
            "rows": dataset_manifest.get("rows"),
            "independent_rounds": dataset_manifest.get("independent_rounds"),
            "calendar_weeks": dataset_manifest.get("calendar_weeks"),
            "promotable": dataset_manifest.get("promotable", False),
        },
        "models": models,
        "ledger": ledger,
        "plain_result": (
            "Shadow evidence gates passed. Champion still remains unchanged."
            if actionable
            else "NO TRADE: at least one evidence, M0, or artifact-integrity gate is open."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    report = build_report(args.dataset.resolve(), args.db.resolve())
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
