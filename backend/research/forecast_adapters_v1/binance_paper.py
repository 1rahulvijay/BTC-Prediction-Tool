"""Readiness audit for Binance paper outputs that are not forecast contracts."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .catalog import TARGET_SPECS
from .common import AdapterReadiness


SPECS = tuple(
    spec
    for spec in TARGET_SPECS
    if spec.source_campaign == "BINANCE_PAPER"
)


def audit(source_db: Path) -> dict[str, AdapterReadiness]:
    results = {
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
            adapter_implemented=False,
            status="SOURCE_DB_MISSING",
            blocker=spec.static_blocker,
        )
        for spec in SPECS
    }
    if not source_db.is_file():
        return results
    try:
        with duckdb.connect(str(source_db), read_only=True) as con:
            tables = {
                str(row[0]) for row in con.execute("SHOW TABLES").fetchall()
            }
            required = {
                "binance_paper_signals",
                "binance_paper_fills",
                "binance_paper_trades",
            }
            if not required.issubset(tables):
                raise ValueError(
                    "source_tables_missing:"
                    + ",".join(sorted(required - tables))
                )
            signals = int(
                con.execute(
                    "SELECT count(*) FROM binance_paper_signals"
                ).fetchone()[0]
            )
            fills = int(
                con.execute(
                    "SELECT count(*) FROM binance_paper_fills"
                ).fetchone()[0]
            )
            trades = int(
                con.execute(
                    "SELECT count(*) FROM binance_paper_trades"
                ).fetchone()[0]
            )
    except (duckdb.Error, ValueError) as exc:
        for result in results.values():
            result.status = "SOURCE_DB_UNAVAILABLE"
            result.blocker = f"{result.blocker};{exc}"
        return results
    for spec in SPECS:
        result = results[spec.adapter_id]
        result.status = "SOURCE_PRESENT_OUTPUT_INELIGIBLE"
        result.source_rows = signals
        if "fill" in spec.adapter_id or "slippage" in spec.adapter_id:
            result.outcomes_seen = fills
        elif "holding_time" in spec.adapter_id or "net_return" in spec.adapter_id:
            result.outcomes_seen = trades
    return results

