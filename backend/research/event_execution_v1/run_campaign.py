#!/usr/bin/env python
"""Run the frozen event execution and anchor-crossing campaign.

The event-time heads are tested only as timing, veto, crossing, and short-horizon
execution evidence. They are never allowed to replace the settlement-side model.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (BACKEND, RESEARCH):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from event_evidence_accumulator.run_accumulator_campaign import (
    align_prediction_heads,
    dates_for_price_window,
    load_spot_seconds,
)
from polymarket_fee import polymarket_taker_fee_per_share

from event_execution_v1.model_bundle import CalibratedBinary

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "research" / "event_execution_v1"
RANDOM_SEED = 20260728
_LOG_PATH: Path | None = None


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_PATH is not None:
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def safe_auc(target: np.ndarray, probability: np.ndarray) -> float:
    y = np.asarray(target, dtype=int)
    return float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else math.nan


def probability_metrics(
    target: np.ndarray, probability: np.ndarray
) -> dict[str, float]:
    y = np.asarray(target, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return {
        "auc": safe_auc(y, p),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "accuracy": float(accuracy_score(y, p >= 0.5)),
    }


def logit(values: pd.Series | np.ndarray) -> np.ndarray:
    probability = np.clip(np.asarray(values, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(probability / (1.0 - probability))


def resolve_input(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def load_protocol() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if protocol["maximum_experiments"] != 10 or len(protocol["experiments"]) != 10:
        raise ValueError("frozen protocol must contain exactly ten experiments")
    return protocol


def load_kacho(
    archive: Path,
    minimum_ts: int,
    maximum_ts: int,
) -> pd.DataFrame:
    with zipfile.ZipFile(archive) as handle:
        markets = pq.read_table(
            io.BytesIO(handle.read("btc_markets.parquet")),
            columns=["condition_id", "market_start", "market_end", "outcome"],
        ).to_pandas()
        market_start_s = markets["market_start"].astype("int64") // 1_000_000_000
        market_end_s = markets["market_end"].astype("int64") // 1_000_000_000
        markets["market_start_s"] = market_start_s.astype(np.int64)
        markets["market_end_s"] = market_end_s.astype(np.int64)
        markets = markets[
            (markets["market_end_s"] >= minimum_ts)
            & (markets["market_start_s"] <= maximum_ts)
            & markets["outcome"].isin(["Up", "Down"])
        ].copy()
        ids = set(markets["condition_id"])
        ticks = pq.read_table(
            io.BytesIO(handle.read("btc_ticks.parquet")),
            columns=[
                "condition_id",
                "t",
                "bu",
                "au",
                "bd",
                "ad",
                "sau",
                "sad",
                "du",
                "dd",
            ],
        ).to_pandas()
    ticks = ticks[
        ticks["condition_id"].isin(ids) & ticks["t"].between(minimum_ts, maximum_ts)
    ].copy()
    ticks = ticks.merge(
        markets[
            [
                "condition_id",
                "market_start_s",
                "market_end_s",
                "outcome",
            ]
        ],
        on="condition_id",
        how="inner",
        validate="many_to_one",
    )
    ticks = ticks.sort_values(["condition_id", "t"]).drop_duplicates(
        ["condition_id", "t"], keep="last"
    )
    for column in ("bu", "au", "bd", "ad"):
        ticks[column] = pd.to_numeric(ticks[column], errors="coerce")
        ticks.loc[~ticks[column].between(0.001, 0.999), column] = np.nan
    ticks["up_mid"] = (ticks["bu"] + ticks["au"]) / 2.0
    ticks["down_mid"] = (ticks["bd"] + ticks["ad"]) / 2.0
    midpoint_sum = ticks["up_mid"] + ticks["down_mid"]
    ticks["market_prob_up"] = ticks["up_mid"] / midpoint_sum
    ticks["spread_up"] = ticks["au"] - ticks["bu"]
    ticks["spread_down"] = ticks["ad"] - ticks["bd"]
    ticks["spread_sum"] = ticks["spread_up"] + ticks["spread_down"]
    ticks["ask_sum_minus_one"] = ticks["au"] + ticks["ad"] - 1.0
    ticks["seconds_left"] = ticks["market_end_s"] - ticks["t"]
    ticks["seconds_ratio"] = ticks["seconds_left"] / 300.0
    ticks["settlement_up"] = (ticks["outcome"] == "Up").astype(int)
    ticks["day"] = pd.to_datetime(
        ticks["market_start_s"], unit="s", utc=True
    ).dt.strftime("%Y-%m-%d")
    log(
        f"[book] markets={ticks['condition_id'].nunique():,} rows={len(ticks):,} "
        f"range={pd.to_datetime(ticks['t'].min(), unit='s', utc=True)}.."
        f"{pd.to_datetime(ticks['t'].max(), unit='s', utc=True)}"
    )
    return ticks.reset_index(drop=True)


def attach_event_and_spot(
    ticks: pd.DataFrame,
    prediction_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    aligned = align_prediction_heads(prediction_path, [5, 15])
    minimum_ts = int(min(ticks["t"].min(), aligned["timestamp_s"].min()))
    maximum_ts = int(max(ticks["t"].max(), aligned["timestamp_s"].max()))
    prices = load_spot_seconds(dates_for_price_window(minimum_ts, maximum_ts))
    prices = prices[prices["timestamp_s"].between(minimum_ts - 60, maximum_ts + 60)]

    event = aligned.copy()
    event["event_score"] = (
        0.70 * logit(event["p_up_5"]) * event["p_move_5"]
        + 0.30 * logit(event["p_up_15"]) * event["p_move_15"]
    )
    data = pd.merge_asof(
        ticks.sort_values("t"),
        event.sort_values("timestamp_s"),
        left_on="t",
        right_on="timestamp_s",
        direction="backward",
        tolerance=5,
    )
    data = data.merge(
        prices,
        left_on="t",
        right_on="timestamp_s",
        how="left",
        suffixes=("", "_spot"),
        validate="many_to_one",
    )
    anchor_map = prices.set_index("timestamp_s")["spot_price"]
    data["anchor_price"] = anchor_map.reindex(data["market_start_s"]).to_numpy()
    data["distance_bps"] = (data["spot_price"] / data["anchor_price"] - 1.0) * 10_000.0
    remaining_std = data["rv_60s_bps"].clip(lower=0.01) * np.sqrt(
        data["seconds_left"].clip(lower=1)
    )
    data["z_distance"] = (data["distance_bps"] / remaining_std.clip(lower=0.25)).clip(
        -12.0, 12.0
    )
    data["abs_distance_bps"] = data["distance_bps"].abs()
    data["abs_z_distance"] = data["z_distance"].abs()
    data["event_abs_score"] = data["event_score"].abs()
    data["event_persistent_score"] = data.groupby("condition_id", sort=False)[
        "event_score"
    ].transform(lambda values: values.ewm(halflife=5.0, adjust=False).mean())
    data["event_side_up"] = data["event_persistent_score"] >= 0.0
    data["current_side_up"] = data["distance_bps"] >= 0.0
    data["event_disagrees_current"] = (
        data["event_side_up"] != data["current_side_up"]
    ).astype(int)
    required = [
        "spot_price",
        "anchor_price",
        "rv_60s_bps",
        "p_up_5",
        "p_move_5",
        "p_roundtrip_5",
        "p_up_15",
        "p_move_15",
        "p_roundtrip_15",
        "event_score",
    ]
    before = len(data)
    data = data[np.isfinite(data[required].to_numpy(float)).all(axis=1)].copy()
    log(f"[align] retained={len(data):,}/{before:,} quote rows with causal event+spot")
    return data.sort_values(["condition_id", "t"]).reset_index(drop=True), prices


def assign_development_roles(data: pd.DataFrame, fit_fraction: float) -> pd.DataFrame:
    markets = (
        data[["condition_id", "market_start_s"]]
        .drop_duplicates("condition_id")
        .sort_values(["market_start_s", "condition_id"])
        .reset_index(drop=True)
    )
    cut = max(1, min(len(markets) - 1, int(len(markets) * fit_fraction)))
    fit_ids = set(markets.iloc[:cut]["condition_id"])
    output = data.copy()
    output["role"] = np.where(
        output["condition_id"].isin(fit_ids), "fit", "calibration"
    )
    return output


def fit_calibrated(
    fit: pd.DataFrame,
    calibration: pd.DataFrame,
    features: list[str],
    target: str,
) -> CalibratedBinary:
    clean_fit = fit[np.isfinite(fit[target])].copy()
    clean_cal = calibration[np.isfinite(calibration[target])].copy()
    if clean_fit[target].nunique() < 2 or len(clean_fit) < 100:
        raise ValueError(f"insufficient classes/rows for {target}")
    medians = clean_fit[features].replace([np.inf, -np.inf], np.nan).median()
    x_fit = clean_fit[features].replace([np.inf, -np.inf], np.nan).fillna(medians)
    sample_counts = clean_fit.groupby("condition_id")["condition_id"].transform("size")
    sample_weight = 1.0 / sample_counts.clip(lower=1).to_numpy(float)
    pipeline = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=0.5,
                    max_iter=1_000,
                    class_weight="balanced",
                    random_state=RANDOM_SEED,
                ),
            ),
        ]
    )
    pipeline.fit(
        x_fit, clean_fit[target].astype(int), model__sample_weight=sample_weight
    )
    calibrator: IsotonicRegression | None = None
    if len(clean_cal) >= 100 and clean_cal[target].nunique() == 2:
        x_cal = clean_cal[features].replace([np.inf, -np.inf], np.nan).fillna(medians)
        raw = pipeline.predict_proba(x_cal)[:, 1]
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw, clean_cal[target].astype(int))
    return CalibratedBinary(features, medians, pipeline, calibrator)


def bootstrap_day_lower(values: pd.DataFrame, column: str) -> float:
    daily = values.groupby("day")[column].mean().to_numpy(float)
    if len(daily) < 2:
        return math.nan
    generator = np.random.default_rng(RANDOM_SEED)
    samples = np.empty(2_000, dtype=float)
    for index in range(len(samples)):
        samples[index] = generator.choice(daily, size=len(daily), replace=True).mean()
    return float(np.quantile(samples, 0.025))


def incremental_experiment(
    experiment_id: str,
    name: str,
    target: str,
    development: pd.DataFrame,
    locked: pd.DataFrame,
    baseline_features: list[str],
    evidence_features: list[str],
    gate: dict[str, Any],
    model_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame]:
    fit = development[development["role"] == "fit"]
    calibration = development[development["role"] == "calibration"]
    base = fit_calibrated(fit, calibration, baseline_features, target)
    evidence = fit_calibrated(
        fit, calibration, baseline_features + evidence_features, target
    )
    test = locked[np.isfinite(locked[target])].copy()
    base_probability = base.predict(test)
    evidence_probability = evidence.predict(test)
    base_metrics = probability_metrics(test[target], base_probability)
    evidence_metrics = probability_metrics(test[target], evidence_probability)
    test["baseline_probability"] = base_probability
    test["evidence_probability"] = evidence_probability
    test["brier_improvement"] = np.square(test[target] - base_probability) - np.square(
        test[target] - evidence_probability
    )
    day_lower = bootstrap_day_lower(test, "brier_improvement")
    auc_delta = evidence_metrics["auc"] - base_metrics["auc"]
    brier_delta = evidence_metrics["brier"] - base_metrics["brier"]
    checks = {
        "enough_rows": len(test) >= int(gate["minimum_locked_rows"]),
        "auc_delta": auc_delta >= float(gate["minimum_auc_delta"]),
        "brier_delta": brier_delta <= float(gate["maximum_brier_delta"]),
        "day_block_brier_improvement": day_lower
        > float(gate["minimum_day_block_brier_improvement_lower_95"]),
    }
    research_gate_passed = all(checks.values())
    joblib.dump(
        {"baseline": base, "evidence": evidence},
        model_dir / f"{experiment_id}_{name}.joblib",
    )
    result = {
        "experiment_id": experiment_id,
        "family": "incremental_classifier",
        "name": name,
        "target": target,
        "locked_rows": len(test),
        "locked_markets": int(test["condition_id"].nunique()),
        "locked_days": int(test["day"].nunique()),
        "locked_positive_rate": float(test[target].mean()),
        "baseline_auc": base_metrics["auc"],
        "evidence_auc": evidence_metrics["auc"],
        "auc_delta": auc_delta,
        "baseline_brier": base_metrics["brier"],
        "evidence_brier": evidence_metrics["brier"],
        "brier_delta": brier_delta,
        "baseline_log_loss": base_metrics["log_loss"],
        "evidence_log_loss": evidence_metrics["log_loss"],
        "day_block_brier_improvement_lower_95": day_lower,
        "research_gate_passed": research_gate_passed,
        "promoted": False,
        "promotion_blocker": "research gate only; forward shadow and live execution evidence required",
        "gate_checks": checks,
    }
    log(
        f"[{experiment_id}] {name} AUC {base_metrics['auc']:.4f}->"
        f"{evidence_metrics['auc']:.4f} d={auc_delta:+.4f}; Brier "
        f"{base_metrics['brier']:.4f}->{evidence_metrics['brier']:.4f}; "
        f"research_gate_passed={research_gate_passed}"
    )
    output = test[
        [
            "condition_id",
            "t",
            "day",
            target,
            "baseline_probability",
            "evidence_probability",
        ]
    ].copy()
    output["experiment_id"] = experiment_id
    return result, output


def attach_future_book_targets(data: pd.DataFrame) -> pd.DataFrame:
    future = data[["condition_id", "t", "au", "ad"]].copy()
    future["t"] = future["t"] - 5
    future = future.rename(columns={"au": "future_au_5s", "ad": "future_ad_5s"})
    output = data.merge(
        future,
        on=["condition_id", "t"],
        how="left",
        validate="one_to_one",
    )
    output["up_ask_worsens_5s"] = (
        output["future_au_5s"] >= output["au"] + 0.01 - 1e-9
    ).astype(float)
    output["down_ask_worsens_5s"] = (
        output["future_ad_5s"] >= output["ad"] + 0.01 - 1e-9
    ).astype(float)
    unavailable = output["future_au_5s"].isna() | output["future_ad_5s"].isna()
    output.loc[unavailable, ["up_ask_worsens_5s", "down_ask_worsens_5s"]] = np.nan
    return output


def attach_crossing_targets(data: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    output = data.copy()
    price_map = prices.set_index("timestamp_s")["spot_price"]
    current_up = output["spot_price"].to_numpy(float) >= output[
        "anchor_price"
    ].to_numpy(float)
    crossed_5 = np.zeros(len(output), dtype=bool)
    crossed_15 = np.zeros(len(output), dtype=bool)
    recrossed_15 = np.zeros(len(output), dtype=bool)
    crossed_so_far = np.zeros(len(output), dtype=bool)
    valid_all = np.ones(len(output), dtype=bool)
    timestamps = output["t"].to_numpy(np.int64)
    anchors = output["anchor_price"].to_numpy(float)
    for offset in range(1, 16):
        future_price = price_map.reindex(timestamps + offset).to_numpy(float)
        valid = np.isfinite(future_price)
        valid_all &= valid
        future_up = future_price >= anchors
        opposite = future_up != current_up
        if offset <= 5:
            crossed_5 |= opposite
        crossed_15 |= opposite
        recrossed_15 |= crossed_so_far & (future_up == current_up)
        crossed_so_far |= opposite
    output["anchor_cross_5s"] = crossed_5.astype(float)
    output["anchor_cross_15s"] = crossed_15.astype(float)
    output["cross_then_recross_15s"] = recrossed_15.astype(float)
    output.loc[
        ~valid_all,
        ["anchor_cross_5s", "anchor_cross_15s", "cross_then_recross_15s"],
    ] = np.nan
    return output


def fee_array(prices: pd.Series) -> np.ndarray:
    return np.asarray(
        [polymarket_taker_fee_per_share(value) for value in prices.to_numpy(float)],
        dtype=float,
    )


def execution_policy_metrics(
    name: str,
    frame: pd.DataFrame,
    accepted: np.ndarray,
    entry_ask: np.ndarray,
    baseline_pnl: np.ndarray,
    baseline_entry_ask: np.ndarray,
) -> tuple[dict[str, Any], pd.DataFrame]:
    payout = (
        frame["trade_side_up"].to_numpy(bool) == frame["settlement_up"].to_numpy(bool)
    ).astype(float)
    fees = np.asarray(
        [
            polymarket_taker_fee_per_share(value) if ok else 0.0
            for value, ok in zip(entry_ask, accepted)
        ],
        dtype=float,
    )
    pnl = np.where(accepted, payout - entry_ask - fees, 0.0)
    rows = frame[
        [
            "condition_id",
            "day",
            "t",
            "seconds_left",
            "abs_z_distance",
            "trade_side_up",
            "settlement_up",
            "event_disagreement",
        ]
    ].copy()
    rows["policy"] = name
    rows["accepted"] = accepted
    rows["entry_ask"] = np.where(accepted, entry_ask, np.nan)
    rows["immediate_entry_ask"] = baseline_entry_ask
    rows["entry_price_improvement"] = np.where(
        accepted, baseline_entry_ask - entry_ask, np.nan
    )
    rows["pnl"] = pnl
    rows["pnl_delta_vs_immediate"] = pnl - baseline_pnl
    rows["missed_profitable_trade"] = (~accepted) & (baseline_pnl > 0.0)
    rows["avoided_losing_trade"] = (~accepted) & (baseline_pnl < 0.0)
    accepted_count = int(accepted.sum())
    week = pd.to_datetime(rows["day"], utc=True).dt.strftime("%G-W%V")
    weekly_delta = (
        rows.assign(week=week).groupby("week")["pnl_delta_vs_immediate"].sum()
    )
    metrics = {
        "name": name,
        "original_candidates": len(frame),
        "accepted": accepted_count,
        "coverage": accepted_count / len(frame) if len(frame) else math.nan,
        "win_rate": float(payout[accepted].mean()) if accepted_count else math.nan,
        "average_pnl_per_accepted": float(pnl[accepted].mean())
        if accepted_count
        else math.nan,
        "average_pnl_per_original_candidate": float(pnl.mean())
        if len(frame)
        else math.nan,
        "profit_factor": profit_factor(pnl[accepted]) if accepted_count else math.nan,
        "total_pnl": float(pnl.sum()),
        "total_pnl_delta_vs_immediate": float((pnl - baseline_pnl).sum()),
        "average_entry_price_improvement": float(
            np.nanmean(rows["entry_price_improvement"])
        )
        if accepted_count
        else math.nan,
        "missed_profitable_trades": int(rows["missed_profitable_trade"].sum()),
        "avoided_losing_trades": int(rows["avoided_losing_trade"].sum()),
        "positive_week_fraction": float((weekly_delta > 0.0).mean())
        if len(weekly_delta)
        else math.nan,
        "day_block_pnl_delta_lower_95": bootstrap_day_lower(
            rows, "pnl_delta_vs_immediate"
        ),
    }
    return metrics, rows


def run_execution_policies(
    development: pd.DataFrame,
    locked: pd.DataFrame,
    protocol: dict[str, Any],
    model_dir: Path,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    baseline_features = [
        "market_prob_up",
        "distance_bps",
        "z_distance",
        "seconds_ratio",
        "rv_60s_bps",
        "spread_sum",
        "ask_sum_minus_one",
    ]
    fit = development[development["role"] == "fit"]
    calibration = development[development["role"] == "calibration"]
    settlement = fit_calibrated(fit, calibration, baseline_features, "settlement_up")
    joblib.dump(settlement, model_dir / "settlement_side_baseline.joblib")
    test = locked.copy()
    test["fair_up"] = settlement.predict(test)
    test["trade_side_up"] = test["fair_up"] >= 0.5
    test["fair_side"] = np.where(
        test["trade_side_up"], test["fair_up"], 1.0 - test["fair_up"]
    )
    test["entry_ask"] = np.where(test["trade_side_up"], test["au"], test["ad"])
    test["entry_fee"] = fee_array(test["entry_ask"])
    test["net_edge"] = (
        test["fair_side"]
        - test["entry_ask"]
        - test["entry_fee"]
        - float(protocol["execution"]["required_edge_buffer"])
    )
    minimum = int(protocol["data_rules"]["minimum_seconds_left"])
    maximum = int(protocol["data_rules"]["maximum_seconds_left"])
    candidates = test[
        test["seconds_left"].between(minimum, maximum)
        & (test["net_edge"] > 0.0)
        & np.isfinite(test["entry_ask"])
    ].copy()
    candidates = (
        candidates.sort_values(["condition_id", "t"])
        .groupby("condition_id", as_index=False)
        .head(1)
    )
    event_side_up = candidates["event_persistent_score"] >= 0.0
    candidates["event_disagreement"] = (
        (event_side_up != candidates["trade_side_up"])
        & (candidates["event_persistent_score"].abs() >= 0.12)
        & (candidates["p_move_5"] >= 0.5)
    )
    immediate_accept = np.ones(len(candidates), dtype=bool)
    immediate_ask = candidates["entry_ask"].to_numpy(float)
    payout = (
        candidates["trade_side_up"].to_numpy(bool)
        == candidates["settlement_up"].to_numpy(bool)
    ).astype(float)
    immediate_pnl = payout - immediate_ask - fee_array(candidates["entry_ask"])

    veto_accept = ~candidates["event_disagreement"].to_numpy(bool)
    veto_ask = immediate_ask.copy()

    delay_seconds = int(protocol["execution"]["delay_seconds"])

    def delayed_quotes(seconds: int, prefix: str) -> pd.DataFrame:
        lookup = locked[["condition_id", "t", "au", "ad"]].copy()
        lookup["t"] = lookup["t"] - seconds
        lookup = lookup.rename(columns={"au": f"{prefix}_au", "ad": f"{prefix}_ad"})
        return candidates[["condition_id", "t"]].merge(
            lookup,
            on=["condition_id", "t"],
            how="left",
            validate="one_to_one",
        )

    delayed = delayed_quotes(delay_seconds, "delay")
    delayed_ask = np.where(
        candidates["trade_side_up"].to_numpy(bool),
        delayed["delay_au"].to_numpy(float),
        delayed["delay_ad"].to_numpy(float),
    )
    use_delay = candidates["event_disagreement"].to_numpy(bool)
    delay_accept = (~use_delay) | np.isfinite(delayed_ask)
    delay_entry = np.where(use_delay, delayed_ask, immediate_ask)

    definitions = [
        ("immediate_entry", immediate_accept, immediate_ask),
        ("event_disagreement_veto", veto_accept, veto_ask),
        ("event_disagreement_delay_5s", delay_accept, delay_entry),
    ]
    results: list[dict[str, Any]] = []
    rows: list[pd.DataFrame] = []
    gate = protocol["execution_policy_gate"]
    for index, (name, accepted, ask) in enumerate(definitions, start=1):
        metrics, policy_rows = execution_policy_metrics(
            name, candidates, accepted, ask, immediate_pnl, immediate_ask
        )
        checks = {
            "enough_candidates": metrics["original_candidates"]
            >= int(gate["minimum_original_candidates"]),
            "positive_total_delta": metrics["total_pnl_delta_vs_immediate"]
            > float(gate["minimum_total_pnl_delta"]),
            "positive_day_block_delta": metrics["day_block_pnl_delta_lower_95"]
            > float(gate["minimum_day_block_pnl_delta_lower_95"]),
        }
        metrics.update(
            {
                "experiment_id": f"E0{index}",
                "family": "execution",
                "research_gate_passed": index != 1 and all(checks.values()),
                "promoted": False,
                "promotion_blocker": "forward shadow and live depth/latency evidence required",
                "gate_checks": checks,
            }
        )
        if name == "event_disagreement_delay_5s":
            stressed = delayed_quotes(delay_seconds + 2, "stress")
            stressed_ask = np.where(
                candidates["trade_side_up"].to_numpy(bool),
                stressed["stress_au"].to_numpy(float),
                stressed["stress_ad"].to_numpy(float),
            )
            stressed_entry = np.where(use_delay, stressed_ask, immediate_ask)
            stressed_accept = (~use_delay) | np.isfinite(stressed_entry)
            stressed_fee = np.asarray(
                [
                    polymarket_taker_fee_per_share(value) if ok else 0.0
                    for value, ok in zip(stressed_entry, stressed_accept)
                ],
                dtype=float,
            )
            stressed_pnl = np.where(
                stressed_accept, payout - stressed_entry - stressed_fee, 0.0
            )
            metrics["two_second_latency_stress_total_pnl"] = float(stressed_pnl.sum())
            metrics["two_second_latency_stress_delta_vs_immediate"] = float(
                (stressed_pnl - immediate_pnl).sum()
            )
        results.append(metrics)
        rows.append(policy_rows)
        log(
            f"[E0{index}] {name} candidates={len(candidates):,} "
            f"accepted={metrics['accepted']:,} total={metrics['total_pnl']:+.3f} "
            f"delta={metrics['total_pnl_delta_vs_immediate']:+.3f}"
        )
    if results:
        results[0]["research_gate_passed"] = False
        results[0]["gate_checks"]["baseline_not_promotable"] = False
    return results, pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def contract_lead_lag_diagnostics(data: pd.DataFrame) -> pd.DataFrame:
    base = data[(data["event_abs_score"] >= 0.12) & (data["p_move_5"] >= 0.5)].copy()
    base["event_side"] = np.where(base["event_score"] >= 0.0, "UP", "DOWN")
    base["current_side_ask"] = np.where(
        base["event_side"] == "UP", base["au"], base["ad"]
    )
    rows: list[dict[str, Any]] = []
    for latency in (1, 2, 5, 10):
        lookup = data[["condition_id", "t", "au", "ad"]].copy()
        lookup["t"] = lookup["t"] - latency
        lookup = lookup.rename(columns={"au": "future_au", "ad": "future_ad"})
        joined = base.merge(
            lookup,
            on=["condition_id", "t"],
            how="left",
            validate="one_to_one",
        )
        joined["future_side_ask"] = np.where(
            joined["event_side"] == "UP", joined["future_au"], joined["future_ad"]
        )
        joined["ask_change"] = joined["future_side_ask"] - joined["current_side_ask"]
        for side, subset in joined.groupby("event_side"):
            valid = subset[np.isfinite(subset["ask_change"])].copy()
            rows.append(
                {
                    "latency_seconds": latency,
                    "event_side": side,
                    "rows": len(valid),
                    "mean_ask_change": float(valid["ask_change"].mean()),
                    "median_ask_change": float(valid["ask_change"].median()),
                    "probability_worsens_1c": float(
                        (valid["ask_change"] >= 0.01).mean()
                    ),
                    "probability_improves_1c": float(
                        (valid["ask_change"] <= -0.01).mean()
                    ),
                }
            )
    return pd.DataFrame(rows)


def execution_slice_summary(rows: pd.DataFrame) -> pd.DataFrame:
    data = rows.copy()
    data["distance_zone"] = pd.cut(
        data["abs_z_distance"],
        bins=[-np.inf, 1.0, 3.0, np.inf],
        labels=["NEAR", "INTERMEDIATE", "FAR"],
    )
    data["time_zone"] = pd.cut(
        data["seconds_left"],
        bins=[14, 30, 60, 120],
        labels=["15-30s", "31-60s", "61-120s"],
        include_lowest=True,
    )
    pieces: list[pd.DataFrame] = []
    for dimension in ("day", "distance_zone", "time_zone"):
        summary = (
            data.groupby(["policy", dimension], observed=True)
            .agg(
                original_candidates=("condition_id", "size"),
                accepted=("accepted", "sum"),
                total_pnl=("pnl", "sum"),
                average_pnl=("pnl", "mean"),
                average_entry_price_improvement=("entry_price_improvement", "mean"),
                missed_profitable_trades=("missed_profitable_trade", "sum"),
                avoided_losing_trades=("avoided_losing_trade", "sum"),
            )
            .reset_index()
            .rename(columns={dimension: "slice"})
        )
        summary.insert(1, "dimension", dimension)
        pieces.append(summary)
    return pd.concat(pieces, ignore_index=True)


def profit_factor(values: np.ndarray) -> float:
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    return (
        positive / negative if negative > 0 else math.inf if positive > 0 else math.nan
    )


def greedy_nonoverlap(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    kept: list[int] = []
    next_allowed = -1
    for index, timestamp in zip(frame.index, frame["timestamp_s"].to_numpy(np.int64)):
        if timestamp >= next_allowed:
            kept.append(index)
            next_allowed = int(timestamp) + horizon
    return frame.loc[kept].copy()


def run_btc_proxy(
    experiment_id: str,
    horizon: int,
    prediction_path: Path,
    prices: pd.DataFrame,
    protocol: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    raw = pd.read_parquet(prediction_path)
    data = raw[raw["horizon_seconds"] == horizon].copy().sort_values("timestamp_s")
    price_map = prices.set_index("timestamp_s")["spot_price"]
    data["entry_price"] = price_map.reindex(data["timestamp_s"]).to_numpy()
    data["exit_price"] = price_map.reindex(data["timestamp_s"] + horizon).to_numpy()
    data = data[
        np.isfinite(data[["entry_price", "exit_price"]].to_numpy(float)).all(axis=1)
        & (
            data["p_movement"]
            >= float(protocol["btc_proxy_execution"]["minimum_movement_probability"])
        )
        & (
            (data["p_up_first"] - 0.5).abs()
            >= float(protocol["btc_proxy_execution"]["minimum_direction_margin"])
        )
    ].copy()
    data = greedy_nonoverlap(data, horizon)
    side = np.where(data["p_up_first"] >= 0.5, 1.0, -1.0)
    data["gross_bps"] = (
        side * (data["exit_price"] / data["entry_price"] - 1.0) * 10_000.0
    )
    metrics: dict[str, Any] = {
        "experiment_id": experiment_id,
        "family": "btc_proxy",
        "name": f"event_{horizon}s_microtrade",
        "calls": len(data),
        "gross_win_rate": float((data["gross_bps"] > 0).mean())
        if len(data)
        else math.nan,
        "gross_mean_bps": float(data["gross_bps"].mean()) if len(data) else math.nan,
        "gross_profit_factor": profit_factor(data["gross_bps"].to_numpy(float)),
        "promoted": False,
        "promotion_blocker": "no historical executable Binance bid/ask",
    }
    for cost in protocol["btc_proxy_execution"]["round_trip_cost_stress_bps"]:
        column = f"net_{cost}bps_cost"
        data[column] = data["gross_bps"] - float(cost)
        metrics[f"mean_net_at_{cost}bps"] = (
            float(data[column].mean()) if len(data) else math.nan
        )
        metrics[f"profit_factor_at_{cost}bps"] = (
            profit_factor(data[column].to_numpy(float)) if len(data) else math.nan
        )
    log(
        f"[{experiment_id}] event_{horizon}s_microtrade calls={len(data):,} "
        f"gross={metrics['gross_mean_bps']:+.3f}bps PF={metrics['gross_profit_factor']:.3f}"
    )
    return metrics, data


def run_campaign(output_root: Path) -> Path:
    global _LOG_PATH
    started = time.perf_counter()
    protocol = load_protocol()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / run_id
    model_dir = run_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=False)
    _LOG_PATH = run_dir / "run.log"
    shutil.copy2(PROTOCOL_PATH, run_dir / "frozen_protocol_snapshot.json")
    development_path = resolve_input(
        protocol["inputs"]["development_event_predictions"]
    )
    locked_path = resolve_input(protocol["inputs"]["locked_event_predictions"])
    archive_path = resolve_input(protocol["inputs"]["polymarket_archive"])
    inputs = [development_path, locked_path, archive_path, PROTOCOL_PATH]
    manifest = {
        "inputs": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in inputs
        ]
    }
    (run_dir / "input_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    log(f"[start] {protocol['protocol_id']} output={run_dir}")

    dev_prediction = pd.read_parquet(
        development_path, columns=["timestamp_s", "horizon_seconds"]
    )
    locked_prediction = pd.read_parquet(
        locked_path, columns=["timestamp_s", "horizon_seconds"]
    )
    dev_book = load_kacho(
        archive_path,
        int(dev_prediction["timestamp_s"].min()),
        int(dev_prediction["timestamp_s"].max()),
    )
    locked_book = load_kacho(
        archive_path,
        int(locked_prediction["timestamp_s"].min()),
        int(locked_prediction["timestamp_s"].max()),
    )
    development, dev_prices = attach_event_and_spot(dev_book, development_path)
    locked, locked_prices = attach_event_and_spot(locked_book, locked_path)
    if development["t"].max() >= locked["t"].min():
        raise AssertionError("development and locked quote periods overlap")
    development = assign_development_roles(
        development, float(protocol["data_rules"]["development_market_split"][0])
    )
    development = attach_future_book_targets(development)
    locked = attach_future_book_targets(locked)
    development = attach_crossing_targets(development, dev_prices)
    locked = attach_crossing_targets(locked, locked_prices)

    results, execution_rows = run_execution_policies(
        development, locked, protocol, model_dir
    )
    prediction_rows: list[pd.DataFrame] = []
    crossing_baseline = [
        "abs_distance_bps",
        "abs_z_distance",
        "seconds_ratio",
        "rv_60s_bps",
        "market_prob_up",
        "spread_sum",
    ]
    crossing_evidence = [
        "p_move_5",
        "p_move_15",
        "p_roundtrip_5",
        "p_roundtrip_15",
        "event_abs_score",
        "event_disagrees_current",
    ]
    gate = protocol["incremental_model_gate"]
    crossing_specs = [
        ("E04", "anchor_cross_within_5s", "anchor_cross_5s"),
        ("E05", "anchor_cross_within_15s", "anchor_cross_15s"),
        ("E06", "cross_then_recross_within_15s", "cross_then_recross_15s"),
    ]
    for experiment_id, name, target in crossing_specs:
        result, rows = incremental_experiment(
            experiment_id,
            name,
            target,
            development,
            locked,
            crossing_baseline,
            crossing_evidence,
            gate,
            model_dir,
        )
        result["family"] = "crossing"
        results.append(result)
        prediction_rows.append(rows)

    contract_baseline = [
        "market_prob_up",
        "seconds_ratio",
        "rv_60s_bps",
        "distance_bps",
        "z_distance",
        "spread_up",
        "spread_down",
        "sau",
        "sad",
        "du",
        "dd",
        "au",
        "ad",
    ]
    contract_evidence = [
        "p_up_5",
        "p_move_5",
        "p_roundtrip_5",
        "p_up_15",
        "p_move_15",
        "p_roundtrip_15",
        "event_score",
        "event_persistent_score",
    ]
    contract_specs = [
        ("E07", "up_ask_worsens_1c_within_5s", "up_ask_worsens_5s"),
        ("E08", "down_ask_worsens_1c_within_5s", "down_ask_worsens_5s"),
    ]
    for experiment_id, name, target in contract_specs:
        result, rows = incremental_experiment(
            experiment_id,
            name,
            target,
            development,
            locked,
            contract_baseline,
            contract_evidence,
            gate,
            model_dir,
        )
        result["family"] = "contract_repricing"
        results.append(result)
        prediction_rows.append(rows)

    btc_results: list[pd.DataFrame] = []
    for experiment_id, horizon in (("E09", 5), ("E10", 15)):
        result, rows = run_btc_proxy(
            experiment_id, horizon, locked_path, locked_prices, protocol
        )
        results.append(result)
        btc_results.append(rows)

    ordered = sorted(results, key=lambda item: item["experiment_id"])
    if [item["experiment_id"] for item in ordered] != [
        f"E{index:02d}" for index in range(1, 11)
    ]:
        raise AssertionError("campaign did not produce exactly E01..E10")
    metrics_frame = pd.DataFrame(
        [
            {key: value for key, value in item.items() if key != "gate_checks"}
            for item in ordered
        ]
    )
    metrics_frame.to_csv(run_dir / "experiment_metrics.csv", index=False)
    if not execution_rows.empty:
        execution_rows.to_parquet(
            run_dir / "execution_policy_candidates.parquet", index=False
        )
        execution_slice_summary(execution_rows).to_csv(
            run_dir / "execution_slices.csv", index=False
        )
    contract_lead_lag_diagnostics(locked).to_csv(
        run_dir / "contract_lead_lag.csv", index=False
    )
    if prediction_rows:
        pd.concat(prediction_rows, ignore_index=True).to_parquet(
            run_dir / "locked_incremental_predictions.parquet", index=False
        )
    if btc_results:
        pd.concat(btc_results, ignore_index=True).to_parquet(
            run_dir / "btc_proxy_trades.parquet", index=False
        )
    split_manifest = {
        "development_min": int(development["t"].min()),
        "development_max": int(development["t"].max()),
        "locked_min": int(locked["t"].min()),
        "locked_max": int(locked["t"].max()),
        "development_markets": int(development["condition_id"].nunique()),
        "locked_markets": int(locked["condition_id"].nunique()),
        "development_roles": development.groupby("role")["condition_id"]
        .nunique()
        .to_dict(),
    }
    (run_dir / "split_manifest.json").write_text(
        json.dumps(json_safe(split_manifest), indent=2), encoding="utf-8"
    )
    summary = {
        "protocol_id": protocol["protocol_id"],
        "run_id": run_id,
        "runtime_seconds": time.perf_counter() - started,
        "result_count": len(ordered),
        "promoted": [item["experiment_id"] for item in ordered if item.get("promoted")],
        "research_gate_passed": [
            item["experiment_id"]
            for item in ordered
            if item.get("research_gate_passed")
        ],
        "results": ordered,
    }
    (run_dir / "results.json").write_text(
        json.dumps(json_safe(summary), indent=2), encoding="utf-8"
    )
    log(
        f"[done] experiments={len(ordered)} research_gate_passed="
        f"{summary['research_gate_passed']} promoted={summary['promoted']} "
        f"elapsed={summary['runtime_seconds']:.1f}s"
    )
    return run_dir


def selftest() -> None:
    assert [item["id"] for item in load_protocol()["experiments"]] == [
        f"E{index:02d}" for index in range(1, 11)
    ]
    assert polymarket_taker_fee_per_share(0.5) == 0.0175
    sample = pd.DataFrame(
        {
            "timestamp_s": [0, 5, 10],
            "p_up_first": [0.7, 0.3, 0.8],
            "p_movement": [0.8, 0.8, 0.8],
        }
    )
    assert len(greedy_nonoverlap(sample, 5)) == 3
    assert len(greedy_nonoverlap(sample, 6)) == 2
    assert profit_factor(np.array([2.0, -1.0])) == 2.0
    print("event_execution_v1 self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    run_dir = run_campaign(args.output_root)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
