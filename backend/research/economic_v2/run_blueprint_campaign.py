#!/usr/bin/env python
"""Run the frozen Economic V2 blueprint experiments.

The campaign is research-only:
  E1 decomposes existing independent LONG/SHORT scores into common magnitude and
     residual direction factors across two historical eras.
  E2 tests whether a Polymarket probability residual model improves on the
     contemporaneous market midpoint and survives executable-ask delay stress.

No artifact is written to production model directories and no trading process is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from polymarket_fee import polymarket_taker_fee_per_share  # noqa: E402

DATA = ROOT / "data"
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_OUTPUT = DATA / "research" / "economic_v2"
RECENT_FACTOR_PATH = (
    DATA
    / "research"
    / "trade_policy_heads_120d"
    / "20260727T192227Z"
    / "oof_predictions.parquet"
)
OLDER_FACTOR_PATH = (
    DATA
    / "research"
    / "economic_policy_campaign_180d"
    / "20260727T201350Z"
    / "locked_test_predictions.parquet"
)
EXECUTION_DB = DATA / "execution_layer.duckdb"

CHECKPOINTS = {5: (240, 180, 120, 60, 30, 15), 15: (600, 300, 120, 60, 30, 15)}
CHECKPOINT_TOLERANCE_S = 6.0
EDGE_BUFFER = 0.03
DELAYS_S = (0, 1, 2, 5, 10)
SLIPPAGE_STRESS = (0.0, 0.01)
POSITION_SIZE = 1.0
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
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
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
    p = np.asarray(probability, dtype=float)
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else math.nan


def probability_metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    y = np.asarray(target, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return {
        "n": len(y),
        "positive_rate": float(y.mean()),
        "auc": safe_auc(y, p),
        "average_precision": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "accuracy": float(accuracy_score(y, p >= 0.5)),
    }


def spearman(left: pd.Series, right: pd.Series) -> float:
    return float(pd.Series(left).corr(pd.Series(right), method="spearman"))


def logit(probability: pd.Series) -> np.ndarray:
    p = np.clip(pd.to_numeric(probability, errors="coerce").to_numpy(float), 1e-6, 1 - 1e-6)
    return np.log(p / (1.0 - p))


def residualize(values: np.ndarray, controls: pd.DataFrame) -> np.ndarray:
    matrix = controls.replace([np.inf, -np.inf], np.nan)
    pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("ridge", Ridge(alpha=10.0)),
        ]
    )
    fitted = pipeline.fit(matrix, values).predict(matrix)
    return np.asarray(values, dtype=float) - np.asarray(fitted, dtype=float)


def factor_experiment(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sources = {
        "recent_120d_oof": RECENT_FACTOR_PATH,
        "older_180d_locked": OLDER_FACTOR_PATH,
    }
    summary_rows: list[dict[str, Any]] = []
    bucket_rows: list[dict[str, Any]] = []
    block_rows: list[dict[str, Any]] = []
    for era, path in sources.items():
        if not path.exists():
            raise FileNotFoundError(path)
        frame = pd.read_parquet(path)
        required = {
            "timestamp_ms",
            "horizon",
            "gross_return_bps",
            "long_net_bps",
            "short_net_bps",
            "p_long_ensemble",
            "p_short_ensemble",
            "realized_vol_15m_bps",
            "realized_vol_60m_bps",
            "volume_z_60m",
            "trade_count_z_60m",
        }
        missing = sorted(required - set(frame.columns))
        if missing:
            raise ValueError(f"{era} missing factor columns: {missing}")
        frame["timestamp"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True)
        for horizon, group in frame.groupby("horizon", sort=True):
            data = group.sort_values("timestamp_ms").copy()
            z_long = logit(data["p_long_ensemble"])
            z_short = logit(data["p_short_ensemble"])
            data["magnitude_factor"] = (z_long + z_short) / 2.0
            data["direction_factor"] = (z_long - z_short) / 2.0
            controls = data[
                [
                    "magnitude_factor",
                    "realized_vol_15m_bps",
                    "realized_vol_60m_bps",
                    "volume_z_60m",
                    "trade_count_z_60m",
                ]
            ]
            data["direction_residual"] = residualize(
                data["direction_factor"].to_numpy(float), controls
            )
            data["selected_net_bps"] = np.where(
                data["direction_residual"] >= 0,
                data["long_net_bps"],
                data["short_net_bps"],
            )
            data["abs_direction"] = np.abs(data["direction_residual"])
            data["direction_bucket"] = pd.qcut(
                data["direction_residual"], 10, labels=False, duplicates="drop"
            )
            data["confidence_bucket"] = pd.qcut(
                data["abs_direction"], 10, labels=False, duplicates="drop"
            )
            direction_bucket_means = (
                data.groupby("direction_bucket", observed=True)["gross_return_bps"]
                .mean()
                .sort_index()
            )
            direction_monotonicity = spearman(
                pd.Series(direction_bucket_means.index, dtype=float),
                direction_bucket_means.reset_index(drop=True),
            )
            high_confidence = data[
                data["confidence_bucket"] == data["confidence_bucket"].max()
            ]
            summary_rows.append(
                {
                    "experiment_id": "E1_LONG_SHORT_FACTOR_DECOMPOSITION",
                    "era": era,
                    "horizon": int(horizon),
                    "n": len(data),
                    "long_short_probability_correlation": float(
                        data["p_long_ensemble"].corr(data["p_short_ensemble"])
                    ),
                    "magnitude_abs_return_spearman": spearman(
                        data["magnitude_factor"], data["gross_return_bps"].abs()
                    ),
                    "direction_return_spearman": spearman(
                        data["direction_factor"], data["gross_return_bps"]
                    ),
                    "residual_direction_return_spearman": spearman(
                        data["direction_residual"], data["gross_return_bps"]
                    ),
                    "direction_bucket_monotonicity": direction_monotonicity,
                    "top_decile_trades": len(high_confidence),
                    "top_decile_selected_net_bps": float(
                        high_confidence["selected_net_bps"].mean()
                    ),
                    "top_decile_win_rate": float(
                        (high_confidence["selected_net_bps"] > 0).mean()
                    ),
                }
            )
            for bucket_id, bucket_data in data.groupby("direction_bucket", observed=True):
                bucket_rows.append(
                    {
                        "era": era,
                        "horizon": int(horizon),
                        "bucket_type": "signed_direction",
                        "bucket": int(bucket_id),
                        "n": len(bucket_data),
                        "mean_factor": float(bucket_data["direction_residual"].mean()),
                        "mean_gross_return_bps": float(bucket_data["gross_return_bps"].mean()),
                        "mean_selected_net_bps": float(bucket_data["selected_net_bps"].mean()),
                    }
                )
            for bucket_id, bucket_data in data.groupby("confidence_bucket", observed=True):
                bucket_rows.append(
                    {
                        "era": era,
                        "horizon": int(horizon),
                        "bucket_type": "absolute_direction",
                        "bucket": int(bucket_id),
                        "n": len(bucket_data),
                        "mean_factor": float(bucket_data["abs_direction"].mean()),
                        "mean_gross_return_bps": float(bucket_data["gross_return_bps"].mean()),
                        "mean_selected_net_bps": float(bucket_data["selected_net_bps"].mean()),
                    }
                )
            data["block"] = (
                data["timestamp"].dt.tz_convert(None).dt.to_period("W").astype(str)
            )
            for block, block_data in data.groupby("block"):
                block_rows.append(
                    {
                        "era": era,
                        "horizon": int(horizon),
                        "block": block,
                        "n": len(block_data),
                        "residual_direction_ic": spearman(
                            block_data["direction_residual"],
                            block_data["gross_return_bps"],
                        ),
                        "selected_net_bps": float(block_data["selected_net_bps"].mean()),
                    }
                )
            # The transformation itself must be mirror symmetric by construction.
            mirrored_direction = (z_short - z_long) / 2.0
            if not np.allclose(mirrored_direction, -data["direction_factor"], atol=1e-12):
                raise AssertionError("LONG/SHORT factor decomposition is not mirror symmetric")
            log(
                f"[E1] era={era} h={int(horizon)}m corr="
                f"{summary_rows[-1]['long_short_probability_correlation']:.3f} "
                f"dirIC={summary_rows[-1]['residual_direction_return_spearman']:.4f} "
                f"topNet={summary_rows[-1]['top_decile_selected_net_bps']:.2f}bps"
            )
    summary = pd.DataFrame(summary_rows)
    buckets = pd.DataFrame(bucket_rows)
    blocks = pd.DataFrame(block_rows)
    summary.to_csv(run_dir / "e1_factor_summary.csv", index=False)
    buckets.to_csv(run_dir / "e1_factor_buckets.csv", index=False)
    blocks.to_csv(run_dir / "e1_factor_blocks.csv", index=False)
    return summary, buckets, blocks


def load_polymarket_snapshots() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not EXECUTION_DB.exists():
        raise FileNotFoundError(EXECUTION_DB)
    connection = duckdb.connect(str(EXECUTION_DB), read_only=True)
    query = """
        SELECT
            s.ts, s.slug, CAST(s.horizon AS INTEGER) AS horizon, s.anchor_ts,
            s.seconds_left, s.seconds_elapsed, s.anchor_price, s.btc_price,
            s.distance_pct, s.distance_bps, s.current_side, s.vol_60s_pct,
            s.p_hold_cur, s.p_hold_up, s.p_hold_down,
            s.up_bid, s.up_ask, s.up_mid, s.up_spread, s.up_top_ask_size,
            s.up_d1, s.up_d2, s.up_d5,
            s.down_bid, s.down_ask, s.down_mid, s.down_spread, s.down_top_ask_size,
            s.down_d1, s.down_d2, s.down_d5,
            CAST(r.up_win AS INTEGER) AS up_win, r.settled_side
        FROM pm_round_snapshots s
        JOIN pm_round_settlements r USING (slug)
        WHERE s.seconds_left BETWEEN 0 AND s.horizon * 60
          AND s.up_bid BETWEEN 0.0 AND 0.99
          AND s.down_bid BETWEEN 0.0 AND 0.99
          AND s.up_ask BETWEEN 0.01 AND 0.99
          AND s.down_ask BETWEEN 0.01 AND 0.99
          AND s.up_mid BETWEEN 0.01 AND 0.99
          AND s.down_mid BETWEEN 0.01 AND 0.99
          AND s.up_top_ask_size >= 1.0
          AND s.down_top_ask_size >= 1.0
    """
    raw = connection.execute(query).fetchdf()
    connection.close()
    if raw.empty:
        raise ValueError("no settled executable Polymarket snapshots")
    checkpoint_frames: list[pd.DataFrame] = []
    for horizon, checkpoints in CHECKPOINTS.items():
        subset = raw[raw["horizon"] == horizon].copy()
        if subset.empty:
            continue
        distance = np.abs(
            subset["seconds_left"].to_numpy(float)[:, None]
            - np.asarray(checkpoints, dtype=float)[None, :]
        )
        nearest = np.argmin(distance, axis=1)
        subset["checkpoint"] = np.asarray(checkpoints, dtype=int)[nearest]
        subset["checkpoint_gap_s"] = distance[np.arange(len(subset)), nearest]
        subset = subset[subset["checkpoint_gap_s"] <= CHECKPOINT_TOLERANCE_S]
        subset = (
            subset.sort_values(["slug", "checkpoint", "checkpoint_gap_s", "ts"])
            .drop_duplicates(["slug", "checkpoint"], keep="first")
            .reset_index(drop=True)
        )
        checkpoint_frames.append(subset)
    checkpoints = pd.concat(checkpoint_frames, ignore_index=True)
    if bool(checkpoints.duplicated(["slug", "checkpoint"]).any()):
        raise AssertionError("duplicate market/checkpoint observations")
    return raw.sort_values(["slug", "ts"]).reset_index(drop=True), checkpoints


def assign_grouped_splits(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["split"] = ""
    for horizon, group in result.groupby("horizon"):
        markets = (
            group[["slug", "anchor_ts"]]
            .drop_duplicates("slug")
            .sort_values(["anchor_ts", "slug"])
            .reset_index(drop=True)
        )
        count = len(markets)
        train_end = max(1, int(count * 0.60))
        calibration_end = max(train_end + 1, int(count * 0.80))
        calibration_end = min(calibration_end, count - 1)
        mapping: dict[str, str] = {}
        for index, slug in enumerate(markets["slug"]):
            mapping[slug] = (
                "train"
                if index < train_end
                else "calibration"
                if index < calibration_end
                else "test"
            )
        mask = result["horizon"] == horizon
        result.loc[mask, "split"] = result.loc[mask, "slug"].map(mapping)
    for split in ("train", "calibration", "test"):
        if not bool((result["split"] == split).any()):
            raise ValueError(f"empty Polymarket split: {split}")
    leakage = result.groupby("slug")["split"].nunique()
    if bool((leakage != 1).any()):
        raise AssertionError("market crosses Polymarket split")
    return result


def add_polymarket_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    data = frame.copy()
    midpoint_sum = data["up_mid"] + data["down_mid"]
    data["market_p_up"] = np.clip(data["up_mid"] / midpoint_sum, 0.01, 0.99)
    data["market_logit_up"] = np.log(data["market_p_up"] / (1.0 - data["market_p_up"]))
    remaining_vol_bps = (
        data["vol_60s_pct"].abs() * 10_000.0 * np.sqrt(data["seconds_left"].clip(lower=1) / 60.0)
    )
    data["z_distance"] = data["distance_bps"] / np.maximum(remaining_vol_bps, 0.25)
    data["seconds_ratio"] = data["seconds_left"] / (data["horizon"] * 60.0)
    data["quote_overround"] = data["up_ask"] + data["down_ask"] - 1.0
    data["mid_overround"] = midpoint_sum - 1.0
    data["top_ask_imbalance"] = (
        data["down_top_ask_size"] - data["up_top_ask_size"]
    ) / np.maximum(data["down_top_ask_size"] + data["up_top_ask_size"], 1e-9)
    for depth in ("d1", "d2", "d5"):
        data[f"depth_imbalance_{depth}"] = (
            data[f"down_{depth}"] - data[f"up_{depth}"]
        ) / np.maximum(data[f"down_{depth}"] + data[f"up_{depth}"], 1e-9)
        data[f"log_up_{depth}"] = np.log1p(data[f"up_{depth}"].clip(lower=0))
        data[f"log_down_{depth}"] = np.log1p(data[f"down_{depth}"].clip(lower=0))
    data["log_up_top_ask_size"] = np.log1p(data["up_top_ask_size"].clip(lower=0))
    data["log_down_top_ask_size"] = np.log1p(data["down_top_ask_size"].clip(lower=0))
    features = [
        "market_p_up",
        "market_logit_up",
        "horizon",
        "checkpoint",
        "seconds_ratio",
        "distance_bps",
        "z_distance",
        "vol_60s_pct",
        "current_side",
        "p_hold_cur",
        "p_hold_up",
        "p_hold_down",
        "up_spread",
        "down_spread",
        "quote_overround",
        "mid_overround",
        "top_ask_imbalance",
        "depth_imbalance_d1",
        "depth_imbalance_d2",
        "depth_imbalance_d5",
        "log_up_top_ask_size",
        "log_down_top_ask_size",
        "log_up_d1",
        "log_down_d1",
        "log_up_d2",
        "log_down_d2",
        "log_up_d5",
        "log_down_d5",
    ]
    return data, features


def fit_residual_models(
    data: pd.DataFrame, features: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    train = data[data["split"] == "train"].copy()
    calibration = data[data["split"] == "calibration"].copy()
    test = data[data["split"] == "test"].copy()
    y_train = train["up_win"].to_numpy(int)
    y_calibration = calibration["up_win"].to_numpy(int)
    imputer = SimpleImputer(strategy="median")
    x_train = imputer.fit_transform(train[features])
    x_calibration = imputer.transform(calibration[features])
    x_test = imputer.transform(test[features])

    baseline_calibrator = IsotonicRegression(out_of_bounds="clip")
    baseline_calibrator.fit(calibration["market_p_up"], y_calibration)
    test["p_market_raw"] = test["market_p_up"]
    test["p_market_calibrated"] = baseline_calibrator.predict(test["market_p_up"])

    models: dict[str, Any] = {
        "ridge_residual": Pipeline(
            [("scale", StandardScaler()), ("ridge", Ridge(alpha=20.0))]
        ),
        "histgb_residual": HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_iter=120,
            max_depth=3,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=RANDOM_SEED,
        ),
    }
    residual_target = y_train - train["market_p_up"].to_numpy(float)
    predictions: list[np.ndarray] = []
    for name, model in models.items():
        model.fit(x_train, residual_target)
        calibration_probability = np.clip(
            calibration["market_p_up"].to_numpy(float) + model.predict(x_calibration),
            0.001,
            0.999,
        )
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(calibration_probability, y_calibration)
        test_raw = np.clip(
            test["market_p_up"].to_numpy(float) + model.predict(x_test), 0.001, 0.999
        )
        probability = np.asarray(calibrator.predict(test_raw), dtype=float)
        test[f"p_{name}"] = probability
        predictions.append(probability)
    test["p_residual_ensemble"] = np.column_stack(predictions).mean(axis=1)

    # Fixed negative control: shuffled residual labels. It receives the same features and
    # market baseline but no valid incremental target relationship.
    generator = np.random.default_rng(RANDOM_SEED)
    shuffled = generator.permutation(residual_target)
    null_model = HistGradientBoostingRegressor(
        learning_rate=0.04,
        max_iter=120,
        max_depth=3,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=RANDOM_SEED + 1,
    )
    null_model.fit(x_train, shuffled)
    test["p_shuffled_residual"] = np.clip(
        test["market_p_up"].to_numpy(float) + null_model.predict(x_test), 0.001, 0.999
    )

    metric_rows: list[dict[str, Any]] = []
    probability_columns = {
        "market_raw": "p_market_raw",
        "market_calibrated": "p_market_calibrated",
        "ridge_residual": "p_ridge_residual",
        "histgb_residual": "p_histgb_residual",
        "residual_ensemble": "p_residual_ensemble",
        "shuffled_residual_control": "p_shuffled_residual",
    }
    for scope, scoped in [("all", test), *[(f"{horizon}m", group) for horizon, group in test.groupby("horizon")]]:
        for model_name, column in probability_columns.items():
            metric_rows.append(
                {
                    "experiment_id": "E2_POLYMARKET_MARKET_RESIDUAL",
                    "scope": scope,
                    "model": model_name,
                    **probability_metrics(scoped["up_win"], scoped[column]),
                }
            )
    metrics = pd.DataFrame(metric_rows)

    # Adversarial validation is grouped by market: train and locked-test markets cannot be
    # split between fit/evaluation rows.
    adversarial_pool = data[data["split"].isin(["train", "test"])].copy()
    adversarial_pool["is_locked_test"] = (adversarial_pool["split"] == "test").astype(int)
    market_ids = adversarial_pool[["slug", "is_locked_test"]].drop_duplicates()
    fit_slugs: set[str] = set()
    score_slugs: set[str] = set()
    rng = np.random.default_rng(RANDOM_SEED)
    for label, group in market_ids.groupby("is_locked_test"):
        slugs = group["slug"].to_numpy()
        rng.shuffle(slugs)
        cut = max(1, len(slugs) // 2)
        fit_slugs.update(slugs[:cut])
        score_slugs.update(slugs[cut:])
    adversarial_train = adversarial_pool[adversarial_pool["slug"].isin(fit_slugs)]
    adversarial_test = adversarial_pool[adversarial_pool["slug"].isin(score_slugs)]
    adv_model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(max_iter=500, C=0.2, random_state=RANDOM_SEED)),
        ]
    )
    adv_model.fit(adversarial_train[features], adversarial_train["is_locked_test"])
    adv_probability = adv_model.predict_proba(adversarial_test[features])[:, 1]
    diagnostics = {
        "train_markets": int(train["slug"].nunique()),
        "calibration_markets": int(calibration["slug"].nunique()),
        "locked_test_markets": int(test["slug"].nunique()),
        "train_rows": len(train),
        "calibration_rows": len(calibration),
        "locked_test_rows": len(test),
        "adversarial_auc": safe_auc(
            adversarial_test["is_locked_test"].to_numpy(int), adv_probability
        ),
        "features": features,
    }
    return test, metrics, diagnostics


def first_signals(test: pd.DataFrame, probability_column: str) -> pd.DataFrame:
    data = test.copy()
    data["p_up"] = np.clip(data[probability_column], 0.0, 1.0)
    data["p_down"] = 1.0 - data["p_up"]
    data["fee_up"] = data["up_ask"].map(polymarket_taker_fee_per_share)
    data["fee_down"] = data["down_ask"].map(polymarket_taker_fee_per_share)
    data["edge_up"] = data["p_up"] - data["up_ask"] - data["fee_up"] - EDGE_BUFFER
    data["edge_down"] = (
        data["p_down"] - data["down_ask"] - data["fee_down"] - EDGE_BUFFER
    )
    data["action"] = np.where(
        (data["edge_up"] > 0) & (data["edge_up"] >= data["edge_down"]),
        "UP",
        np.where(data["edge_down"] > 0, "DOWN", "SKIP"),
    )
    eligible = data[data["action"] != "SKIP"].sort_values(["slug", "ts"])
    return eligible.drop_duplicates("slug", keep="first").reset_index(drop=True)


def delayed_entry(
    signal: pd.Series, raw_by_slug: dict[str, pd.DataFrame], delay_s: int
) -> pd.Series | None:
    path = raw_by_slug.get(str(signal["slug"]))
    if path is None or path.empty:
        return None
    eligible = path[path["ts"] >= float(signal["ts"]) + delay_s]
    if eligible.empty:
        return None
    row = eligible.iloc[0]
    if float(row["seconds_left"]) < 0:
        return None
    return row


def economic_metrics(pnl: list[float]) -> dict[str, Any]:
    values = np.asarray(pnl, dtype=float)
    if not len(values):
        return {
            "trades": 0,
            "mean_pnl_per_share": math.nan,
            "total_pnl": 0.0,
            "win_rate": math.nan,
            "profit_factor": math.nan,
            "bootstrap_lower_95": math.nan,
        }
    positive = values[values > 0].sum()
    negative = -values[values < 0].sum()
    generator = np.random.default_rng(RANDOM_SEED)
    bootstrap = np.empty(2_000, dtype=float)
    for index in range(len(bootstrap)):
        bootstrap[index] = generator.choice(values, size=len(values), replace=True).mean()
    return {
        "trades": len(values),
        "mean_pnl_per_share": float(values.mean()),
        "total_pnl": float(values.sum()),
        "win_rate": float((values > 0).mean()),
        "profit_factor": float(positive / negative) if negative > 0 else math.inf,
        "bootstrap_lower_95": float(np.quantile(bootstrap, 0.025)),
    }


def execution_stress(
    test: pd.DataFrame, raw: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw_by_slug = {
        slug: group.sort_values("ts").reset_index(drop=True)
        for slug, group in raw.groupby("slug")
    }
    signal_frames: list[pd.DataFrame] = []
    result_rows: list[dict[str, Any]] = []
    policies = {
        "market_calibrated": "p_market_calibrated",
        "residual_ensemble": "p_residual_ensemble",
    }
    for policy_name, probability_column in policies.items():
        signals = first_signals(test, probability_column)
        signals["policy"] = policy_name
        signal_frames.append(signals)
        for delay in DELAYS_S:
            for slippage in SLIPPAGE_STRESS:
                for scope, scoped in [
                    ("all", signals),
                    *[(f"{horizon}m", group) for horizon, group in signals.groupby("horizon")],
                ]:
                    pnl: list[float] = []
                    for _, signal in scoped.iterrows():
                        entry = delayed_entry(signal, raw_by_slug, delay)
                        if entry is None:
                            continue
                        side = str(signal["action"])
                        ask = float(entry["up_ask"] if side == "UP" else entry["down_ask"])
                        size = float(
                            entry["up_top_ask_size"]
                            if side == "UP"
                            else entry["down_top_ask_size"]
                        )
                        if size < POSITION_SIZE or not 0.01 <= ask <= 0.99:
                            continue
                        stressed_ask = min(0.99, ask + slippage)
                        fee = polymarket_taker_fee_per_share(stressed_ask)
                        won = bool(signal["up_win"]) if side == "UP" else not bool(signal["up_win"])
                        payout = 1.0 if won else 0.0
                        pnl.append(payout - stressed_ask - fee)
                    result_rows.append(
                        {
                            "experiment_id": "E2_POLYMARKET_MARKET_RESIDUAL",
                            "policy": policy_name,
                            "scope": scope,
                            "delay_seconds": delay,
                            "slippage_stress": slippage,
                            **economic_metrics(pnl),
                        }
                    )
    signals = (
        pd.concat(signal_frames, ignore_index=True)
        if signal_frames
        else pd.DataFrame()
    )
    return pd.DataFrame(result_rows), signals


def polymarket_residual_experiment(
    run_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    raw, checkpoints = load_polymarket_snapshots()
    checkpoints = assign_grouped_splits(checkpoints)
    data, features = add_polymarket_features(checkpoints)
    test, probability_metrics_frame, diagnostics = fit_residual_models(data, features)
    execution, signals = execution_stress(test, raw)
    data.to_parquet(run_dir / "e2_checkpoint_dataset.parquet", index=False)
    test.to_parquet(run_dir / "e2_locked_predictions.parquet", index=False)
    probability_metrics_frame.to_csv(run_dir / "e2_probability_metrics.csv", index=False)
    execution.to_csv(run_dir / "e2_execution_stress.csv", index=False)
    signals.to_csv(run_dir / "e2_signals.csv", index=False)
    (run_dir / "e2_diagnostics.json").write_text(
        json.dumps(json_safe(diagnostics), indent=2), encoding="utf-8"
    )
    all_metrics = probability_metrics_frame[probability_metrics_frame["scope"] == "all"]
    baseline = all_metrics[all_metrics["model"] == "market_calibrated"].iloc[0]
    residual = all_metrics[all_metrics["model"] == "residual_ensemble"].iloc[0]
    log(
        f"[E2] markets train/cal/test={diagnostics['train_markets']}/"
        f"{diagnostics['calibration_markets']}/{diagnostics['locked_test_markets']} "
        f"Brier market={baseline.brier:.4f} residual={residual.brier:.4f} "
        f"adversarialAUC={diagnostics['adversarial_auc']:.3f}"
    )
    return probability_metrics_frame, execution, signals, diagnostics


def write_results(
    run_dir: Path,
    factor: pd.DataFrame,
    factor_blocks: pd.DataFrame,
    probabilities: pd.DataFrame,
    execution: pd.DataFrame,
    diagnostics: dict[str, Any],
    elapsed: float,
) -> None:
    lines = [
        "# Economic V2 Blueprint Campaign Results",
        "",
        f"Run: `{run_dir.name}`",
        "",
        "Status: **COMPLETE - RESEARCH ONLY**",
        "",
        "## E1: LONG/SHORT Factor Decomposition",
        "",
        "| Era | Horizon | LONG/SHORT corr | Magnitude IC | Direction IC | Bucket monotonicity | Top-decile net bps |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in factor.iterrows():
        lines.append(
            f"| {row.era} | {int(row.horizon)}m | "
            f"{row.long_short_probability_correlation:.3f} | "
            f"{row.magnitude_abs_return_spearman:.4f} | "
            f"{row.residual_direction_return_spearman:.4f} | "
            f"{row.direction_bucket_monotonicity:.4f} | "
            f"{row.top_decile_selected_net_bps:.2f} |"
        )
    block_pass = (
        factor_blocks.groupby(["era", "horizon"])["residual_direction_ic"]
        .apply(lambda values: float((values > 0).mean()))
        .reset_index(name="positive_block_fraction")
    )
    lines.extend(
        [
            "",
            "Positive direction-IC block fractions:",
            "",
            "| Era | Horizon | Positive blocks |",
            "|---|---:|---:|",
        ]
    )
    for _, row in block_pass.iterrows():
        lines.append(
            f"| {row.era} | {int(row.horizon)}m | {row.positive_block_fraction:.1%} |"
        )
    lines.extend(
        [
            "",
            "## E2: Polymarket Market-Price Residual",
            "",
            f"Grouped markets: train `{diagnostics['train_markets']}`, calibration "
            f"`{diagnostics['calibration_markets']}`, locked test `{diagnostics['locked_test_markets']}`.",
            "",
            f"Train-versus-test adversarial AUC: `{diagnostics['adversarial_auc']:.4f}`.",
            "",
            "| Scope | Model | n | AUC | Brier | Log loss |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    keep_models = {
        "market_raw",
        "market_calibrated",
        "residual_ensemble",
        "shuffled_residual_control",
    }
    for _, row in probabilities[probabilities["model"].isin(keep_models)].iterrows():
        lines.append(
            f"| {row.scope} | {row.model} | {int(row.n)} | {row.auc:.4f} | "
            f"{row.brier:.4f} | {row.log_loss:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Executable-Ask Settlement Stress",
            "",
            "The decision gate subtracts actual ask, the canonical taker fee, and a frozen 3c",
            "safety buffer. PnL uses the first available ask after the stated delay and allows",
            "only one trade per market.",
            "",
            "| Policy | Scope | Delay | Slippage | Trades | Mean PnL/share | PF | Bootstrap lower 95% |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in execution.iterrows():
        if row.scope == "all":
            lines.append(
                f"| {row.policy} | {row.scope} | {int(row.delay_seconds)}s | "
                f"{row.slippage_stress:.2f} | {int(row.trades)} | "
                f"{row.mean_pnl_per_share:.4f} | {row.profit_factor:.3f} | "
                f"{row.bootstrap_lower_95:.4f} |"
            )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "No code in this campaign can promote itself. The frozen pass requirements include",
            "at least 200 locked-test trades, positive delayed/stressed PnL, and a positive",
            "bootstrap lower bound. Failing any one requirement keeps the result research-only.",
            "",
            f"Runtime: `{elapsed:.1f}` seconds.",
            "",
        ]
    )
    (run_dir / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def selftest() -> int:
    probability = pd.Series([0.2, 0.4, 0.6, 0.8])
    z = logit(probability)
    direction = (z - z[::-1]) / 2.0
    mirrored = (z[::-1] - z) / 2.0
    if not np.allclose(direction, -mirrored):
        raise AssertionError("factor mirror test failed")
    if polymarket_taker_fee_per_share(0.5) != 0.0175:
        raise AssertionError("canonical Polymarket fee mismatch")
    sample = pd.DataFrame(
        {
            "slug": [f"5m-{value}" for value in "abcdef"]
            + [f"15m-{value}" for value in "abcdef"],
            "anchor_ts": list(range(6)) * 2,
            "horizon": [5] * 6 + [15] * 6,
        }
    )
    split = assign_grouped_splits(sample)
    if bool((split.groupby("slug")["split"].nunique() != 1).any()):
        raise AssertionError("group split leakage")
    metrics = probability_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.3, 0.7, 0.9]))
    if metrics["auc"] != 1.0:
        raise AssertionError("probability metric self-test failed")
    print("SELFTEST PASS")
    return 0


def run(output_root: Path) -> Path:
    global _LOG_PATH
    started = time.perf_counter()
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(PROTOCOL_PATH)
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    run_dir = output_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    _LOG_PATH = run_dir / "run.log"
    protocol_snapshot = run_dir / "frozen_protocol_snapshot.json"
    script_snapshot = run_dir / "campaign_script_snapshot.py"
    shutil.copy2(PROTOCOL_PATH, protocol_snapshot)
    shutil.copy2(Path(__file__).resolve(), script_snapshot)
    log(
        f"[start] protocol={protocol['protocol_id']} "
        f"hash={sha256_file(PROTOCOL_PATH)}"
    )
    factor, _, factor_blocks = factor_experiment(run_dir)
    probabilities, execution, _, diagnostics = polymarket_residual_experiment(run_dir)
    elapsed = time.perf_counter() - started
    write_results(
        run_dir,
        factor,
        factor_blocks,
        probabilities,
        execution,
        diagnostics,
        elapsed,
    )
    manifest = {
        "run_id": run_dir.name,
        "protocol": str(protocol_snapshot),
        "protocol_sha256": sha256_file(protocol_snapshot),
        "script": str(script_snapshot),
        "script_sha256": sha256_file(script_snapshot),
        "inputs": {
            "recent_factor_predictions": str(RECENT_FACTOR_PATH),
            "older_factor_predictions": str(OLDER_FACTOR_PATH),
            "execution_db": str(EXECUTION_DB),
        },
        "production_artifacts_changed": False,
        "eligible_for_production": False,
        "elapsed_seconds": elapsed,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(json_safe(manifest), indent=2), encoding="utf-8"
    )
    log(f"[done] output={run_dir} elapsed={elapsed:.1f}s")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    run(args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
