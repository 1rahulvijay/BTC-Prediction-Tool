#!/usr/bin/env python
"""Populate the universal ledger from provenance-complete research evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from backend.quant_platform.forecast_ledger import ForecastLedger

from . import binance_maker, binance_paper, poly_1h, repricing
from .catalog import TARGET_SPECS
from .common import AdapterReadiness


ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_LEDGER = DATA / "research" / "model_forecast_ledger_v1.duckdb"
DEFAULT_OUTPUT = (
    DATA / "research" / "model_forecast_adapters_v1" / "readiness.json"
)
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")


def _load_protocol() -> dict[str, object]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    unsafe = (
        protocol["serving_enabled"],
        protocol["paper_enabled"],
        protocol["live_enabled"],
        protocol["may_submit_orders"],
        *protocol["boundaries"].values(),
    )
    if any(bool(value) for value in unsafe):
        raise RuntimeError("forecast-adapter research boundary was weakened")
    if protocol["evidence"]["admitted_kinds"] != ["FORWARD"]:
        raise RuntimeError("adapter evidence kind drifted")
    return protocol


def _static_readiness() -> dict[str, AdapterReadiness]:
    return {
        spec.adapter_id: AdapterReadiness(
            adapter_id=spec.adapter_id,
            source_campaign=spec.source_campaign,
            source_head=spec.source_head,
            model_id=spec.model_id,
            contract_key=spec.contract.key,
            target_name=spec.contract.target_name,
            target_role=spec.contract.role.value,
            venue=spec.contract.venue,
            instrument=spec.contract.instrument,
            horizon_seconds=spec.contract.horizon_seconds,
            adapter_implemented=spec.adapter_implemented,
            status=(
                "NOT_IMPLEMENTED"
                if not spec.adapter_implemented
                else "NOT_RUN"
            ),
            blocker=spec.static_blocker,
        )
        for spec in TARGET_SPECS
    }


def run(
    *,
    ledger_path: Path,
    output_path: Path,
    poly_1h_db: Path,
    repricing_db: Path,
    binance_maker_db: Path,
    binance_paper_db: Path,
) -> dict[str, object]:
    protocol = _load_protocol()
    ledger = ForecastLedger(ledger_path)
    readiness = _static_readiness()
    readiness.update(
        poly_1h.adapt(
            poly_1h_db,
            ledger,
            ROOT
            / "backend"
            / "research"
            / "poly_1h_digital_fair_value_v1"
            / "frozen_protocol.json",
        )
    )
    readiness.update(
        repricing.adapt(
            repricing_db,
            ledger,
            ROOT
            / "backend"
            / "research"
            / "polymarket_repricing_shadow_v1"
            / "frozen_protocol.json",
        )
    )
    readiness.update(binance_maker.adapt(binance_maker_db, ledger))
    readiness.update(binance_paper.audit(binance_paper_db))
    integrity_ok, integrity_reasons = ledger.verify_integrity()
    if not integrity_ok:
        raise RuntimeError(
            "universal forecast ledger integrity failure:"
            + ",".join(integrity_reasons)
        )
    rows = [
        readiness[spec.adapter_id].to_dict() for spec in TARGET_SPECS
    ]
    summary = {
        "campaign": "MODEL_FORECAST_ADAPTERS_V1",
        "protocol_id": protocol["protocol_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ledger_path": str(ledger_path.resolve()),
        "ledger_counts": ledger.counts(),
        "integrity_ok": True,
        "adapter_count": len(rows),
        "implemented_adapter_count": sum(
            bool(row["adapter_implemented"]) for row in rows
        ),
        "ready_resolved_count": sum(
            row["status"] == "READY_RESOLVED" for row in rows
        ),
        "rows": rows,
        "restrictions": {
            "serving_enabled": False,
            "paper_enabled": False,
            "live_enabled": False,
            "may_submit_orders": False,
            "may_train_models": False,
            "may_promote_models": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(
        output_path.suffix + f".{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--poly-1h-db",
        type=Path,
        default=(
            DATA
            / "research"
            / "poly_1h_digital_fair_value_v1"
            / "shadow.duckdb"
        ),
    )
    parser.add_argument(
        "--repricing-db",
        type=Path,
        default=(
            DATA
            / "research"
            / "polymarket_repricing_shadow_v1"
            / "shadow.duckdb"
        ),
    )
    parser.add_argument(
        "--binance-maker-db",
        type=Path,
        default=(
            DATA
            / "research"
            / "binance_maker_conversion_v1"
            / "shadow.duckdb"
        ),
    )
    parser.add_argument(
        "--binance-paper-db",
        type=Path,
        default=DATA / "binance_paper.duckdb",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run(
        ledger_path=args.ledger,
        output_path=args.output,
        poly_1h_db=args.poly_1h_db,
        repricing_db=args.repricing_db,
        binance_maker_db=args.binance_maker_db,
        binance_paper_db=args.binance_paper_db,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in summary.items()
                if key not in {"rows"}
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
