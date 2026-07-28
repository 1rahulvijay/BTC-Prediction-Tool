#!/usr/bin/env python
"""Resolve and report the forward repricing shadow without changing its policies."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from polymarket_repricing_shadow_v1.routing import (
    parse_ladder,
    taker_fee_per_share,
    walk_asks,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = (
    ROOT / "data" / "research" / "polymarket_repricing_shadow_v1" / "shadow.duckdb"
)
DEFAULT_SETTLEMENTS = ROOT / "data" / "pm_export_settlements.parquet"
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
RANDOM_SEED = 20260728


def safe_auc(target: pd.Series, probability: pd.Series) -> float:
    return (
        float(roc_auc_score(target, probability)) if target.nunique() == 2 else math.nan
    )


def day_block_lower(frame: pd.DataFrame, column: str) -> float:
    daily = frame.groupby("day")[column].mean().to_numpy(float)
    if len(daily) < 2:
        return math.nan
    generator = np.random.default_rng(RANDOM_SEED)
    sampled = np.empty(2_000, dtype=float)
    for index in range(len(sampled)):
        sampled[index] = generator.choice(daily, len(daily), replace=True).mean()
    return float(np.quantile(sampled, 0.025))


def profit_factor(values: pd.Series) -> float:
    gains = float(values.clip(lower=0.0).sum())
    losses = float(-values.clip(upper=0.0).sum())
    if losses <= 0.0:
        return math.inf if gains > 0.0 else math.nan
    return gains / losses


def import_settlements(connection: Any, path: Path) -> int:
    if not path.is_file():
        return 0
    settlements = pd.read_parquet(path)
    if settlements.empty:
        return 0
    candidates = connection.execute(
        "SELECT candidate_id, market_id FROM repricing_candidates"
    ).fetchdf()
    joined = candidates.merge(
        settlements[["slug", "settled_side", "resolution_source", "resolved_at"]],
        left_on="market_id",
        right_on="slug",
        how="inner",
    )
    rows = []
    for row in joined.itertuples():
        raw_side = str(row.settled_side).strip().upper()
        if raw_side in {"1", "1.0", "TRUE", "UP", "YES"}:
            side = "UP"
        elif raw_side in {"0", "0.0", "FALSE", "DOWN", "NO"}:
            side = "DOWN"
        else:
            continue
        rows.append(
            (
                row.candidate_id,
                side,
                str(row.resolution_source),
                float(row.resolved_at),
            )
        )
    if rows:
        connection.executemany(
            "INSERT OR REPLACE INTO repricing_settlements VALUES (?,?,?,?)", rows
        )
    return len(rows)


def probability_report(
    candidates: pd.DataFrame, observations: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty or observations.empty:
        return pd.DataFrame(), pd.DataFrame()
    at_five = observations[observations["offset_seconds"] == 5][
        ["candidate_id", "ask"]
    ].rename(columns={"ask": "ask_5s"})
    data = candidates.merge(at_five, on="candidate_id", how="inner")
    if data.empty:
        return pd.DataFrame(), pd.DataFrame()
    data["ask_change_5s"] = data["ask_5s"] - data["current_ask"]
    data["worsened_1c"] = (data["ask_change_5s"] >= 0.01 - 1e-9).astype(int)
    data["selected_baseline_worsening_probability"] = np.where(
        data["selected_side"] == "UP",
        data["up_baseline_worsening_probability"],
        data["down_baseline_worsening_probability"],
    )
    rows: list[dict[str, Any]] = []
    decile_rows: list[pd.DataFrame] = []
    for side, subset in data.groupby("selected_side"):
        subset = subset.dropna(
            subset=[
                "selected_baseline_worsening_probability",
                "selected_worsening_probability",
            ]
        )
        if subset.empty:
            continue
        target = subset["worsened_1c"]
        baseline = subset["selected_baseline_worsening_probability"].clip(
            1e-6, 1 - 1e-6
        )
        evidence = subset["selected_worsening_probability"].clip(1e-6, 1 - 1e-6)
        baseline_brier = float(brier_score_loss(target, baseline))
        evidence_brier = float(brier_score_loss(target, evidence))
        baseline_log_loss = float(log_loss(target, baseline, labels=[0, 1]))
        evidence_log_loss = float(log_loss(target, evidence, labels=[0, 1]))
        monotonicity = math.nan
        if len(subset) >= 10:
            deciles = subset.copy()
            deciles["probability_decile"] = pd.qcut(
                deciles["selected_worsening_probability"],
                10,
                labels=False,
                duplicates="drop",
            )
            grouped = (
                deciles.groupby("probability_decile", observed=True)
                .agg(
                    rows=("candidate_id", "size"),
                    mean_probability=("selected_worsening_probability", "mean"),
                    observed_worsening_rate=("worsened_1c", "mean"),
                    mean_ask_change=("ask_change_5s", "mean"),
                )
                .reset_index()
            )
            grouped.insert(0, "selected_side", side)
            decile_rows.append(grouped)
            if len(grouped) >= 2:
                monotonicity = float(
                    grouped["mean_probability"]
                    .rank()
                    .corr(grouped["observed_worsening_rate"].rank())
                )
        rows.append(
            {
                "side": side,
                "rows": len(subset),
                "baseline_auc": safe_auc(target, baseline),
                "evidence_auc": safe_auc(target, evidence),
                "auc_delta": safe_auc(target, evidence) - safe_auc(target, baseline),
                "baseline_brier": baseline_brier,
                "evidence_brier": evidence_brier,
                "brier_delta": evidence_brier - baseline_brier,
                "baseline_log_loss": baseline_log_loss,
                "evidence_log_loss": evidence_log_loss,
                "log_loss_delta": evidence_log_loss - baseline_log_loss,
                "mean_baseline_probability": float(baseline.mean()),
                "mean_evidence_probability": float(evidence.mean()),
                "observed_worsening_rate": float(target.mean()),
                "mean_ask_change": float(subset["ask_change_5s"].mean()),
                "decile_monotonicity": monotonicity,
            }
        )
    return (
        pd.DataFrame(rows),
        pd.concat(decile_rows, ignore_index=True) if decile_rows else pd.DataFrame(),
    )


def size_stress_report(candidates: pd.DataFrame, sizes: list[float]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for size in sizes:
        executions = [
            walk_asks(parse_ladder(ladder), size)
            for ladder in candidates.get("ladder_json", pd.Series(dtype=str))
        ]
        complete = [bool(value["complete"]) for value in executions]
        vwaps = [
            float(value["vwap"]) for value in executions if bool(value["complete"])
        ]
        rows.append(
            {
                "quantity": size,
                "candidates": len(executions),
                "complete_rate": float(np.mean(complete)) if complete else math.nan,
                "average_vwap": float(np.mean(vwaps)) if vwaps else math.nan,
                "maximum_vwap": float(np.max(vwaps)) if vwaps else math.nan,
            }
        )
    return pd.DataFrame(rows)


def delay_stress_report(
    candidates: pd.DataFrame,
    observations: pd.DataFrame,
    settlements: pd.DataFrame,
    offsets: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty or observations.empty:
        return pd.DataFrame(), pd.DataFrame()
    resolved = candidates.merge(settlements, on="candidate_id", how="inner")
    rows: list[dict[str, Any]] = []
    for offset in offsets:
        delayed = observations[observations["offset_seconds"] == offset]
        joined = resolved.merge(
            delayed[
                [
                    "candidate_id",
                    "actual_elapsed_seconds",
                    "ask",
                    "ladder_json",
                ]
            ],
            on="candidate_id",
            how="inner",
            suffixes=("_decision", "_delayed"),
        )
        for row in joined.itertuples():
            immediate = walk_asks(
                parse_ladder(row.ladder_json_decision), float(row.quantity)
            )
            delayed_execution = walk_asks(
                parse_ladder(row.ladder_json_delayed), float(row.quantity)
            )
            won = row.selected_side == row.settled_side

            def net_pnl(
                execution: dict[str, float | bool],
                quantity: float,
                won_trade: bool,
            ) -> float:
                if not execution["complete"]:
                    return math.nan
                vwap = float(execution["vwap"])
                fee = quantity * taker_fee_per_share(vwap)
                return float(won_trade) * quantity - float(execution["notional"]) - fee

            immediate_pnl = net_pnl(immediate, float(row.quantity), won)
            delayed_pnl = net_pnl(delayed_execution, float(row.quantity), won)
            rows.append(
                {
                    "candidate_id": row.candidate_id,
                    "offset_seconds": offset,
                    "actual_elapsed_seconds": float(row.actual_elapsed_seconds),
                    "observation_lag_seconds": float(row.actual_elapsed_seconds)
                    - offset,
                    "selected_side": row.selected_side,
                    "immediate_complete": bool(immediate["complete"]),
                    "delayed_complete": bool(delayed_execution["complete"]),
                    "immediate_vwap": float(immediate["vwap"]),
                    "delayed_vwap": float(delayed_execution["vwap"]),
                    "vwap_change": float(delayed_execution["vwap"])
                    - float(immediate["vwap"]),
                    "immediate_pnl": immediate_pnl,
                    "delayed_pnl": delayed_pnl,
                    "pnl_delta_delayed_vs_immediate": delayed_pnl - immediate_pnl,
                }
            )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return pd.DataFrame(), detail
    summary = (
        detail.groupby("offset_seconds")
        .agg(
            candidates=("candidate_id", "nunique"),
            complete_rate=("delayed_complete", "mean"),
            average_vwap_change=("vwap_change", "mean"),
            average_pnl_delta=("pnl_delta_delayed_vs_immediate", "mean"),
            median_observation_lag_seconds=("observation_lag_seconds", "median"),
            p90_observation_lag_seconds=(
                "observation_lag_seconds",
                lambda values: float(np.quantile(values, 0.90)),
            ),
        )
        .reset_index()
    )
    return summary, detail


def route_report(
    candidates: pd.DataFrame,
    routes: pd.DataFrame,
    settlements: pd.DataFrame,
    observations: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if candidates.empty or routes.empty or settlements.empty:
        return pd.DataFrame(), pd.DataFrame()
    data = routes.merge(candidates, on="candidate_id", how="inner").merge(
        settlements, on="candidate_id", how="inner"
    )
    at_five = observations[observations["offset_seconds"] == 5][
        ["candidate_id", "ask"]
    ].rename(columns={"ask": "ask_5s"})
    data = data.merge(at_five, on="candidate_id", how="left")
    data["adverse_repricing"] = np.where(
        data["ask_5s"].notna(),
        data["ask_5s"] >= data["current_ask"] + 0.01 - 1e-9,
        np.nan,
    )
    data["day"] = pd.to_datetime(data["decision_ts"], unit="s", utc=True).dt.strftime(
        "%Y-%m-%d"
    )
    data["week"] = pd.to_datetime(data["decision_ts"], unit="s", utc=True).dt.strftime(
        "%G-W%V"
    )
    data["won"] = data["selected_side"] == data["settled_side"]
    data["entry_cost"] = data["average_price"].fillna(0.0) * data[
        "filled_quantity"
    ] + data["fee"].fillna(0.0)
    data["net_pnl"] = (
        data["won"].astype(float) * data["filled_quantity"] - data["entry_cost"]
    )
    baseline = data[data["policy"] == "A_BASELINE_TAKER"][
        ["candidate_id", "net_pnl", "entry_cost"]
    ].rename(columns={"net_pnl": "baseline_pnl", "entry_cost": "baseline_entry_cost"})
    data = data.merge(baseline, on="candidate_id", how="left")
    data["pnl_delta_vs_baseline"] = data["net_pnl"] - data["baseline_pnl"]
    data["missed_quantity"] = (
        data["requested_quantity"] - data["filled_quantity"]
    ).clip(lower=0.0)
    data["missed_fill_penalty"] = np.where(
        data["won"],
        data["missed_quantity"]
        * (
            1.0
            - data["current_ask"]
            - 0.07 * data["current_ask"] * (1.0 - data["current_ask"])
        ),
        0.0,
    )
    data["execution_improvement"] = (
        data["baseline_entry_cost"] - data["entry_cost"] - data["missed_fill_penalty"]
    )
    rows = []

    def summarize(policy: str, segment: str, subset: pd.DataFrame) -> dict[str, Any]:
        weekly = subset.groupby("week")["pnl_delta_vs_baseline"].sum()
        weekly_positive = weekly.clip(lower=0.0)
        positive_total = weekly_positive.sum()
        largest_share = (
            float(weekly_positive.max() / positive_total)
            if positive_total > 0 and len(weekly_positive)
            else math.nan
        )
        adverse = subset[subset["adverse_repricing"] == 1.0]
        favorable_wins = subset[(subset["adverse_repricing"] == 0.0) & subset["won"]]
        policy_pf = profit_factor(subset["net_pnl"])
        baseline_pf = profit_factor(subset["baseline_pnl"])
        return {
            "policy": policy,
            "segment": segment,
            "original_candidates": subset["candidate_id"].nunique(),
            "full_fill_rate": float(
                (
                    subset["filled_quantity"] >= subset["requested_quantity"] - 1e-9
                ).mean()
            ),
            "partial_fill_rate": float(
                (
                    (subset["filled_quantity"] > 0)
                    & (subset["filled_quantity"] < subset["requested_quantity"] - 1e-9)
                ).mean()
            ),
            "missed_fill_rate": float((subset["filled_quantity"] <= 0).mean()),
            "average_fill_latency": float(subset["fill_time_seconds"].mean()),
            "average_execution_improvement": float(
                subset["execution_improvement"].mean()
            ),
            "average_execution_improvement_cents": float(
                subset["execution_improvement"].mean() * 100.0
            ),
            "median_execution_improvement": float(
                subset["execution_improvement"].median()
            ),
            "median_execution_improvement_cents": float(
                subset["execution_improvement"].median() * 100.0
            ),
            "adverse_repricing_capture_rate": float(
                (
                    (adverse["filled_quantity"] > 0)
                    & (adverse["average_price"] <= adverse["current_ask"] + 1e-9)
                ).mean()
            )
            if len(adverse)
            else math.nan,
            "favorable_winning_opportunity_missed_rate": float(
                (
                    favorable_wins["filled_quantity"]
                    < favorable_wins["requested_quantity"] - 1e-9
                ).mean()
            )
            if len(favorable_wins)
            else math.nan,
            "net_pnl": float(subset["net_pnl"].sum()),
            "pnl_delta_vs_baseline": float(subset["pnl_delta_vs_baseline"].sum()),
            "profit_factor": policy_pf,
            "baseline_profit_factor": baseline_pf,
            "profit_factor_delta": (
                policy_pf - baseline_pf
                if math.isfinite(policy_pf) and math.isfinite(baseline_pf)
                else math.nan
            ),
            "day_block_improvement_lower_95": day_block_lower(
                subset, "execution_improvement"
            ),
            "positive_week_fraction": float((weekly > 0).mean()),
            "largest_week_improvement_share": largest_share,
            "final_untouched_rows": 0,
            "final_untouched_average_improvement": math.nan,
        }

    for policy, subset in data.groupby("policy"):
        rows.append(summarize(policy, "ALL", subset))
        for side, side_subset in subset.groupby("selected_side"):
            rows.append(summarize(policy, str(side), side_subset))
    return pd.DataFrame(rows), data


def gate_report(
    protocol: dict[str, Any],
    candidates: pd.DataFrame,
    policy_metrics: pd.DataFrame,
    probability_metrics: pd.DataFrame,
    delay_metrics: pd.DataFrame,
    size_metrics: pd.DataFrame,
    route_detail: pd.DataFrame,
) -> dict[str, Any]:
    gate = protocol["forward_gate"]
    if candidates.empty:
        return {
            "promotion_status": "research_only",
            "production_promoted": False,
            "paper_promoted": False,
            "reason": "no forward candidates recorded",
            "policies": {},
        }
    decision_time = pd.to_datetime(candidates["decision_ts"], unit="s", utc=True)
    weeks = decision_time.dt.strftime("%G-W%V")
    up_rows = int((candidates["selected_side"] == "UP").sum())
    down_rows = int((candidates["selected_side"] == "DOWN").sum())
    calibration = (
        probability_metrics.set_index("side").to_dict("index")
        if "side" in probability_metrics
        else {}
    )
    up_calibration = calibration.get("UP", {})
    down_calibration = calibration.get("DOWN", {})
    required_delays = {int(value) for value in gate["required_delay_stress_seconds"]}
    available_delays = (
        set(delay_metrics["offset_seconds"].astype(int))
        if not delay_metrics.empty
        else set()
    )
    timing_quality = (
        bool(
            (
                delay_metrics["median_observation_lag_seconds"]
                <= gate["maximum_median_observation_lag_seconds"]
            ).all()
        )
        if not delay_metrics.empty
        else False
    )
    maximum_size = max(float(value) for value in protocol["routing"]["position_sizes"])
    maximum_size_rows = size_metrics[
        np.isclose(size_metrics.get("quantity", math.nan), maximum_size)
    ]
    size_complete_rate = (
        float(maximum_size_rows.iloc[0]["complete_rate"])
        if not maximum_size_rows.empty
        else math.nan
    )
    up_incremental = (
        bool(up_calibration)
        and up_calibration["brier_delta"] < 0
        and up_calibration["log_loss_delta"] < 0
        and up_calibration["decile_monotonicity"] >= gate["minimum_decile_monotonicity"]
    )
    down_incremental = (
        bool(down_calibration)
        and down_calibration["brier_delta"] < 0
        and down_calibration["log_loss_delta"] < 0
        and down_calibration["decile_monotonicity"]
        >= gate["minimum_decile_monotonicity"]
    )
    common = {
        "minimum_decisions": len(candidates) >= gate["minimum_independent_decisions"],
        "minimum_up": up_rows >= gate["minimum_up_decisions"],
        "minimum_down": down_rows >= gate["minimum_down_decisions"],
        "minimum_weeks": weeks.nunique() >= gate["minimum_continuous_weeks"],
        "both_probability_sides_present": set(
            probability_metrics.get("side", pd.Series(dtype=str))
        )
        == {"UP", "DOWN"},
        "delay_stress_available": required_delays.issubset(available_delays),
        "delay_observation_timing_quality": timing_quality,
        "maximum_position_size_fillable": math.isfinite(size_complete_rate)
        and size_complete_rate >= gate["minimum_size_stress_complete_rate"],
    }
    untouched_start = pd.Timestamp(gate["final_untouched_start_utc"])
    policies = {}
    for row in policy_metrics.to_dict("records"):
        segment = row["segment"]
        policy_detail = route_detail[route_detail["policy"] == row["policy"]]
        if segment in {"UP", "DOWN"}:
            policy_detail = policy_detail[policy_detail["selected_side"] == segment]
        untouched = policy_detail[
            pd.to_datetime(policy_detail["decision_ts"], unit="s", utc=True)
            >= untouched_start
        ]
        untouched_positive = (
            len(untouched) > 0
            and float(untouched["execution_improvement"].mean()) > 0.0
        )
        expected_denominator = (
            len(candidates)
            if segment == "ALL"
            else int((candidates["selected_side"] == segment).sum())
        )
        segment_calibration = (
            up_incremental and down_incremental
            if segment == "ALL"
            else up_incremental
            if segment == "UP"
            else down_incremental
        )
        checks = {
            **common,
            "same_candidate_denominator": row["original_candidates"]
            == expected_denominator,
            "segment_incremental_calibration": segment_calibration,
            "policy_within_frozen_promotion_scope": row["policy"]
            in protocol["routing"]["paper_promotion_candidates"],
            "positive_mean_execution_improvement": row["average_execution_improvement"]
            > 0,
            "positive_day_block_lower": row["day_block_improvement_lower_95"]
            > gate["minimum_day_block_execution_improvement_lower_95"],
            "positive_weeks": row["positive_week_fraction"]
            >= gate["minimum_positive_week_fraction"],
            "week_concentration": row["largest_week_improvement_share"]
            <= gate["maximum_single_week_improvement_share"],
            "final_untouched_period_positive": untouched_positive,
        }
        policies[f"{row['policy']}:{segment}"] = {
            "checks": checks,
            "final_untouched_start_utc": gate["final_untouched_start_utc"],
            "final_untouched_rows": len(untouched),
            "paper_routing_eligible": all(checks.values()),
        }
    return {
        "promotion_status": "research_only",
        "production_promoted": False,
        "paper_promoted": False,
        "policies": policies,
    }


def run(db: Path, settlements_path: Path, output: Path) -> None:
    if not db.is_file():
        raise FileNotFoundError(f"shadow database does not exist: {db}")
    connection = duckdb.connect(str(db))
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    required_tables = {
        "repricing_candidates",
        "repricing_routes",
        "repricing_observations",
        "repricing_settlements",
    }
    missing = required_tables - tables
    if missing:
        connection.close()
        raise RuntimeError(f"shadow database missing tables: {sorted(missing)}")
    imported = import_settlements(connection, settlements_path)
    candidates = connection.execute("SELECT * FROM repricing_candidates").fetchdf()
    routes = connection.execute("SELECT * FROM repricing_routes").fetchdf()
    observations = connection.execute("SELECT * FROM repricing_observations").fetchdf()
    settlements = connection.execute("SELECT * FROM repricing_settlements").fetchdf()
    connection.close()
    output.mkdir(parents=True, exist_ok=True)
    probability, deciles = probability_report(candidates, observations)
    policies, detail = route_report(candidates, routes, settlements, observations)
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    delay, delay_detail = delay_stress_report(
        candidates,
        observations,
        settlements,
        [
            int(value)
            for value in protocol["forward_gate"]["required_delay_stress_seconds"]
        ],
    )
    size = size_stress_report(
        candidates,
        [float(value) for value in protocol["routing"]["position_sizes"]],
    )
    gates = gate_report(
        protocol,
        candidates,
        policies,
        probability,
        delay,
        size,
        detail,
    )
    probability.to_csv(output / "repricing_calibration.csv", index=False)
    deciles.to_csv(output / "repricing_probability_deciles.csv", index=False)
    policies.to_csv(output / "routing_policy_metrics.csv", index=False)
    delay.to_csv(output / "delay_stress.csv", index=False)
    delay_detail.to_parquet(output / "delay_stress_detail.parquet", index=False)
    size.to_csv(output / "size_depth_stress.csv", index=False)
    detail.to_parquet(output / "resolved_route_detail.parquet", index=False)
    (output / "gate_status.json").write_text(
        json.dumps(gates, indent=2, default=str), encoding="utf-8"
    )
    print(
        f"[report] candidates={len(candidates)} resolved={settlements['candidate_id'].nunique()} "
        f"settlements_imported={imported} output={output}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--settlements", type=Path, default=DEFAULT_SETTLEMENTS)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DB.parent / "latest_report",
    )
    args = parser.parse_args()
    run(args.db.resolve(), args.settlements.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
