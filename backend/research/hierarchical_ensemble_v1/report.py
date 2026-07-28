"""Readiness report for the universal target-specific forecast ledger."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb

from backend.research.forecast_adapters_v1.catalog import TARGET_SPECS


def _adapter_readiness(path: Path | None) -> list[dict[str, object]]:
    if path is not None and path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("campaign") != "MODEL_FORECAST_ADAPTERS_V1":
            raise ValueError("invalid forecast-adapter readiness report")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("forecast-adapter readiness rows are missing")
        return [dict(row) for row in rows]
    return [
        {
            "adapter_id": spec.adapter_id,
            "source_campaign": spec.source_campaign,
            "source_head": spec.source_head,
            "model_id": spec.model_id,
            "contract_key": spec.contract.key,
            "target_name": spec.contract.target_name,
            "target_role": spec.contract.role.value,
            "venue": spec.contract.venue,
            "instrument": spec.contract.instrument,
            "horizon_seconds": spec.contract.horizon_seconds,
            "adapter_implemented": spec.adapter_implemented,
            "status": (
                "NOT_IMPLEMENTED"
                if not spec.adapter_implemented
                else "NOT_RUN"
            ),
            "blocker": spec.static_blocker,
            "source_rows": 0,
            "forecasts_seen": 0,
            "forecasts_inserted": 0,
            "outcomes_seen": 0,
            "outcomes_inserted": 0,
        }
        for spec in TARGET_SPECS
    ]


def build_report(
    ledger_path: Path,
    adapter_readiness_path: Path | None = None,
) -> dict[str, object]:
    if not ledger_path.is_file():
        raise FileNotFoundError(f"forecast ledger not found:{ledger_path}")
    with duckdb.connect(str(ledger_path), read_only=True) as con:
        tables = {
            str(row[0])
            for row in con.execute("SHOW TABLES").fetchall()
        }
        required = {"model_forecasts", "model_forecast_outcomes"}
        if not required.issubset(tables):
            raise ValueError("database is not a MODEL_FORECAST_LEDGER_V1 ledger")
        totals = con.execute(
            "SELECT count(*), count(DISTINCT model_id), "
            "count(DISTINCT contract_key) FROM model_forecasts"
        ).fetchone()
        resolved = int(
            con.execute(
                "SELECT count(*) FROM model_forecast_outcomes"
            ).fetchone()[0]
        )
        rows = con.execute(
            "SELECT f.contract_key, f.target_name, f.target_role, f.venue, "
            "f.instrument, f.horizon_seconds, f.evidence_kind, "
            "count(*) AS forecasts, count(DISTINCT f.model_id) AS models, "
            "count(o.forecast_id) AS resolved_forecasts, "
            "count(DISTINCT CASE WHEN o.forecast_id IS NOT NULL "
            "THEN f.candidate_id END) AS resolved_candidates "
            "FROM model_forecasts f LEFT JOIN model_forecast_outcomes o "
            "USING(forecast_id) "
            "GROUP BY ALL ORDER BY f.target_name, f.horizon_seconds, "
            "f.evidence_kind"
        ).fetchall()
        aligned_rows = con.execute(
            "WITH panels AS ("
            "  SELECT f.contract_key, f.evidence_kind, f.forecast_at_ns, "
            "    f.candidate_id, "
            "    array_to_string(list_sort(list(DISTINCT "
            "      f.model_id || ':' || f.model_version)), ',') AS model_set "
            "  FROM model_forecasts f JOIN model_forecast_outcomes o "
            "    USING(forecast_id) "
            "  WHERE f.evidence_kind IN ('OOF', 'FORWARD') "
            "  GROUP BY f.contract_key, f.evidence_kind, f.forecast_at_ns, "
            "    f.candidate_id "
            "  HAVING count(DISTINCT f.model_id || ':' || f.model_version) >= 2"
            "), consistent_panels AS ("
            "  SELECT contract_key, evidence_kind, model_set, count(*) AS n "
            "  FROM panels GROUP BY contract_key, evidence_kind, model_set"
            ") "
            "SELECT contract_key, evidence_kind, max(n) "
            "FROM consistent_panels GROUP BY contract_key, evidence_kind"
        ).fetchall()
    columns = (
        "contract_key",
        "target_name",
        "target_role",
        "venue",
        "instrument",
        "horizon_seconds",
        "evidence_kind",
        "forecasts",
        "models",
        "resolved_forecasts",
        "resolved_candidates",
    )
    aligned = {
        (str(contract_key), str(evidence_kind)): int(count)
        for contract_key, evidence_kind, count in aligned_rows
    }
    targets = []
    for row in rows:
        item = dict(zip(columns, row, strict=True))
        item["aligned_resolved_candidates"] = aligned.get(
            (str(item["contract_key"]), str(item["evidence_kind"])),
            0,
        )
        targets.append(item)
    eligible = [
        item
        for item in targets
        if item["evidence_kind"] in {"OOF", "FORWARD"}
        and int(item["models"]) >= 2
        and int(item["aligned_resolved_candidates"]) >= 20
    ]
    adapters = _adapter_readiness(adapter_readiness_path)
    adapter_keys = {str(item["contract_key"]) for item in adapters}
    ledger_keys = {str(item["contract_key"]) for item in targets}
    unknown_contracts = sorted(ledger_keys - adapter_keys)
    return {
        "campaign": "HIERARCHICAL_TARGET_SPECIFIC_ENSEMBLE_V1",
        "status": (
            "META_TRAINING_DATA_AVAILABLE" if eligible else "COLLECT_EVIDENCE"
        ),
        "forecasts": int(totals[0]),
        "models": int(totals[1]),
        "target_contracts": int(totals[2]),
        "resolved_forecasts": resolved,
        "eligible_target_slices": len(eligible),
        "targets": targets,
        "adapter_readiness": adapters,
        "uncatalogued_contract_keys": unknown_contracts,
        "restrictions": {
            "serving_enabled": False,
            "paper_enabled": False,
            "live_enabled": False,
            "may_submit_orders": False,
        },
    }


def write_report(report: dict[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    targets = list(report["targets"])
    with (output_dir / "target_readiness.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        if targets:
            writer = csv.DictWriter(handle, fieldnames=list(targets[0]))
            writer.writeheader()
            writer.writerows(targets)
        else:
            handle.write(
                "contract_key,target_name,target_role,venue,instrument,"
                "horizon_seconds,evidence_kind,forecasts,models,"
                "resolved_forecasts,resolved_candidates,"
                "aligned_resolved_candidates\n"
            )
    adapters = list(report["adapter_readiness"])
    with (output_dir / "adapter_readiness.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(adapters[0]))
        writer.writeheader()
        writer.writerows(adapters)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("data/research/model_forecast_ledger_v1.duckdb"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research/hierarchical_ensemble_v1/report"),
    )
    parser.add_argument(
        "--adapter-readiness",
        type=Path,
        default=Path(
            "data/research/model_forecast_adapters_v1/readiness.json"
        ),
    )
    args = parser.parse_args()
    report = build_report(args.ledger, args.adapter_readiness)
    write_report(report, args.output)
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"targets", "adapter_readiness"}
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
