#!/usr/bin/env python
"""Generate the complete-set forward-evidence report and fail-closed gate status."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_ROOT = DATA / "research" / "poly_complete_set_arbitrage_v1"
DEFAULT_DB = DEFAULT_ROOT / "shadow.duckdb"
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")


def _scalar(conn: duckdb.DuckDBPyConnection, query: str, params=None) -> Any:
    row = conn.execute(query, params or []).fetchone()
    return row[0] if row else None


def _mean_lower_95(values: list[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) == 1:
        return None
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean - 1.96 * math.sqrt(variance / len(values))


def build_report(db_path: Path, output_root: Path) -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if not db_path.is_file():
        result = {
            "protocol_id": protocol["protocol_id"],
            "status": "NO_DATA",
            "promotion_ready": False,
            "blockers": ["shadow_database_missing"],
        }
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "report.json").write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        return result

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        snapshots = int(_scalar(conn, "SELECT count(*) FROM complete_set_snapshots"))
        qualified = int(
            _scalar(
                conn,
                "SELECT count(*) FROM complete_set_snapshots WHERE qualified",
            )
        )
        raw_buy = int(
            _scalar(
                conn,
                """
                SELECT count(*) FROM complete_set_snapshots
                WHERE raw_buy_gap_usd > 0
                """,
            )
        )
        raw_sell = int(
            _scalar(
                conn,
                """
                SELECT count(*) FROM complete_set_snapshots
                WHERE raw_sell_gap_usd > 0
                """,
            )
        )
        opportunities = int(
            _scalar(conn, "SELECT count(*) FROM complete_set_opportunities")
        )
        open_count = int(
            _scalar(
                conn,
                """
                SELECT count(*) FROM complete_set_opportunities
                WHERE status='OPEN'
                """,
            )
        )
        opportunity_rows = conn.execute(
            """
            SELECT direction, quantity, count(*), avg(entry_conservative_net_usd),
                   max(maximum_conservative_net_usd), avg(duration_ms)
            FROM complete_set_opportunities
            GROUP BY direction, quantity
            ORDER BY direction, quantity
            """
        ).fetchall()
        delay_rows = conn.execute(
            """
            SELECT target_delay_ms, count(*),
                   avg(CASE WHEN survives_positive THEN 1.0 ELSE 0.0 END),
                   avg(current_pair_net_usd),
                   avg(failed_leg_worst_net_usd),
                   max(actual_delay_ms)
            FROM complete_set_delay_stress
            GROUP BY target_delay_ms
            ORDER BY target_delay_ms
            """
        ).fetchall()
        terminal_rows = conn.execute(
            """
            SELECT o.started_ts_ns, d.current_pair_net_usd,
                   d.failed_leg_worst_net_usd
            FROM complete_set_opportunities o
            JOIN complete_set_delay_stress d USING(opportunity_id)
            WHERE d.target_delay_ms=1000
            ORDER BY o.started_ts_ns
            """
        ).fetchall()
    finally:
        conn.close()

    gate = protocol["forward_gate"]
    day_totals: dict[str, float] = {}
    week_totals: dict[str, float] = {}
    terminal_net: list[float] = []
    for started_ns, current_net, _ in terminal_rows:
        timestamp = datetime.fromtimestamp(float(started_ns) / 1e9, UTC)
        value = float(current_net) if current_net is not None else 0.0
        terminal_net.append(value)
        day_key = timestamp.strftime("%Y-%m-%d")
        week_key = timestamp.strftime("%G-%V")
        day_totals[day_key] = day_totals.get(day_key, 0.0) + value
        week_totals[week_key] = week_totals.get(week_key, 0.0) + value
    day_values = list(day_totals.values())
    weeks = len(week_totals)
    positive_weeks = [value for value in week_totals.values() if value > 0.0]
    positive_week_fraction = (
        len(positive_weeks) / weeks if weeks else None
    )
    positive_total = sum(positive_weeks)
    maximum_week_share = (
        max(positive_weeks) / positive_total
        if positive_weeks and positive_total > 0.0
        else None
    )
    gross_profit = sum(value for value in terminal_net if value > 0.0)
    gross_loss = -sum(value for value in terminal_net if value < 0.0)
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0.0
        else (math.inf if gross_profit > 0.0 else None)
    )
    delay_lookup = {
        int(row[0]): {
            "n": int(row[1]),
            "survival_rate": float(row[2]) if row[2] is not None else None,
            "mean_current_pair_net_usd": (
                float(row[3]) if row[3] is not None else None
            ),
            "mean_failed_leg_worst_net_usd": (
                float(row[4]) if row[4] is not None else None
            ),
            "maximum_actual_delay_ms": (
                float(row[5]) if row[5] is not None else None
            ),
        }
        for row in delay_rows
    }
    blockers: list[str] = []
    if opportunities < gate["minimum_independent_opportunities"]:
        blockers.append("insufficient_independent_opportunities")
    if weeks < gate["minimum_continuous_weeks"]:
        blockers.append("insufficient_continuous_weeks")
    if (
        positive_week_fraction is None
        or positive_week_fraction < gate["minimum_positive_week_fraction"]
    ):
        blockers.append("positive_week_fraction_below_gate")
    if profit_factor is None or profit_factor < gate["minimum_profit_factor"]:
        blockers.append("profit_factor_below_gate")
    if (
        maximum_week_share is None
        or maximum_week_share > gate["maximum_single_week_profit_share"]
    ):
        blockers.append("weekly_profit_concentration_above_gate")
    for target in protocol["delay_stress_ms"]:
        metrics = delay_lookup.get(int(target))
        if not metrics or not metrics["n"]:
            blockers.append(f"missing_{target}ms_delay_stress")
        elif metrics["n"] < opportunities:
            blockers.append(f"incomplete_{target}ms_delay_coverage")
        elif (
            gate["require_all_delay_stresses_positive"]
            and (
                metrics["mean_current_pair_net_usd"] is None
                or metrics["mean_current_pair_net_usd"] <= 0.0
            )
        ):
            blockers.append(f"nonpositive_{target}ms_pair_net")
    day_lb = _mean_lower_95(day_values)
    if (
        day_lb is None
        or day_lb <= gate["minimum_day_block_net_lower_95_usd"]
    ):
        blockers.append("day_block_lower_95_not_positive")
    if (
        gate["require_measured_operational_cost"]
        and protocol["economics"]["operational_cost_status"] != "measured"
    ):
        blockers.append("operational_cost_not_measured")
    if gate["require_real_two_leg_fill_evidence"]:
        blockers.append("real_two_leg_fill_evidence_missing")
    if gate["require_failed_leg_stress_inside_limit"]:
        blockers.append("failed_leg_loss_limit_not_predeclared")
    if gate["require_final_untouched_period_positive"]:
        blockers.append("final_untouched_period_not_available")
    if (
        gate["require_zero_rebate_profitability"]
        and protocol["economics"]["maker_rebate_usd"] != 0.0
    ):
        blockers.append("zero_rebate_economics_not_tested")

    result = {
        "protocol_id": protocol["protocol_id"],
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "promotion_ready": not blockers,
        "blockers": sorted(set(blockers)),
        "counts": {
            "snapshots": snapshots,
            "qualified_snapshots": qualified,
            "raw_buy_gap_snapshots": raw_buy,
            "raw_sell_gap_snapshots": raw_sell,
            "independent_opportunities": opportunities,
            "open_opportunities": open_count,
            "calendar_weeks": weeks,
        },
        "opportunities": [
            {
                "direction": row[0],
                "quantity": float(row[1]),
                "n": int(row[2]),
                "mean_entry_conservative_net_usd": (
                    float(row[3]) if row[3] is not None else None
                ),
                "maximum_conservative_net_usd": (
                    float(row[4]) if row[4] is not None else None
                ),
                "mean_duration_ms": (
                    float(row[5]) if row[5] is not None else None
                ),
            }
            for row in opportunity_rows
        ],
        "delay_stress": delay_lookup,
        "day_block_net_lower_95_usd": day_lb,
        "terminal_1000ms_profit_factor": profit_factor,
        "positive_week_fraction": positive_week_fraction,
        "maximum_single_week_profit_share": maximum_week_share,
        "important_limit": (
            "Book survival is not an observed two-order fill probability. "
            "Promotion remains blocked until real shadow orders or an equivalent "
            "exchange-confirmed fill record exists."
        ),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "report.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )

    lines = [
        "# POLY_COMPLETE_SET_ARBITRAGE_V1 Forward Report",
        "",
        f"Generated: `{result['generated_at_utc']}`",
        "",
        f"Status: **{result['status']}**",
        f"Promotion ready: **{result['promotion_ready']}**",
        "",
        "## Counts",
        "",
        f"- Snapshots: **{snapshots:,}** ({qualified:,} synchronized/qualified)",
        f"- Raw buy-both gaps: **{raw_buy:,}**",
        f"- Raw sell-both gaps: **{raw_sell:,}**",
        f"- Independent executable-book episodes: **{opportunities:,}**",
        f"- Calendar weeks: **{weeks}**",
        "",
        "## Opportunity Economics",
        "",
        "| direction | quantity | n | mean entry net | max net | mean duration |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["opportunities"]:
        lines.append(
            f"| {row['direction']} | {row['quantity']:g} | {row['n']} | "
            f"{row['mean_entry_conservative_net_usd'] or 0:+.5f} | "
            f"{row['maximum_conservative_net_usd'] or 0:+.5f} | "
            f"{row['mean_duration_ms'] or 0:.1f} ms |"
        )
    lines.extend(
        [
            "",
            "## Delay And Failed-Leg Stress",
            "",
            "| target | n | survives | current pair net | failed-leg worst net |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for target, metrics in sorted(delay_lookup.items()):
        lines.append(
            f"| {target} ms | {metrics['n']} | "
            f"{100 * (metrics['survival_rate'] or 0):.1f}% | "
            f"{metrics['mean_current_pair_net_usd'] or 0:+.5f} | "
            f"{metrics['mean_failed_leg_worst_net_usd'] or 0:+.5f} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Blockers",
            "",
            *[f"- `{blocker}`" for blocker in result["blockers"]],
            "",
            "## Interpretation",
            "",
            (
                "Raw top-of-book gaps are diagnostics only. An opportunity requires current "
                "token-specific fees, valid full ladders, exact equal size on both outcomes, "
                "fresh books and bounded cross-book timestamp skew."
            ),
            "",
            result["important_limit"],
            "",
        ]
    )
    (output_root / "REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    result = build_report(Path(args.db), Path(args.out))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
