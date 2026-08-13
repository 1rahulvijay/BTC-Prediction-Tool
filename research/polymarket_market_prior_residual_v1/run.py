#!/usr/bin/env python
"""Causal Polymarket market-prior residual experiment.

This campaign asks whether anchor, path and volatility state add incremental
settlement information beyond the executable market's own probability. It is
strictly research-only: no model artifact is saved and no order path is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_DB = ROOT / "data" / "execution_layer.duckdb"
DEFAULT_SNAPSHOTS = ROOT / "data" / "pm_export_snapshots.parquet"
DEFAULT_SETTLEMENTS = ROOT / "data" / "pm_export_settlements.parquet"
DEFAULT_OUTPUT = (
    ROOT / "data" / "research" / "polymarket_market_prior_residual_v1" / "latest"
)

MODEL_A = "A_market_prior"
MODEL_B = "B_anchor_time"
MODEL_C = "C_market_prior_anchor_residual"
MODEL_D = "D_market_prior_full_residual"
MODELS = (MODEL_A, MODEL_B, MODEL_C, MODEL_D)

B_FEATURES = (
    "signed_distance_bps",
    "abs_distance_bps",
    "horizon_15m",
)
D_FEATURES = (
    *B_FEATURES,
    "vol_60s_pct",
    "path_range_bps",
    "flips_so_far",
    "distance_velocity_10s_bps",
    "side_age_s",
    "up_spread",
    "down_spread",
    "log_up_depth",
    "log_down_depth",
    "complete_set_ask_sum",
    "p_hold_up",
    "market_phold_disagreement",
)


def _load_protocol() -> dict[str, Any]:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _frame_sha256(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    normalized = normalized.reindex(sorted(normalized.columns), axis=1)
    values = pd.util.hash_pandas_object(normalized, index=True).to_numpy(
        dtype=np.uint64
    )
    return hashlib.sha256(values.tobytes()).hexdigest()


def _code_identity() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _clip_probability(values: Iterable[float]) -> np.ndarray:
    return np.clip(np.asarray(values, dtype=float), 1e-5, 1.0 - 1e-5)


def _logit(values: Iterable[float]) -> np.ndarray:
    probability = _clip_probability(values)
    return np.log(probability / (1.0 - probability))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    output = np.empty_like(values)
    positive = values >= 0
    output[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    negative = ~positive
    exp_values = np.exp(values[negative])
    output[negative] = exp_values / (1.0 + exp_values)
    return output


@dataclass
class OffsetLogit:
    l2_penalty: float
    medians: np.ndarray | None = None
    scales: np.ndarray | None = None
    coefficients: np.ndarray | None = None

    def _transform(self, matrix: np.ndarray, *, fit: bool) -> np.ndarray:
        values = np.asarray(matrix, dtype=float)
        if values.ndim != 2:
            raise ValueError("feature matrix must be two-dimensional")
        if fit:
            with np.errstate(all="ignore"):
                medians = np.nanmedian(values, axis=0)
            medians = np.where(np.isfinite(medians), medians, 0.0)
            filled = np.where(np.isfinite(values), values, medians)
            scales = np.std(filled, axis=0)
            scales = np.where(np.isfinite(scales) & (scales > 1e-8), scales, 1.0)
            self.medians = medians
            self.scales = scales
        if self.medians is None or self.scales is None:
            raise RuntimeError("model preprocessing has not been fitted")
        filled = np.where(np.isfinite(values), values, self.medians)
        standardized = (filled - self.medians) / self.scales
        return np.column_stack([np.ones(len(standardized)), standardized])

    def fit(
        self,
        matrix: np.ndarray,
        target: np.ndarray,
        *,
        offset: np.ndarray | None = None,
    ) -> "OffsetLogit":
        design = self._transform(matrix, fit=True)
        target_values = np.asarray(target, dtype=float)
        if len(target_values) != len(design) or len(np.unique(target_values)) != 2:
            raise ValueError("binary training target must contain both classes")
        offset_values = (
            np.zeros(len(design), dtype=float)
            if offset is None
            else np.asarray(offset, dtype=float)
        )
        if len(offset_values) != len(design):
            raise ValueError("offset length differs from the feature matrix")
        penalty = float(self.l2_penalty)

        def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
            eta = offset_values + design @ beta
            probability = _sigmoid(eta)
            loss = float(np.logaddexp(0.0, eta).sum() - np.dot(target_values, eta))
            regularized = beta.copy()
            regularized[0] = 0.0
            loss += 0.5 * penalty * float(np.dot(regularized, regularized))
            gradient = design.T @ (probability - target_values)
            gradient += penalty * regularized
            return loss, gradient

        result = minimize(
            objective,
            np.zeros(design.shape[1], dtype=float),
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": 1_000, "ftol": 1e-12},
        )
        if not result.success or not np.isfinite(result.x).all():
            raise RuntimeError(f"offset-logit fit failed: {result.message}")
        self.coefficients = np.asarray(result.x, dtype=float)
        return self

    def predict(
        self, matrix: np.ndarray, *, offset: np.ndarray | None = None
    ) -> np.ndarray:
        if self.coefficients is None:
            raise RuntimeError("model has not been fitted")
        design = self._transform(matrix, fit=False)
        offset_values = (
            np.zeros(len(design), dtype=float)
            if offset is None
            else np.asarray(offset, dtype=float)
        )
        return _clip_probability(_sigmoid(offset_values + design @ self.coefficients))


def _load_data(
    db_path: Path,
    snapshots_path: Path | None = None,
    settlements_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if snapshots_path is not None or settlements_path is not None:
        if snapshots_path is None or settlements_path is None:
            raise ValueError("both snapshot and settlement Parquet paths are required")
        return pd.read_parquet(snapshots_path), pd.read_parquet(settlements_path)
    connection = duckdb.connect(str(db_path), read_only=True)
    snapshots = connection.execute("SELECT * FROM pm_round_snapshots").fetchdf()
    settlements = connection.execute("SELECT * FROM pm_round_settlements").fetchdf()
    connection.close()
    return snapshots, settlements


def _trustworthy_slugs(
    snapshots: pd.DataFrame, maximum_open_skew_seconds: float
) -> set[str]:
    first = (
        snapshots.sort_values("ts")
        .groupby("slug", as_index=False)
        .first()[["slug", "ts", "anchor_ts"]]
    )
    skew = (first["ts"].astype(float) - first["anchor_ts"].astype(float)).abs()
    return set(first.loc[skew <= maximum_open_skew_seconds, "slug"].astype(str))


def _side_age_seconds(history: pd.DataFrame) -> float:
    signed = np.sign(history["signed_distance_bps"].to_numpy(float))
    if not len(signed):
        return 0.0
    current = signed[-1]
    if current == 0:
        return 0.0
    start = len(signed) - 1
    while start > 0 and signed[start - 1] == current:
        start -= 1
    return float(history["ts"].iloc[-1] - history["ts"].iloc[start])


def _path_values(history: pd.DataFrame, selected: pd.Series) -> dict[str, float]:
    signed = history["signed_distance_bps"].to_numpy(float)
    signs = np.sign(signed)
    nonzero = signs[signs != 0]
    flips = int(np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) > 1 else 0
    cutoff = float(selected["ts"]) - 10.0
    prior = history[history["ts"] <= cutoff]
    old_distance = (
        float(prior["signed_distance_bps"].iloc[-1])
        if len(prior)
        else float(signed[0])
    )
    current_distance = float(selected["signed_distance_bps"])
    return {
        "path_range_bps": float(np.max(signed) - np.min(signed)),
        "flips_so_far": float(flips),
        "distance_velocity_10s_bps": current_distance - old_distance,
        "side_age_s": _side_age_seconds(history),
    }


def build_checkpoint_frame(
    snapshots: pd.DataFrame,
    settlements: pd.DataFrame,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, int]]:
    data_config = protocol["data"]
    allowed_sources = set(data_config["allowed_resolution_sources"])
    horizons = {int(value) for value in data_config["horizons_minutes"]}
    checkpoint = float(data_config["checkpoint_seconds_left"])
    tolerance = float(data_config["checkpoint_tolerance_seconds"])
    trustworthy = _trustworthy_slugs(
        snapshots, float(data_config["maximum_open_snapshot_skew_seconds"])
    )
    settlement = settlements[
        settlements["resolution_source"].astype(str).isin(allowed_sources)
        & settlements["horizon"].astype(int).isin(horizons)
    ].copy()
    settlement = settlement.drop_duplicates("slug", keep="last")
    settlement["slug"] = settlement["slug"].astype(str)
    settlement_by_slug = settlement.set_index("slug")

    source = snapshots[snapshots["slug"].astype(str).isin(trustworthy)].copy()
    source["slug"] = source["slug"].astype(str)
    source = source[source["slug"].isin(set(settlement_by_slug.index))]
    source["horizon"] = source["horizon"].astype(int)
    source = source[source["horizon"].isin(horizons)]
    source = source[
        (source["seconds_left"] >= 0)
        & (source["seconds_left"] <= source["horizon"] * 60 + tolerance)
    ]
    source["signed_distance_bps"] = (
        (source["btc_price"].astype(float) / source["anchor_price"].astype(float)) - 1.0
    ) * 10_000.0
    candidates = source[
        (source["seconds_left"] >= checkpoint)
        & (source["seconds_left"] <= checkpoint + tolerance)
        & source["up_ask"].between(0.001, 0.999)
        & source["down_ask"].between(0.001, 0.999)
        & source["up_bid"].between(0.0, 0.999)
        & source["down_bid"].between(0.0, 0.999)
    ].copy()
    candidates = candidates.sort_values(["slug", "seconds_left"]).drop_duplicates(
        "slug", keep="first"
    )

    rows: list[dict[str, Any]] = []
    for selected in candidates.itertuples(index=False):
        row = pd.Series(selected._asdict())
        slug = str(row["slug"])
        history = source[
            (source["slug"] == slug)
            & (source["ts"] >= float(row["anchor_ts"]))
            & (source["ts"] <= float(row["ts"]))
        ].sort_values("ts")
        if history.empty:
            continue
        up_mid = float(row["up_mid"])
        down_mid = float(row["down_mid"])
        midpoint_sum = up_mid + down_mid
        if not np.isfinite(midpoint_sum) or midpoint_sum <= 0:
            continue
        market_prior = float(np.clip(up_mid / midpoint_sum, 1e-5, 1.0 - 1e-5))
        settlement_row = settlement_by_slug.loc[slug]
        if int(settlement_row["up_win"]) + int(settlement_row["down_win"]) != 1:
            continue
        path = _path_values(history, row)
        up_depth = max(float(row["up_top_ask_size"]), 0.0)
        down_depth = max(float(row["down_top_ask_size"]), 0.0)
        p_hold_up = float(row["p_hold_up"])
        rows.append(
            {
                "slug": slug,
                "anchor_ts": float(row["anchor_ts"]),
                "decision_ts": float(row["ts"]),
                "outcome_end_ts": float(row["anchor_ts"]) + int(row["horizon"]) * 60,
                "day": pd.to_datetime(
                    float(row["anchor_ts"]), unit="s", utc=True
                ).strftime("%Y-%m-%d"),
                "horizon": int(row["horizon"]),
                "target_up": int(settlement_row["up_win"]),
                "market_prior": market_prior,
                "up_ask": float(row["up_ask"]),
                "down_ask": float(row["down_ask"]),
                "up_top_ask_size": up_depth,
                "down_top_ask_size": down_depth,
                "signed_distance_bps": float(row["signed_distance_bps"]),
                "abs_distance_bps": abs(float(row["signed_distance_bps"])),
                "horizon_15m": float(int(row["horizon"]) == 15),
                "vol_60s_pct": float(row["vol_60s_pct"]),
                "up_spread": float(row["up_spread"]),
                "down_spread": float(row["down_spread"]),
                "log_up_depth": math.log1p(up_depth),
                "log_down_depth": math.log1p(down_depth),
                "complete_set_ask_sum": float(row["up_ask"] + row["down_ask"]),
                "p_hold_up": p_hold_up,
                "market_phold_disagreement": p_hold_up - market_prior,
                **path,
            }
        )
    frame = pd.DataFrame(rows).sort_values(["anchor_ts", "horizon", "slug"])
    counts = {
        "snapshot_rows": int(len(snapshots)),
        "official_settlements": int(len(settlement)),
        "trustworthy_rounds": int(len(trustworthy)),
        "checkpoint_rounds": int(len(frame)),
    }
    return frame.reset_index(drop=True), counts


def expanding_day_predictions(
    frame: pd.DataFrame, protocol: dict[str, Any]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    validation = protocol["validation"]
    l2_penalty = float(protocol["models"]["l2_penalty"])
    days = sorted(frame["day"].unique())
    first_training_days = int(validation["first_training_days"])
    predictions: list[pd.DataFrame] = []
    folds: list[dict[str, Any]] = []
    for test_day in days[first_training_days:]:
        test_start = pd.Timestamp(test_day, tz="UTC").timestamp()
        train = frame[
            frame["outcome_end_ts"]
            < test_start - float(validation["purge_seconds"])
        ]
        test = frame[frame["day"] == test_day]
        if len(train) < int(validation["minimum_training_rounds"]):
            continue
        if len(test) < int(validation["minimum_test_rounds_per_day"]):
            continue
        target = train["target_up"].to_numpy(int)
        if len(np.unique(target)) != 2:
            continue
        market_train_offset = _logit(train["market_prior"])
        market_test_offset = _logit(test["market_prior"])
        model_b = OffsetLogit(l2_penalty).fit(
            train.loc[:, B_FEATURES].to_numpy(float), target
        )
        model_c = OffsetLogit(l2_penalty).fit(
            train.loc[:, B_FEATURES].to_numpy(float),
            target,
            offset=market_train_offset,
        )
        model_d = OffsetLogit(l2_penalty).fit(
            train.loc[:, D_FEATURES].to_numpy(float),
            target,
            offset=market_train_offset,
        )
        result = test.copy()
        result[MODEL_A] = _clip_probability(test["market_prior"])
        result[MODEL_B] = model_b.predict(
            test.loc[:, B_FEATURES].to_numpy(float)
        )
        result[MODEL_C] = model_c.predict(
            test.loc[:, B_FEATURES].to_numpy(float), offset=market_test_offset
        )
        result[MODEL_D] = model_d.predict(
            test.loc[:, D_FEATURES].to_numpy(float), offset=market_test_offset
        )
        result["fold_test_day"] = test_day
        predictions.append(result)
        folds.append(
            {
                "test_day": test_day,
                "training_rounds": int(len(train)),
                "test_rounds": int(len(test)),
                "training_end_ts": float(train["outcome_end_ts"].max()),
                "test_start_ts": float(test["anchor_ts"].min()),
                "purge_seconds_observed": float(
                    test["anchor_ts"].min() - train["outcome_end_ts"].max()
                ),
            }
        )
    if not predictions:
        return pd.DataFrame(), folds
    return pd.concat(predictions, ignore_index=True), folds


def _ece(target: np.ndarray, probability: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    bucket = np.minimum(np.digitize(probability, edges[1:-1]), bins - 1)
    total = len(target)
    value = 0.0
    for index in range(bins):
        selected = bucket == index
        if selected.any():
            value += (
                float(selected.sum())
                / total
                * abs(float(target[selected].mean() - probability[selected].mean()))
            )
    return value


def probability_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    segments = [("ALL", predictions)]
    segments.extend(
        (f"{int(horizon)}m", group)
        for horizon, group in predictions.groupby("horizon")
    )
    for segment, group in segments:
        target = group["target_up"].to_numpy(int)
        for model in MODELS:
            probability = _clip_probability(group[model])
            rows.append(
                {
                    "segment": segment,
                    "model": model,
                    "rounds": int(len(group)),
                    "days": int(group["day"].nunique()),
                    "brier": float(brier_score_loss(target, probability)),
                    "log_loss": float(log_loss(target, probability, labels=[0, 1])),
                    "ece_10": _ece(target, probability),
                    "auc": (
                        float(roc_auc_score(target, probability))
                        if len(np.unique(target)) == 2
                        else math.nan
                    ),
                    "mean_probability": float(probability.mean()),
                    "up_rate": float(target.mean()),
                }
            )
    return pd.DataFrame(rows)


def _taker_fee(price: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(price, dtype=float), 0.0, 1.0)
    return 0.07 * values * (1.0 - values)


def _day_block_lower(
    actions: pd.DataFrame, random_seed: int, repetitions: int = 5_000
) -> float:
    if actions.empty:
        return math.nan
    days = sorted(actions["day"].unique())
    if len(days) < 2:
        return math.nan
    grouped = {
        day: actions.loc[actions["day"] == day, "net_pnl"].to_numpy(float)
        for day in days
    }
    rng = np.random.default_rng(random_seed)
    estimates = np.empty(repetitions, dtype=float)
    for index in range(repetitions):
        sampled_days = rng.choice(days, size=len(days), replace=True)
        sample = np.concatenate([grouped[day] for day in sampled_days])
        estimates[index] = float(sample.mean())
    return float(np.quantile(estimates, 0.025))


def action_metrics(
    predictions: pd.DataFrame, protocol: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    economics = protocol["economics"]
    buffer = float(economics["edge_buffer"])
    minimum_depth = float(protocol["data"]["minimum_top_ask_size"])
    random_seed = int(protocol["validation"]["random_seed"])
    summaries: list[dict[str, Any]] = []
    details: list[pd.DataFrame] = []
    for model in MODELS:
        frame = predictions.copy()
        probability = _clip_probability(frame[model])
        up_fee = _taker_fee(frame["up_ask"].to_numpy(float))
        down_fee = _taker_fee(frame["down_ask"].to_numpy(float))
        up_edge = probability - frame["up_ask"].to_numpy(float) - up_fee
        down_edge = (
            1.0 - probability - frame["down_ask"].to_numpy(float) - down_fee
        )
        choose_up = up_edge >= down_edge
        chosen_edge = np.where(choose_up, up_edge, down_edge)
        depth = np.where(
            choose_up,
            frame["up_top_ask_size"].to_numpy(float),
            frame["down_top_ask_size"].to_numpy(float),
        )
        acted = (chosen_edge >= buffer) & (depth >= minimum_depth)
        action = frame.loc[acted].copy()
        action["model"] = model
        action["side"] = np.where(choose_up[acted], "UP", "DOWN")
        action["predicted_edge"] = chosen_edge[acted]
        action["entry_ask"] = np.where(
            choose_up[acted],
            action["up_ask"].to_numpy(float),
            action["down_ask"].to_numpy(float),
        )
        action["entry_fee"] = _taker_fee(action["entry_ask"].to_numpy(float))
        action["won"] = np.where(
            action["side"] == "UP",
            action["target_up"].to_numpy(int) == 1,
            action["target_up"].to_numpy(int) == 0,
        )
        action["net_pnl"] = (
            action["won"].astype(float) - action["entry_ask"] - action["entry_fee"]
        )
        details.append(action)
        positive = float(action.loc[action["net_pnl"] > 0, "net_pnl"].sum())
        negative = abs(float(action.loc[action["net_pnl"] < 0, "net_pnl"].sum()))
        daily = action.groupby("day")["net_pnl"].sum() if len(action) else pd.Series()
        positive_daily = daily.clip(lower=0.0)
        summaries.append(
            {
                "model": model,
                "oos_rounds": int(len(frame)),
                "actions": int(len(action)),
                "coverage": float(len(action) / len(frame)) if len(frame) else 0.0,
                "win_rate": float(action["won"].mean()) if len(action) else math.nan,
                "average_entry_ask": (
                    float(action["entry_ask"].mean()) if len(action) else math.nan
                ),
                "average_predicted_edge": (
                    float(action["predicted_edge"].mean())
                    if len(action)
                    else math.nan
                ),
                "net_pnl": float(action["net_pnl"].sum()) if len(action) else 0.0,
                "mean_net_pnl": (
                    float(action["net_pnl"].mean()) if len(action) else math.nan
                ),
                "profit_factor": (
                    positive / negative
                    if negative > 0
                    else math.inf
                    if positive > 0
                    else math.nan
                ),
                "day_block_mean_lower_95": _day_block_lower(
                    action, random_seed
                ),
                "positive_day_profit_concentration": (
                    float(positive_daily.max() / positive_daily.sum())
                    if len(positive_daily) and positive_daily.sum() > 0
                    else math.nan
                ),
            }
        )
    return (
        pd.DataFrame(summaries),
        pd.concat(details, ignore_index=True) if details else pd.DataFrame(),
    )


def gate_status(
    frame: pd.DataFrame,
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    actions: pd.DataFrame,
    protocol: dict[str, Any],
) -> dict[str, Any]:
    gate = protocol["promotion_gate"]
    primary = protocol["validation"]["primary_model"]
    champion = protocol["validation"]["champion_baseline"]
    all_metrics = metrics[metrics["segment"] == "ALL"].set_index("model")
    action_metrics_by_model = actions.set_index("model")
    primary_actions = action_metrics_by_model.loc[primary]
    checks = {
        "minimum_independent_oos_rounds": len(predictions)
        >= int(gate["minimum_independent_oos_rounds"]),
        "minimum_calendar_weeks": (
            (
                pd.to_datetime(frame["anchor_ts"].max(), unit="s", utc=True)
                - pd.to_datetime(frame["anchor_ts"].min(), unit="s", utc=True)
            ).days
            / 7.0
            >= float(gate["minimum_calendar_weeks"])
        ),
        "minimum_actions": int(primary_actions["actions"])
        >= int(gate["minimum_actions"]),
        "primary_brier_below_market": float(all_metrics.loc[primary, "brier"])
        < float(all_metrics.loc[champion, "brier"]),
        "primary_log_loss_below_market": float(all_metrics.loc[primary, "log_loss"])
        < float(all_metrics.loc[champion, "log_loss"]),
        "positive_net_pnl": float(primary_actions["net_pnl"]) > 0.0,
        "positive_day_block_lower_95": (
            np.isfinite(primary_actions["day_block_mean_lower_95"])
            and float(primary_actions["day_block_mean_lower_95"]) > 0.0
        ),
        "single_positive_day_profit_share": (
            np.isfinite(primary_actions["positive_day_profit_concentration"])
            and float(primary_actions["positive_day_profit_concentration"])
            <= float(gate["maximum_single_positive_day_profit_share"])
        ),
        "automatic_promotion_disabled": not bool(gate["automatic_promotion"]),
    }
    return {
        "protocol_id": protocol["protocol_id"],
        "status": "research_only",
        "production_promoted": False,
        "paper_promoted": False,
        "primary_model": primary,
        "champion_baseline": champion,
        "checks": checks,
        "all_economic_and_evidence_checks_pass": all(
            value
            for name, value in checks.items()
            if name != "automatic_promotion_disabled"
        ),
        "reason": (
            "automatic promotion is forbidden; independent forward evidence is required"
        ),
    }


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None or not np.isfinite(float(value)):
        return "-"
    return f"{float(value):.{digits}f}"


def render_report(
    frame: pd.DataFrame,
    predictions: pd.DataFrame,
    folds: list[dict[str, Any]],
    metrics: pd.DataFrame,
    actions: pd.DataFrame,
    gates: dict[str, Any],
    counts: dict[str, int],
) -> str:
    lines = [
        "# POLY_MARKET_PRIOR_RESIDUAL_V1 Results",
        "",
        "## Verdict",
        "",
    ]
    primary_checks = gates["checks"]
    if (
        primary_checks["primary_brier_below_market"]
        and primary_checks["primary_log_loss_below_market"]
    ):
        lines.append(
            "The full residual improved proper scores over the market prior in this "
            "short sample, but it is **not promotable** because the independent-time "
            "and economic gates below remain mandatory."
        )
    else:
        lines.append(
            "The full residual did **not** beat the Polymarket market prior on both "
            "proper scores. The market remains champion; no residual model is promotable."
        )
    lines.extend(
        [
            "",
            "No serving artifact was written. No paper or live order was created.",
            "",
            "## Evidence",
            "",
            f"- raw snapshots: {counts['snapshot_rows']:,}",
            f"- official settlements: {counts['official_settlements']:,}",
            f"- trustworthy open-to-checkpoint rounds: {counts['trustworthy_rounds']:,}",
            f"- fixed 60-second checkpoint rounds: {counts['checkpoint_rounds']:,}",
            f"- chronological out-of-sample rounds: {len(predictions):,}",
            f"- out-of-sample days: {predictions['day'].nunique()}",
            f"- expanding day folds: {len(folds)}",
            f"- horizons: {', '.join(str(int(v)) + 'm' for v in sorted(frame['horizon'].unique()))}",
            "",
            "## Probability Metrics",
            "",
            "| segment | model | rounds | Brier | log loss | ECE | AUC |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in metrics.itertuples(index=False):
        lines.append(
            f"| {row.segment} | {row.model} | {row.rounds} | "
            f"{_fmt(row.brier)} | {_fmt(row.log_loss)} | {_fmt(row.ece_10)} | "
            f"{_fmt(row.auc)} |"
        )
    lines.extend(
        [
            "",
            "## One-Share Executable-Ask Policy",
            "",
            "The policy acts only when the model probability exceeds recorded top ask, "
            "the frozen crypto taker-fee formula and a 2-cent buffer. It assumes only "
            "one displayed share at the top level and holds to official settlement.",
            "",
            "| model | actions | coverage | win rate | avg ask | net PnL | mean PnL | PF | day-block LB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in actions.itertuples(index=False):
        lines.append(
            f"| {row.model} | {row.actions} | {_fmt(row.coverage, 3)} | "
            f"{_fmt(row.win_rate, 3)} | {_fmt(row.average_entry_ask, 3)} | "
            f"{_fmt(row.net_pnl, 3)} | {_fmt(row.mean_net_pnl, 4)} | "
            f"{_fmt(row.profit_factor, 3)} | "
            f"{_fmt(row.day_block_mean_lower_95, 4)} |"
        )
    lines.extend(
        [
            "",
            "## Promotion Gate",
            "",
            "| check | pass |",
            "|---|---|",
        ]
    )
    for name, passed in gates["checks"].items():
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "**Final status: RESEARCH ONLY / NO PROMOTION.**",
            "",
            "The quote recorder covers only a few calendar days. Even a positive score "
            "or PnL result here would be a hypothesis for forward shadowing, not proof "
            "of stable edge.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(
    db_path: Path,
    output: Path,
    *,
    snapshots_path: Path | None = None,
    settlements_path: Path | None = None,
) -> dict[str, Any]:
    protocol = _load_protocol()
    snapshots, settlements = _load_data(db_path, snapshots_path, settlements_path)
    frame, counts = build_checkpoint_frame(snapshots, settlements, protocol)
    if len(frame) < int(protocol["validation"]["minimum_training_rounds"]):
        raise RuntimeError(
            f"insufficient fixed-checkpoint rounds: {len(frame)}"
        )
    predictions, folds = expanding_day_predictions(frame, protocol)
    if predictions.empty:
        raise RuntimeError("no valid chronological day-block folds")
    metrics = probability_metrics(predictions)
    action_summary, action_detail = action_metrics(predictions, protocol)
    gates = gate_status(
        frame, predictions, metrics, action_summary, protocol
    )
    output.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output / "probability_metrics.csv", index=False)
    action_summary.to_csv(output / "action_metrics.csv", index=False)
    predictions.to_parquet(output / "oos_predictions.parquet", index=False)
    action_detail.to_parquet(output / "oos_actions.parquet", index=False)
    (output / "gate_status.json").write_text(
        json.dumps(gates, indent=2, default=str), encoding="utf-8"
    )
    report = render_report(
        frame, predictions, folds, metrics, action_summary, gates, counts
    )
    (output / "result.md").write_text(report, encoding="utf-8")
    code_commit, code_dirty = _code_identity()
    manifest = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": _sha256(PROTOCOL_PATH),
        "script_sha256": _sha256(Path(__file__)),
        "code_commit": code_commit,
        "code_dirty": code_dirty,
        "checkpoint_dataset_sha256": _frame_sha256(frame),
        "database_path": str(db_path) if snapshots_path is None else None,
        "database_bytes": db_path.stat().st_size if snapshots_path is None else None,
        "snapshots_path": str(snapshots_path) if snapshots_path is not None else None,
        "settlements_path": str(settlements_path) if settlements_path is not None else None,
        "snapshots_sha256": _sha256(snapshots_path) if snapshots_path is not None else None,
        "settlements_sha256": _sha256(settlements_path) if settlements_path is not None else None,
        "counts": counts,
        "folds": folds,
        "output_files": sorted(path.name for path in output.iterdir()),
        "serving_enabled": False,
        "paper_enabled": False,
        "live_enabled": False,
    }
    (output / "trial_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    print(report, flush=True)
    print(f"[done] output={output}", flush=True)
    return {
        "frame": frame,
        "predictions": predictions,
        "probability_metrics": metrics,
        "action_metrics": action_summary,
        "gates": gates,
        "manifest": manifest,
    }


def selftest() -> None:
    rng = np.random.default_rng(20260731)
    rows = 800
    feature = rng.normal(size=(rows, 1))
    market_latent = rng.normal(scale=0.8, size=rows)
    market_probability = _sigmoid(market_latent)
    true_probability = _sigmoid(_logit(market_probability) + 0.9 * feature[:, 0])
    target = rng.binomial(1, true_probability)
    train = np.arange(0, 550)
    test = np.arange(550, rows)
    model = OffsetLogit(4.0).fit(
        feature[train],
        target[train],
        offset=_logit(market_probability[train]),
    )
    prediction = model.predict(
        feature[test], offset=_logit(market_probability[test])
    )
    baseline = brier_score_loss(target[test], market_probability[test])
    residual = brier_score_loss(target[test], prediction)
    assert residual < baseline, (baseline, residual)
    fee = _taker_fee(np.array([0.0, 0.5, 1.0]))
    assert np.allclose(fee, [0.0, 0.0175, 0.0])
    assert np.isfinite(prediction).all()
    assert ((prediction > 0.0) & (prediction < 1.0)).all()
    print("POLY_MARKET_PRIOR_RESIDUAL_V1 selftest: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--snapshots", type=Path)
    parser.add_argument("--settlements", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    snapshots_path = args.snapshots.resolve() if args.snapshots else None
    settlements_path = args.settlements.resolve() if args.settlements else None
    run(
        args.db.resolve(),
        args.output.resolve(),
        snapshots_path=snapshots_path,
        settlements_path=settlements_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
