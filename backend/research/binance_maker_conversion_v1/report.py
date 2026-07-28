#!/usr/bin/env python
"""Evidence report for BINANCE_MAKER_CONVERSION_V1."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = (
    ROOT / "data" / "research" / "binance_maker_conversion_v1" / "shadow.duckdb"
)


def profit_factor(values: list[float]) -> float:
    gains = sum(value for value in values if value > 0.0)
    losses = -sum(value for value in values if value < 0.0)
    return gains / losses if losses > 0.0 else math.inf if gains > 0.0 else math.nan


def lower_95_day_block(days: list[float]) -> float | None:
    if len(days) < 5:
        return None
    rng = np.random.default_rng(20260728)
    values = np.asarray(days, dtype=float)
    means = np.empty(2000, dtype=float)
    for index in range(len(means)):
        means[index] = rng.choice(values, size=len(values), replace=True).mean()
    return float(np.quantile(means, 0.025))


def policy_detail(db: duckdb.DuckDBPyConnection, policy: str) -> dict:
    rows = db.execute(
        """
        SELECT c.decision_ts_ms, c.side,
               COALESCE(r.candidate_weighted_net_bps, 0.0) AS pnl,
               r.status, r.entry_filled_quantity / NULLIF(r.quantity, 0)
        FROM candidates c
        JOIN routes r ON r.candidate_id=c.candidate_id
        WHERE r.policy=?
        ORDER BY c.decision_ts_ms
        """,
        [policy],
    ).fetchall()
    pnl = [float(row[2]) for row in rows]
    long_pnl = [float(row[2]) for row in rows if row[1] == "LONG"]
    short_pnl = [float(row[2]) for row in rows if row[1] == "SHORT"]
    day_values: dict[str, list[float]] = {}
    week_values: dict[str, float] = {}
    for timestamp, _side, value, _status, _fill in rows:
        moment = datetime.fromtimestamp(float(timestamp) / 1000.0, tz=timezone.utc)
        day = moment.date().isoformat()
        iso = moment.isocalendar()
        week = f"{iso.year:04d}-W{iso.week:02d}"
        day_values.setdefault(day, []).append(float(value))
        week_values[week] = week_values.get(week, 0.0) + float(value)
    daily_means = [
        float(np.mean(values)) for values in day_values.values() if values
    ]
    positive_weeks = [value for value in week_values.values() if value > 0.0]
    week_concentration = (
        max(positive_weeks) / sum(positive_weeks) if positive_weeks else None
    )
    split = max(1, int(len(pnl) * 0.8)) if pnl else 0
    return {
        "expectancy_per_original_candidate_bps": float(np.mean(pnl))
        if pnl
        else None,
        "profit_factor": profit_factor(pnl),
        "long_expectancy_bps": float(np.mean(long_pnl)) if long_pnl else None,
        "short_expectancy_bps": float(np.mean(short_pnl)) if short_pnl else None,
        "day_block_expectancy_lower_95_bps": lower_95_day_block(daily_means),
        "best_week_positive_profit_share": week_concentration,
        "final_untouched_20pct_expectancy_bps": float(np.mean(pnl[split:]))
        if len(pnl) > split
        else None,
        "unresolved_or_incomplete": sum(
            row[3] in {"ACTIVE", "INTERRUPTED", "UNWOUND_INCOMPLETE"}
            for row in rows
        ),
    }


def taker_latency_stress(
    db: duckdb.DuckDBPyConnection, offset_ms: int, taker_fee_bps: float
) -> float | None:
    rows = db.execute(
        """
        SELECT c.side, c.quantity,
               CASE WHEN c.side='LONG' THEN b.long_vwap_1x
                    ELSE b.short_vwap_1x END AS entry_price,
               CASE WHEN c.side='LONG' THEN b.long_fill_1x
                    ELSE b.short_fill_1x END AS entry_fill,
               r.exit_price, r.exit_filled_quantity
        FROM candidates c
        LEFT JOIN candidate_book_checkpoints b
          ON b.candidate_id=c.candidate_id AND b.offset_ms=?
        JOIN routes r ON r.candidate_id=c.candidate_id
        WHERE r.policy='A_TAKER_TAKER'
        """,
        [offset_ms],
    ).fetchall()
    values = []
    for side, quantity, entry, entry_fill, exit_price, exit_fill in rows:
        if (
            entry is None
            or exit_price is None
            or float(entry_fill or 0.0) + 1e-12 < float(quantity)
            or float(exit_fill or 0.0) + 1e-12 < float(quantity)
        ):
            values.append(0.0)
            continue
        direction = 1.0 if side == "LONG" else -1.0
        entry_price = float(entry)
        exit_value = float(exit_price)
        fee_bps = (
            entry_price * taker_fee_bps + exit_value * taker_fee_bps
        ) / entry_price
        values.append(
            direction * (exit_value / entry_price - 1.0) * 10_000.0
            - fee_bps
        )
    return float(np.mean(values)) if values else None


def one_tick_worse_stress(
    db: duckdb.DuckDBPyConnection,
    policy: str,
    tick_size: float,
    maker_fee_bps: float,
    taker_fee_bps: float,
) -> float | None:
    rows = db.execute(
        """
        SELECT c.side, r.entry_price, r.exit_price, r.status,
               r.entry_maker_quantity, r.entry_taker_quantity,
               r.entry_maker_notional, r.entry_taker_notional,
               r.exit_maker_quantity, r.exit_taker_quantity,
               r.exit_maker_notional, r.exit_taker_notional
        FROM candidates c JOIN routes r USING(candidate_id)
        WHERE r.policy=? ORDER BY c.decision_ts_ms
        """,
        [policy],
    ).fetchall()
    values = []
    for row in rows:
        (
            side,
            entry,
            exit_price,
            status,
            entry_maker_qty,
            entry_taker_qty,
            entry_maker_notional,
            entry_taker_notional,
            exit_maker_qty,
            exit_taker_qty,
            exit_maker_notional,
            exit_taker_notional,
        ) = row
        if status != "RESOLVED" or entry is None or exit_price is None:
            values.append(0.0)
            continue
        if side == "LONG":
            stressed_entry = float(entry) + tick_size
            stressed_exit = float(exit_price) - tick_size
            direction = 1.0
        else:
            stressed_entry = float(entry) - tick_size
            stressed_exit = float(exit_price) + tick_size
            direction = -1.0
        entry_shift = tick_size if side == "LONG" else -tick_size
        exit_shift = -tick_size if side == "LONG" else tick_size
        stressed_entry_maker = float(entry_maker_notional or 0.0) + entry_shift * float(
            entry_maker_qty or 0.0
        )
        stressed_entry_taker = float(entry_taker_notional or 0.0) + entry_shift * float(
            entry_taker_qty or 0.0
        )
        stressed_exit_maker = float(exit_maker_notional or 0.0) + exit_shift * float(
            exit_maker_qty or 0.0
        )
        stressed_exit_taker = float(exit_taker_notional or 0.0) + exit_shift * float(
            exit_taker_qty or 0.0
        )
        stressed_entry_notional = stressed_entry_maker + stressed_entry_taker
        fee_usd = (
            stressed_entry_maker * maker_fee_bps
            + stressed_entry_taker * taker_fee_bps
            + stressed_exit_maker * maker_fee_bps
            + stressed_exit_taker * taker_fee_bps
        ) / 10_000.0
        fee_bps = fee_usd / max(stressed_entry_notional, 1e-12) * 10_000.0
        values.append(
            direction * (stressed_exit / stressed_entry - 1.0) * 10_000.0
            - fee_bps
        )
    return float(np.mean(values)) if values else None


def two_x_taker_size_stress(
    db: duckdb.DuckDBPyConnection,
    taker_fee_bps: float,
) -> float | None:
    rows = db.execute(
        """
        SELECT c.side, c.quantity,
               CASE WHEN c.side='LONG' THEN entry.long_vwap_2x
                    ELSE entry.short_vwap_2x END AS entry_price,
               CASE WHEN c.side='LONG' THEN entry.long_fill_2x
                    ELSE entry.short_fill_2x END AS entry_fill,
               CASE WHEN c.side='LONG' THEN exit.short_vwap_2x
                    ELSE exit.long_vwap_2x END AS exit_price,
               CASE WHEN c.side='LONG' THEN exit.short_fill_2x
                    ELSE exit.long_fill_2x END AS exit_fill
        FROM candidates c
        LEFT JOIN candidate_book_checkpoints entry
          ON entry.candidate_id=c.candidate_id AND entry.offset_ms=0
        LEFT JOIN candidate_book_checkpoints exit
          ON exit.candidate_id=c.candidate_id
         AND exit.offset_ms=c.horizon_seconds*1000
        ORDER BY c.decision_ts_ms
        """
    ).fetchall()
    values = []
    for side, quantity, entry, entry_fill, exit_price, exit_fill in rows:
        required = float(quantity) * 2.0
        if (
            entry is None
            or exit_price is None
            or float(entry_fill or 0.0) + 1e-12 < required
            or float(exit_fill or 0.0) + 1e-12 < required
        ):
            values.append(0.0)
            continue
        direction = 1.0 if side == "LONG" else -1.0
        entry_price = float(entry)
        exit_value = float(exit_price)
        fee_bps = (
            entry_price * taker_fee_bps + exit_value * taker_fee_bps
        ) / entry_price
        values.append(
            direction * (exit_value / entry_price - 1.0) * 10_000.0
            - fee_bps
        )
    return float(np.mean(values)) if values else None


def doubled_fee_stress(
    db: duckdb.DuckDBPyConnection, policy: str
) -> float | None:
    values = [
        float(row[0])
        for row in db.execute(
            """
            SELECT CASE WHEN status='RESOLVED'
                        THEN gross_bps - 2.0*fee_bps
                        ELSE 0.0 END
            FROM routes WHERE policy=?
            """,
            [policy],
        ).fetchall()
        if row[0] is not None
    ]
    return float(np.mean(values)) if values else None


def summarize(path: Path) -> dict:
    db = duckdb.connect(str(path), read_only=True)
    candidates = db.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT CAST(to_timestamp(decision_ts_ms/1000)
            AS DATE)), MIN(decision_ts_ms), MAX(decision_ts_ms),
            SUM(side='LONG'), SUM(side='SHORT')
        FROM candidates
        """
    ).fetchone()
    policy_rows = db.execute(
        """
        SELECT policy, COUNT(*) AS original_candidates,
               SUM(status='RESOLVED') AS resolved,
               SUM(entry_status='FILLED') AS full_entry_fills,
               AVG(entry_filled_quantity / NULLIF(quantity, 0)) AS fill_rate,
               AVG(COALESCE(candidate_weighted_net_bps, 0.0)) AS expectancy_bps,
               SUM(COALESCE(candidate_weighted_net_bps, 0.0)) AS total_bps
        FROM routes GROUP BY policy ORDER BY policy
        """
    ).fetchall()
    policies = []
    for row in policy_rows:
        pnl = [
            float(value[0])
            for value in db.execute(
                """
                SELECT COALESCE(candidate_weighted_net_bps, 0.0) FROM routes
                WHERE policy=?
                """,
                [row[0]],
            ).fetchall()
        ]
        detail = policy_detail(db, row[0])
        policies.append(
            {
                "policy": row[0],
                "original_candidates": int(row[1]),
                "resolved": int(row[2] or 0),
                "full_entry_fills": int(row[3] or 0),
                "fill_rate": float(row[4]) if row[4] is not None else None,
                "expectancy_bps": float(row[5]) if row[5] is not None else None,
                "total_bps": float(row[6]) if row[6] is not None else None,
                "profit_factor": profit_factor(pnl),
                **detail,
            }
        )
    meta = {
        key: json.loads(value)
        for key, value in db.execute(
            "SELECT key, value_json FROM campaign_meta"
        ).fetchall()
    }
    gate = meta.get("protocol", {}).get("promotion_gate", {})
    execution = meta.get("protocol", {}).get("execution", {})
    days = int(candidates[1] or 0)
    count = int(candidates[0] or 0)
    primary = next(
        (
            row
            for row in policies
            if row["policy"] == "C_MAKER_TTL_FALLBACK_TAKER"
        ),
        None,
    )
    taker_fee_bps = float(execution.get("taker_fee_bps", 5.0))
    maker_fee_bps = float(execution.get("maker_fee_bps", 2.0))
    latency_250 = taker_latency_stress(db, 250, taker_fee_bps)
    latency_500 = taker_latency_stress(db, 500, taker_fee_bps)
    tick_size = float(
        meta.get("protocol", {}).get("instrument", {}).get("tick_size", 0.1)
    )
    one_tick_worse = one_tick_worse_stress(
        db,
        "C_MAKER_TTL_FALLBACK_TAKER",
        tick_size,
        maker_fee_bps,
        taker_fee_bps,
    )
    two_x_size = two_x_taker_size_stress(db, taker_fee_bps)
    two_x_fees = doubled_fee_stress(
        db, "C_MAKER_TTL_FALLBACK_TAKER"
    )
    db.close()
    gate_checks = {
        "sample_gate": count >= int(gate.get("minimum_original_candidates", 1000)),
        "duration_gate": days >= int(gate.get("minimum_calendar_days", 56)),
        "account_fee_verified": bool(execution.get("account_fee_verified", False)),
        "profit_factor": bool(
            primary
            and primary["profit_factor"] is not None
            and primary["profit_factor"] >= float(gate.get("minimum_profit_factor", 1.2))
        ),
        "day_block_lower_bound": bool(
            primary
            and primary["day_block_expectancy_lower_95_bps"] is not None
            and primary["day_block_expectancy_lower_95_bps"]
            > float(gate.get("day_block_expectancy_lower_95_bps", 0.0))
        ),
        "long_positive": bool(
            primary
            and primary["long_expectancy_bps"] is not None
            and primary["long_expectancy_bps"] > 0.0
        ),
        "short_positive": bool(
            primary
            and primary["short_expectancy_bps"] is not None
            and primary["short_expectancy_bps"] > 0.0
        ),
        "week_concentration": bool(
            primary
            and primary["best_week_positive_profit_share"] is not None
            and primary["best_week_positive_profit_share"]
            <= float(gate.get("maximum_best_week_profit_share", 0.35))
        ),
        "final_untouched_positive": bool(
            primary
            and primary["final_untouched_20pct_expectancy_bps"] is not None
            and primary["final_untouched_20pct_expectancy_bps"] > 0.0
        ),
        "latency_250ms_positive": bool(latency_250 is not None and latency_250 > 0.0),
        "latency_500ms_positive": bool(latency_500 is not None and latency_500 > 0.0),
        "no_unresolved_exposure": bool(
            primary and primary["unresolved_or_incomplete"] == 0
        ),
        "one_tick_worse": bool(
            one_tick_worse is not None and one_tick_worse > 0.0
        ),
        "two_x_size": bool(two_x_size is not None and two_x_size > 0.0),
        "two_x_fees": bool(two_x_fees is not None and two_x_fees > 0.0),
    }
    return {
        "database": str(path),
        "candidates": count,
        "calendar_days": days,
        "long_candidates": int(candidates[4] or 0),
        "short_candidates": int(candidates[5] or 0),
        "minimum_ts_ms": candidates[2],
        "maximum_ts_ms": candidates[3],
        "policies": policies,
        "stress_diagnostics": {
            "taker_latency_250ms_expectancy_bps": latency_250,
            "taker_latency_500ms_expectancy_bps": latency_500,
            "primary_one_tick_worse_expectancy_bps": one_tick_worse,
            "taker_two_x_size_expectancy_bps": two_x_size,
            "primary_two_x_fee_expectancy_bps": two_x_fees,
        },
        "promotion_readiness": {
            "gate_checks": gate_checks,
            "status": "NOT_ELIGIBLE",
            "note": (
                "Promotion remains blocked until all frozen gates, fee verification, "
                "stress tests and final untouched validation pass."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = summarize(args.db.resolve())
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(
            f"BINANCE_MAKER_CONVERSION_V1 candidates={report['candidates']:,} "
            f"days={report['calendar_days']} long={report['long_candidates']:,} "
            f"short={report['short_candidates']:,}"
        )
        for row in report["policies"]:
            expectancy = row["expectancy_bps"]
            print(
                f"  {row['policy']}: n={row['original_candidates']:,} "
                f"resolved={row['resolved']:,} fill={row['fill_rate']!s} "
                f"net={expectancy!s}bps PF={row['profit_factor']!s}"
            )
        print("  promotion: NOT_ELIGIBLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
