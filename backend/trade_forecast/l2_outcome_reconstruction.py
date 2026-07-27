"""Reconstruct Ledger V2 economics from this host's immutable L2 recorder."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import duckdb

from .trade_forecast_logger import (
    OUTCOME_V2_SCHEMA_VERSION,
    connect,
    log_outcome_v2,
    read_forward_rows,
    read_resolved_outcomes,
)
from .trade_labels import entry_cost_per_share, evaluate_exit_plan, net_pnl_per_share
from .trade_schema import (
    MAX_DECISION_BOOK_AGE_S,
    OFFICIAL_RESOLUTION_SOURCES,
    validate_evidence_candidate,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_RECORDER_DB = Path(
    os.environ.get("BTC_EXEC_DB") or DATA / "execution_layer.duckdb"
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def recording_sha256(path: Path) -> str:
    """Hash the DuckDB database and its WAL as one recorder snapshot."""
    digest = hashlib.sha256()
    components = [path, Path(str(path) + ".wal")]
    found = False
    for component in components:
        if not component.is_file():
            continue
        found = True
        digest.update(component.name.encode("utf-8"))
        digest.update(component.stat().st_size.to_bytes(8, "big", signed=False))
        digest.update(bytes.fromhex(_hash_file(component)))
    if not found:
        raise FileNotFoundError(path)
    return digest.hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ladder(raw: Any, side: str) -> list[list[float]]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        rows = (payload or {}).get(side) or []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    parsed = []
    for level in rows:
        try:
            price, size = float(level[0]), float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(price) and math.isfinite(size) and price > 0 and size > 0:
            parsed.append([price, size])
    return parsed


def ladder_vwap(levels: list[list[float]], quantity: float) -> float | None:
    remaining = float(quantity)
    value = 0.0
    for price, size in levels:
        take = min(remaining, size)
        value += take * price
        remaining -= take
        if remaining <= 1e-9:
            return value / float(quantity)
    return None


def _snapshot_time(row: dict[str, Any]) -> float | None:
    return _finite(row.get("decision_ts")) or _finite(row.get("ts"))


def _entry_and_plan(
    forecast: dict[str, Any],
    snapshots: list[dict[str, Any]],
    settled_side: int,
    latency_ms: int,
) -> dict[str, Any] | None:
    prediction_ts = float(forecast["prediction_ts"])
    target_ts = prediction_ts + float(latency_ms) / 1000.0
    expiry_ts = prediction_ts + max(0.0, float(forecast["seconds_left"]))
    if target_ts > expiry_ts + 1e-9:
        return None
    side = str(forecast["side"])
    quantity = float(forecast["requested_qty"])
    ladder_column = "up_ladder" if side == "UP" else "down_ladder"

    entry_row = None
    entry_vwap = None
    for snapshot in snapshots:
        stamp = _snapshot_time(snapshot)
        if stamp is None or stamp + 1e-9 < target_ts:
            continue
        if stamp > expiry_ts + 1e-9:
            break
        # "500 ms latency" cannot silently become "the first quote 30 seconds later".
        if stamp - target_ts > MAX_DECISION_BOOK_AGE_S:
            break
        age = _finite(snapshot.get("book_age_s"))
        if age is None or age < 0.0 or age > MAX_DECISION_BOOK_AGE_S:
            continue
        value = ladder_vwap(_ladder(snapshot.get(ladder_column), "a"), quantity)
        if value is not None:
            entry_row, entry_vwap = snapshot, value
            break
    if entry_row is None or entry_vwap is None:
        return None

    entry_ts = float(_snapshot_time(entry_row))
    times: list[float] = []
    net_path: list[float] = []
    for snapshot in snapshots:
        stamp = _snapshot_time(snapshot)
        if stamp is None or stamp + 1e-9 < entry_ts:
            continue
        if stamp > expiry_ts + 1e-9:
            break
        age = _finite(snapshot.get("book_age_s"))
        if age is None or age < 0.0 or age > MAX_DECISION_BOOK_AGE_S:
            continue
        exit_vwap = ladder_vwap(_ladder(snapshot.get(ladder_column), "b"), quantity)
        if exit_vwap is None:
            # A partial bid is not an executable full-quantity exit.
            continue
        times.append(max(0.0, stamp - entry_ts))
        net_path.append(net_pnl_per_share(entry_vwap, exit_vwap))

    side_won = (side == "UP" and int(settled_side) == 1) or (
        side == "DOWN" and int(settled_side) == 0
    )
    settle_net = (1.0 if side_won else 0.0) - entry_cost_per_share(entry_vwap)
    plan = evaluate_exit_plan(
        str(forecast.get("exit_plan") or ""),
        times,
        net_path,
        settle_net,
    )
    return {
        "entry_filled": True,
        "entry_vwap": float(entry_vwap),
        "entry_snapshot_ts_s": entry_ts,
        "entry_latency_ms": max(0.0, (entry_ts - prediction_ts) * 1000.0),
        "plan_net": float(plan["net"]),
        "plan_exit_kind": plan["exit_kind"],
        "plan_holding_s": plan["holding_s"],
    }


def _fetch_snapshots(recorder: Any, round_id: str) -> list[dict[str, Any]]:
    cursor = recorder.execute(
        """
        SELECT ts, decision_ts, up_ladder, down_ladder, book_age_s
        FROM pm_round_snapshots
        WHERE slug = ?
        ORDER BY COALESCE(decision_ts, ts), ts
        """,
        [round_id],
    )
    names = [entry[0] for entry in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def reconstruct_v2_outcomes(
    *,
    evidence_run_id: str,
    recorder_db: Path = DEFAULT_RECORDER_DB,
    ledger_db: Path | None = None,
) -> dict[str, Any]:
    """Resolve one explicit evidence run from own L2 ladders and official settlements."""
    if not evidence_run_id:
        raise ValueError("evidence_run_id is required")
    recorder_path = Path(recorder_db)
    if not recorder_path.is_file():
        raise FileNotFoundError(recorder_path)
    source_hash_before = recording_sha256(recorder_path)
    recorder = duckdb.connect(str(recorder_path), read_only=True)
    ledger = connect(ledger_db)
    try:
        rows = read_forward_rows(ledger, evidence_run_id)
        already = set(read_resolved_outcomes(ledger))
        by_round: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_round.setdefault(str(row.get("round_id") or ""), []).append(row)

        reconstructed: dict[str, dict[str, Any]] = {}
        settlements: dict[str, dict[str, Any]] = {}
        skipped: list[tuple[str, str]] = []
        for round_id, round_rows in by_round.items():
            settlement = recorder.execute(
                """
                SELECT settled_side, resolution_source, resolved_at
                FROM pm_round_settlements WHERE slug = ?
                """,
                [round_id],
            ).fetchone()
            if settlement is None:
                skipped.extend((str(row["forecast_id"]), "no_official_settlement")
                               for row in round_rows)
                continue
            settled_side, resolution_source, resolved_at = settlement
            if str(resolution_source or "") not in OFFICIAL_RESOLUTION_SOURCES:
                skipped.extend((str(row["forecast_id"]), "unofficial_settlement")
                               for row in round_rows)
                continue
            snapshots = _fetch_snapshots(recorder, round_id)
            if not snapshots:
                skipped.extend((str(row["forecast_id"]), "no_l2_snapshots")
                               for row in round_rows)
                continue
            settlements[round_id] = {
                "settled_side": int(settled_side),
                "resolution_source": str(resolution_source),
                "resolved_at_s": float(resolved_at),
            }
            for row in round_rows:
                primary = _entry_and_plan(row, snapshots, int(settled_side), 500)
                stress = _entry_and_plan(row, snapshots, int(settled_side), 1000)
                if primary is None:
                    skipped.append((str(row["forecast_id"]), "entry_not_fully_fillable_500ms"))
                    continue
                reconstructed[str(row["forecast_id"])] = {
                    **primary,
                    "stress_1000ms_plan_net": (
                        float(stress["plan_net"]) if stress is not None else None
                    ),
                    "stress_entry_vwap": (
                        float(stress["entry_vwap"]) if stress is not None else None
                    ),
                }

        source_hash_after = recording_sha256(recorder_path)
        if source_hash_after != source_hash_before:
            raise RuntimeError(
                "recorder database changed during reconstruction; no outcomes were written"
            )

        written = 0
        for row in rows:
            forecast_id = str(row["forecast_id"])
            if forecast_id in already or forecast_id not in reconstructed:
                continue
            settlement = settlements.get(str(row.get("round_id") or ""))
            if settlement is None:
                continue
            # Clarification-001: alternatives must be eligible candidates from the SAME
            # checkpoint, never later observations and never malformed/stale candidates.
            alternatives = []
            for other in rows:
                other_id = str(other["forecast_id"])
                if (
                    other_id == forecast_id
                    or other.get("exposure_id") != row.get("exposure_id")
                    or validate_evidence_candidate(other)
                    or other_id not in reconstructed
                ):
                    continue
                alternatives.append(float(reconstructed[other_id]["plan_net"]))
            primary = reconstructed[forecast_id]
            log_outcome_v2({
                "forecast_id": forecast_id,
                "round_id": row.get("round_id"),
                "outcome_schema_version": OUTCOME_V2_SCHEMA_VERSION,
                "resolved_at_s": settlement["resolved_at_s"],
                "resolution_source": settlement["resolution_source"],
                "reconstruction_source": "OWN_L2_RECONSTRUCTION",
                "source_recording_sha256": source_hash_before,
                "settled_side": settlement["settled_side"],
                "entry_filled": primary["entry_filled"],
                "entry_vwap": primary["entry_vwap"],
                "entry_snapshot_ts_s": primary["entry_snapshot_ts_s"],
                "entry_latency_ms": primary["entry_latency_ms"],
                "plan_net": primary["plan_net"],
                "plan_exit_kind": primary["plan_exit_kind"],
                "plan_holding_s": primary["plan_holding_s"],
                "stress_1000ms_plan_net": primary["stress_1000ms_plan_net"],
                "stress_entry_vwap": primary["stress_entry_vwap"],
                "candidate_pnls_json": json.dumps(
                    alternatives, separators=(",", ":"), allow_nan=False
                ),
            }, ledger)
            written += 1

        return {
            "evidence_run_id": evidence_run_id,
            "predictions": len(rows),
            "written": written,
            "already_resolved": len(already),
            "skipped": len(skipped),
            "skip_reasons": skipped[:20],
            "source_recording_sha256": source_hash_before,
        }
    finally:
        recorder.close()
        ledger.close()
