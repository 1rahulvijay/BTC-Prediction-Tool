#!/usr/bin/env python
"""Calibration, path-target and executable-EV report for the 1h campaign."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
DEFAULT_DB = DATA / "research" / "poly_1h_digital_fair_value_v1" / "shadow.duckdb"
DEFAULT_OUTPUT = DATA / "research" / "poly_1h_digital_fair_value_v1" / "report"
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")


def clipped_log_loss(targets: np.ndarray, probabilities: np.ndarray) -> float:
    values = np.clip(probabilities.astype(float), 1e-6, 1.0 - 1e-6)
    labels = targets.astype(float)
    return float(np.mean(-(labels * np.log(values) + (1.0 - labels) * np.log(1.0 - values))))


def lower_95_by_round(candidates: list[dict[str, Any]]) -> float | None:
    grouped: dict[str, list[float]] = {}
    for row in candidates:
        grouped.setdefault(str(row["slug"]), []).append(float(row["net_per_share"]))
    round_means = [float(np.mean(values)) for values in grouped.values()]
    if len(round_means) < 20:
        return None
    rng = np.random.default_rng(20260728)
    array = np.asarray(round_means, dtype=float)
    means = np.empty(3000, dtype=float)
    for index in range(len(means)):
        means[index] = float(rng.choice(array, len(array), replace=True).mean())
    return float(np.quantile(means, 0.025))


def checkpoint_rows(
    db: duckdb.DuckDBPyConnection, checkpoints: list[int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        selected = db.execute(
            """
            WITH ranked AS (
                SELECT s.*,
                       row_number() OVER (
                           PARTITION BY s.slug
                           ORDER BY abs(s.seconds_left - ?), s.observed_ts_ms
                       ) AS rank
                FROM hourly_snapshots s
                JOIN hourly_resolutions r USING(slug)
                WHERE s.valid
                  AND r.finalized_kline
                  AND abs(s.seconds_left - ?) <= 5
            )
            SELECT ranked.slug, ranked.observed_ts_ms, ranked.seconds_left,
                   ranked.binance_distance_bps, ranked.p_a_market,
                   ranked.p_b_distance_time, ranked.p_c_volatility_mixture,
                   ranked.up_ask, ranked.down_ask,
                   ranked.up_fee_rate, ranked.down_fee_rate,
                   ranked.vwap_json, ranked.fraction_above,
                   ranked.fraction_below, ranked.crossing_count,
                   ranked.binance_price,
                   r.binance_side, r.finalized_open, r.finalized_close,
                   greatest(
                       0,
                       coalesce((
                           SELECT max(f.crossing_count)
                           FROM hourly_snapshots f
                           WHERE f.slug=ranked.slug
                             AND f.valid
                             AND f.observed_ts_ms > ranked.observed_ts_ms
                       ), ranked.crossing_count) - ranked.crossing_count
                   ) AS future_crossings
            FROM ranked
            JOIN hourly_resolutions r USING(slug)
            WHERE rank=1
            ORDER BY ranked.slug
            """,
            [checkpoint, checkpoint],
        ).fetchall()
        for row in selected:
            rows.append(
                {
                    "checkpoint": checkpoint,
                    "slug": row[0],
                    "observed_ts_ms": int(row[1]),
                    "seconds_left": float(row[2]),
                    "distance_bps": float(row[3]),
                    "p_a": float(row[4]),
                    "p_b": float(row[5]),
                    "p_c": float(row[6]),
                    "up_ask": float(row[7]),
                    "down_ask": float(row[8]),
                    "up_fee_rate": float(row[9]),
                    "down_fee_rate": float(row[10]),
                    "vwap": json.loads(row[11]),
                    "fraction_above": float(row[12]),
                    "fraction_below": float(row[13]),
                    "crossing_count": int(row[14]),
                    "binance_price": float(row[15]),
                    "target_up": 1 if row[16] == "UP" else 0,
                    "finalized_open": float(row[17]),
                    "finalized_close": float(row[18]),
                    "future_crossings": int(row[19]),
                }
            )
    return rows


def probability_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for checkpoint in sorted({row["checkpoint"] for row in rows}, reverse=True):
        subset = [row for row in rows if row["checkpoint"] == checkpoint]
        targets = np.asarray([row["target_up"] for row in subset], dtype=float)
        models = {}
        for key in ("p_a", "p_b", "p_c"):
            probabilities = np.asarray([row[key] for row in subset], dtype=float)
            models[key] = {
                "n": len(subset),
                "brier": float(np.mean((probabilities - targets) ** 2)),
                "log_loss": clipped_log_loss(targets, probabilities),
                "accuracy_at_50": float(
                    np.mean((probabilities >= 0.5).astype(float) == targets)
                ),
                "mean_probability": float(probabilities.mean()),
                "event_rate": float(targets.mean()),
            }
        output[str(checkpoint)] = models
    return output


def calibration_buckets(
    rows: list[dict[str, Any]], price_buckets: list[list[float]]
) -> list[dict[str, Any]]:
    output = []
    for model in ("p_a", "p_b", "p_c"):
        for checkpoint in sorted({row["checkpoint"] for row in rows}, reverse=True):
            subset = [row for row in rows if row["checkpoint"] == checkpoint]
            for lower, upper in price_buckets:
                bucket = [
                    row
                    for row in subset
                    if float(lower) <= row[model] < float(upper)
                ]
                if not bucket:
                    continue
                output.append(
                    {
                        "model": model,
                        "checkpoint_seconds_left": checkpoint,
                        "lower": lower,
                        "upper": upper,
                        "n": len(bucket),
                        "mean_probability": float(
                            np.mean([row[model] for row in bucket])
                        ),
                        "actual_up_rate": float(
                            np.mean([row["target_up"] for row in bucket])
                        ),
                    }
                )
    return output


def executable_economics(
    rows: list[dict[str, Any]], protocol: dict[str, Any]
) -> dict[str, Any]:
    economics = protocol["economics"]
    quantity = float(economics["quantities"][0])
    minimum_edge = float(economics["minimum_net_edge_per_share"])
    buffer = float(economics["uncertainty_buffer_per_share"])
    output: dict[str, Any] = {}
    for model in ("p_b", "p_c"):
        candidates = []
        for row in rows:
            choices = []
            for side in ("up", "down"):
                fair = row[model] if side == "up" else 1.0 - row[model]
                execution = row["vwap"].get(f"{side}_{quantity:g}") or {}
                ask = execution.get("buy_vwap")
                exact_fee = execution.get("buy_fee_per_share")
                fill = float(execution.get("buy_fill") or 0.0)
                if (
                    not execution.get("order_size_eligible")
                    or ask is None
                    or exact_fee is None
                    or fill + 1e-12 < quantity
                ):
                    continue
                fee = float(exact_fee)
                edge = fair - float(ask) - fee - buffer
                choices.append((edge, side, fair, float(ask), fee))
            if not choices:
                continue
            edge, side, fair, ask, fee = max(choices)
            if edge < minimum_edge:
                continue
            won = bool(row["target_up"]) if side == "up" else not bool(row["target_up"])
            pnl = (1.0 if won else 0.0) - ask - fee
            candidates.append(
                {
                    "slug": row["slug"],
                    "checkpoint": row["checkpoint"],
                    "observed_ts_ms": row["observed_ts_ms"],
                    "side": side.upper(),
                    "fair": fair,
                    "ask_vwap": ask,
                    "fee": fee,
                    "predicted_edge": edge,
                    "won": won,
                    "net_per_share": pnl,
                }
            )
        candidates.sort(key=lambda row: row["observed_ts_ms"])
        values = [row["net_per_share"] for row in candidates]
        up_values = [
            row["net_per_share"] for row in candidates if row["side"] == "UP"
        ]
        down_values = [
            row["net_per_share"] for row in candidates if row["side"] == "DOWN"
        ]
        split = int(len(candidates) * 0.8)
        output[model] = {
            "n": len(candidates),
            "win_rate": float(np.mean([row["won"] for row in candidates]))
            if candidates
            else None,
            "expectancy_per_share": float(np.mean(values)) if values else None,
            "expectancy_lower_95": lower_95_by_round(candidates),
            "up_expectancy": float(np.mean(up_values)) if up_values else None,
            "down_expectancy": float(np.mean(down_values)) if down_values else None,
            "final_untouched_20pct_expectancy": (
                float(np.mean([row["net_per_share"] for row in candidates[split:]]))
                if len(candidates) > split
                else None
            ),
            "candidates": candidates,
        }
    return output


def build_path_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        current_side = "UP" if row["distance_bps"] >= 0.0 else "DOWN"
        settled = "UP" if row["target_up"] else "DOWN"
        output.append(
            {
                "slug": row["slug"],
                "checkpoint_seconds_left": row["checkpoint"],
                "observed_ts_ms": row["observed_ts_ms"],
                "distance_bps": row["distance_bps"],
                "fraction_above": row["fraction_above"],
                "fraction_below": row["fraction_below"],
                "crossings_so_far": row["crossing_count"],
                "current_side": current_side,
                "settled_side": settled,
                "current_side_held": int(current_side == settled),
                "crossed_anchor_before_settlement": int(row["future_crossings"] >= 1),
                "recrossed_after_first_cross": int(row["future_crossings"] >= 2),
                "remained_on_current_side": int(row["future_crossings"] == 0),
                "future_crossing_count": row["future_crossings"],
                "terminal_distance_from_open_bps": (
                    row["finalized_close"] / row["finalized_open"] - 1.0
                )
                * 10_000.0,
                "terminal_move_bps_from_snapshot": (
                    row["finalized_close"] / row["binance_price"] - 1.0
                )
                * 10_000.0,
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_report(db_path: Path, output_dir: Path) -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    db = duckdb.connect(str(db_path), read_only=True)
    try:
        counts = {
            table: int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "hourly_markets",
                "hourly_snapshots",
                "hourly_resolutions",
                "health_events",
            )
        }
        resolution = db.execute(
            """
            SELECT count(*) FILTER (WHERE finalized_kline),
                   count(*) FILTER (WHERE polymarket_side IS NOT NULL),
                   count(*) FILTER (WHERE sides_match),
                   count(*) FILTER (WHERE sides_match=false),
                   min(candle_open_ts_ms), max(candle_close_ts_ms)
            FROM hourly_resolutions
            """
        ).fetchone()
        rows = checkpoint_rows(
            db, [int(value) for value in protocol["reporting"]["checkpoints_seconds_left"]]
        )
    finally:
        db.close()
    metrics = probability_metrics(rows) if rows else {}
    calibration = calibration_buckets(
        rows, protocol["reporting"]["price_buckets"]
    )
    economics = executable_economics(rows, protocol)
    path_targets = build_path_targets(rows)
    settled_rounds = int(resolution[0] or 0)
    start_ms = resolution[4]
    end_ms = resolution[5]
    weeks = (
        max(0.0, (float(end_ms) - float(start_ms)) / (7 * 86400 * 1000))
        if start_ms is not None and end_ms is not None
        else 0.0
    )
    best_key = None
    best_brier = math.inf
    market_brier = math.inf
    if rows:
        targets = np.asarray([row["target_up"] for row in rows], dtype=float)
        market_brier = float(
            np.mean(
                (
                    np.asarray([row["p_a"] for row in rows], dtype=float)
                    - targets
                )
                ** 2
            )
        )
        for key in ("p_b", "p_c"):
            value = float(
                np.mean(
                    (
                        np.asarray([row[key] for row in rows], dtype=float)
                        - targets
                    )
                    ** 2
                )
            )
            if value < best_brier:
                best_brier = value
                best_key = key
    best_economics = economics.get(best_key or "", {})
    gate = protocol["promotion_gate"]
    checkpoint_counts = [
        checkpoint["p_a"]["n"] for checkpoint in metrics.values()
    ]
    checks = {
        "minimum_settled_rounds": settled_rounds >= int(gate["minimum_settled_rounds"]),
        "minimum_continuous_weeks": weeks >= float(gate["minimum_continuous_weeks"]),
        "minimum_rounds_per_checkpoint": (
            len(checkpoint_counts)
            == len(protocol["reporting"]["checkpoints_seconds_left"])
            and min(checkpoint_counts, default=0)
            >= int(gate["minimum_rounds_per_checkpoint"])
        ),
        "probability_improves_over_market": best_brier < market_brier,
        "executable_ev_lower_95_positive": (
            best_economics.get("expectancy_lower_95") is not None
            and best_economics["expectancy_lower_95"] > 0.0
        ),
        "up_and_down_ev_positive": (
            best_economics.get("up_expectancy") is not None
            and best_economics.get("down_expectancy") is not None
            and best_economics["up_expectancy"] > 0.0
            and best_economics["down_expectancy"] > 0.0
        ),
        "final_untouched_positive": (
            best_economics.get("final_untouched_20pct_expectancy") is not None
            and best_economics["final_untouched_20pct_expectancy"] > 0.0
        ),
        "zero_resolution_mismatches": int(resolution[3] or 0) == 0,
        "polymarket_resolution_coverage": (
            settled_rounds > 0 and int(resolution[1] or 0) == settled_rounds
        ),
    }
    report = {
        "campaign": protocol["protocol_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "RESEARCH_ONLY",
        "counts": counts,
        "resolution": {
            "finalized_binance_rounds": settled_rounds,
            "polymarket_resolved_rounds": int(resolution[1] or 0),
            "matches": int(resolution[2] or 0),
            "mismatches": int(resolution[3] or 0),
            "continuous_weeks": weeks,
        },
        "probability_metrics_by_checkpoint": metrics,
        "executable_economics": {
            key: {name: value for name, value in detail.items() if name != "candidates"}
            for key, detail in economics.items()
        },
        "best_probability_model": best_key,
        "global_brier": {
            "market": market_brier if math.isfinite(market_brier) else None,
            "best_model": best_brier if math.isfinite(best_brier) else None,
        },
        "promotion_checks": checks,
        "promotion_ready": bool(checks) and all(checks.values()),
        "deferred": protocol["deferred_campaigns"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    write_csv(output_dir / "calibration_buckets.csv", calibration)
    write_csv(output_dir / "path_targets.csv", path_targets)
    candidates = []
    for model, detail in economics.items():
        for row in detail["candidates"]:
            candidates.append({"model": model, **row})
    write_csv(output_dir / "executable_candidates.csv", candidates)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit(f"evidence database does not exist: {args.db}")
    report = build_report(args.db, args.output)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
