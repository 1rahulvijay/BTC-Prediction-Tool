#!/usr/bin/env python
"""Frozen 180-day economic specialist and ACT/SKIP campaign.

This is an isolated historical research lane. It trains direct post-cost LONG
and SHORT heads, evaluates a finite policy catalog on a selection period, and
scores exactly one selected policy per horizon on a locked historical test.
It also gives one causal dynamic-exit challenger a paired comparison with HOLD.

Nothing in this module is imported by production serving code and it never
writes to ``data/saved_models``.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from train_120d_conditional_ev_pipeline import (
    json_safe,
    quantile_factory,
    quantile_metrics,
    regression_metrics,
    regressor_factory,
)
from train_120d_trade_policy_heads import (
    META_CONTEXT_CANDIDATES,
    binary_metrics,
    build_causal_features,
    build_side_labels,
    economic_metrics,
    impute_train_test,
    positive_probability,
    select_features,
)
from train_120d_trade_policy_heads import (
    model_factory as classifier_factory,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_MATRIX = DATA_DIR / "research_matrix_1m.parquet"
DEFAULT_OUTPUT_ROOT = DATA_DIR / "research" / "economic_policy_campaign_180d"

DAY_MS = 86_400_000
MINUTE_MS = 60_000
CLASSIFIER_FAMILIES = (
    "logreg",
    "histgb",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
)
REGRESSOR_FAMILIES = (
    "ridge",
    "histgb",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
)
QUANTILE_FAMILIES = ("histgb", "lightgbm", "catboost")
META_FAMILIES = ("logreg", "histgb")
PROBABILITY_THRESHOLDS = (0.30, 0.40, 0.50, 0.60)
PROBABILITY_GAPS = (0.00, 0.05, 0.10)
MEAN_THRESHOLDS_BPS = (0.0, 2.0, 4.0, 6.0, 8.0)
Q20_THRESHOLDS_BPS = (0.0, 2.0, 4.0)
ACT_THRESHOLDS = (0.50, 0.60, 0.70)
SIDE_MODES = ("BOTH", "LONG", "SHORT")
DYNAMIC_EXIT_THRESHOLD_BPS = -2.0
DYNAMIC_CHECKPOINTS = {5: (1, 2, 3, 4), 15: (3, 6, 9, 12)}

_LOG_FILE: Path | None = None


@dataclass(frozen=True)
class Config:
    matrix: str
    horizons: list[int]
    window_days: int
    base_train_days: int
    meta_train_days: int
    selection_days: int
    locked_test_days: int
    fee_bps_per_side: float
    slippage_bps_per_side: float
    max_features: int
    max_train_rows: int
    threads: int
    classifier_families: list[str]
    regressor_families: list[str]
    quantile_families: list[str]
    meta_families: list[str]
    run_name: str

    @property
    def cost_bps(self) -> float:
        return 2.0 * (self.fee_bps_per_side + self.slippage_bps_per_side)


@dataclass(frozen=True)
class Boundaries:
    start_ms: int
    train_end_ms: int
    meta_end_ms: int
    selection_end_ms: int
    test_end_ms: int


@dataclass(frozen=True)
class PolicySpec:
    layer: str
    seat: str
    threshold: float
    gap: float
    side_mode: str

    @property
    def policy_id(self) -> str:
        return (
            f"{self.layer}:{self.seat}:t={self.threshold:g}:"
            f"g={self.gap:g}:side={self.side_mode}"
        )


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_FILE is not None:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_earliest_window(path: Path, days: int) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    timestamps = pd.read_parquet(path, columns=["ts_ms"])
    values = pd.to_numeric(timestamps["ts_ms"], errors="coerce").dropna()
    if values.empty:
        raise ValueError("matrix has no valid timestamps")
    start_ms = int(values.min())
    end_ms = start_ms + int(days) * DAY_MS
    try:
        frame = pd.read_parquet(
            path,
            filters=[("ts_ms", ">=", start_ms), ("ts_ms", "<", end_ms)],
        )
    except Exception:  # noqa: BLE001 - predicate pushdown is optional
        frame = pd.read_parquet(path)
        ts = pd.to_numeric(frame["ts_ms"], errors="coerce")
        frame = frame[(ts >= start_ms) & (ts < end_ms)]
    frame = (
        frame.sort_values("ts_ms")
        .drop_duplicates("ts_ms", keep="last")
        .reset_index(drop=True)
    )
    expected = days * 1_440
    if len(frame) != expected:
        raise ValueError(
            f"earliest window must be complete: expected {expected:,}, got {len(frame):,}"
        )
    gaps = pd.to_numeric(frame["ts_ms"], errors="raise").diff().dropna()
    if bool((gaps != MINUTE_MS).any()):
        raise ValueError("earliest research window contains one-minute gaps")
    required = {
        "ts_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "taker_buy",
        "taker_sell",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"matrix missing required columns: {missing}")
    log(
        f"[data] rows={len(frame):,} "
        f"range={pd.to_datetime(frame.ts_ms.iloc[0], unit='ms', utc=True)} -> "
        f"{pd.to_datetime(frame.ts_ms.iloc[-1], unit='ms', utc=True)} gaps=0"
    )
    return frame


def make_boundaries(frame: pd.DataFrame, config: Config) -> Boundaries:
    if (
        config.base_train_days
        + config.meta_train_days
        + config.selection_days
        + config.locked_test_days
        != config.window_days
    ):
        raise ValueError("campaign split days must sum to window_days")
    start = int(pd.to_numeric(frame["ts_ms"], errors="raise").iloc[0])
    train_end = start + config.base_train_days * DAY_MS
    meta_end = train_end + config.meta_train_days * DAY_MS
    selection_end = meta_end + config.selection_days * DAY_MS
    test_end = selection_end + config.locked_test_days * DAY_MS
    actual_end = int(pd.to_numeric(frame["ts_ms"], errors="raise").iloc[-1]) + MINUTE_MS
    if test_end != actual_end:
        raise ValueError("split does not terminate exactly at the window boundary")
    return Boundaries(start, train_end, meta_end, selection_end, test_end)


def period_indices(
    timestamps: np.ndarray,
    labels: pd.DataFrame,
    *,
    start_ms: int,
    end_ms: int,
    horizon: int,
) -> np.ndarray:
    candidate = np.flatnonzero(
        (timestamps >= start_ms)
        & (timestamps < end_ms)
        & (((timestamps // MINUTE_MS) % horizon) == 0)
    )
    return candidate[labels.iloc[candidate]["valid"].to_numpy(bool)]


def training_indices(
    timestamps: np.ndarray,
    labels: pd.DataFrame,
    *,
    train_end_ms: int,
    horizon: int,
    max_rows: int,
) -> np.ndarray:
    cutoff = train_end_ms - horizon * MINUTE_MS
    indices = np.flatnonzero(timestamps < cutoff)
    indices = indices[labels.iloc[indices]["valid"].to_numpy(bool)]
    if max_rows > 0 and len(indices) > max_rows:
        indices = indices[-max_rows:]
    return indices


def base_output(
    frame: pd.DataFrame,
    features: pd.DataFrame,
    labels: pd.DataFrame,
    timestamps: np.ndarray,
    train_idx: np.ndarray,
    eval_idx: np.ndarray,
    feature_columns: list[str],
    config: Config,
    horizon: int,
    skipped: dict[str, str],
    metrics: list[dict[str, Any]],
) -> pd.DataFrame:
    x_train, x_eval, _ = impute_train_test(
        features, train_idx, eval_idx, feature_columns
    )
    out = pd.DataFrame(
        {
            "row_index": eval_idx,
            "timestamp_ms": timestamps[eval_idx],
            "timestamp": pd.to_datetime(timestamps[eval_idx], unit="ms", utc=True),
            "horizon": horizon,
            "gross_return_bps": labels.iloc[eval_idx]["gross_return_bps"].to_numpy(
                float
            ),
            "long_net_bps": labels.iloc[eval_idx]["long_net_bps"].to_numpy(float),
            "short_net_bps": labels.iloc[eval_idx]["short_net_bps"].to_numpy(float),
        }
    )
    for column in META_CONTEXT_CANDIDATES:
        if column in features:
            out[column] = features.iloc[eval_idx][column].to_numpy(float)

    successful_classifiers: list[str] = []
    for family in config.classifier_families:
        family_ok = True
        for side in ("long", "short"):
            target = labels.iloc[train_idx][f"{side}_profitable"].to_numpy(np.int8)
            try:
                started = time.monotonic()
                model = classifier_factory(family, config.threads)()
                model.fit(x_train, target)
                probability = positive_probability(model, x_eval)
                out[f"p_{side}_{family}"] = probability
                metrics.append(
                    {
                        "horizon": horizon,
                        "layer": "economic_classifier",
                        "target": side.upper(),
                        "model": family,
                        **binary_metrics(
                            labels.iloc[eval_idx][f"{side}_profitable"].to_numpy(
                                np.int8
                            ),
                            probability,
                        ),
                    }
                )
                del model
                gc.collect()
                log(
                    f"[h={horizon} classifier] {side} {family} "
                    f"{time.monotonic() - started:.1f}s"
                )
            except Exception as exc:  # noqa: BLE001 - optional family boundary
                skipped[f"h{horizon}_classifier_{side}_{family}"] = str(exc)
                family_ok = False
                log(f"[skip] h={horizon} classifier {side} {family}: {exc}")
        if family_ok:
            successful_classifiers.append(family)
    if not successful_classifiers:
        raise RuntimeError(f"no classifier family succeeded for {horizon}m")
    for side in ("long", "short"):
        out[f"p_{side}_ensemble"] = out[
            [f"p_{side}_{family}" for family in successful_classifiers]
        ].mean(axis=1)
        out[f"p_{side}_disagreement"] = out[
            [f"p_{side}_{family}" for family in successful_classifiers]
        ].std(axis=1, ddof=0)

    successful_regressors: list[str] = []
    for family in config.regressor_families:
        family_ok = True
        for side in ("long", "short"):
            target = labels.iloc[train_idx][f"{side}_net_bps"].to_numpy(float)
            try:
                started = time.monotonic()
                model = regressor_factory(family, config.threads)()
                model.fit(x_train, target)
                out[f"mean_{side}_{family}"] = np.asarray(
                    model.predict(x_eval), dtype=float
                )
                del model
                gc.collect()
                log(
                    f"[h={horizon} mean] {side} {family} "
                    f"{time.monotonic() - started:.1f}s"
                )
            except Exception as exc:  # noqa: BLE001 - optional family boundary
                skipped[f"h{horizon}_mean_{side}_{family}"] = str(exc)
                family_ok = False
                log(f"[skip] h={horizon} mean {side} {family}: {exc}")
        if family_ok:
            successful_regressors.append(family)
    if not successful_regressors:
        raise RuntimeError(f"no mean regressor family succeeded for {horizon}m")
    for side in ("long", "short"):
        out[f"mean_{side}_ensemble"] = out[
            [f"mean_{side}_{family}" for family in successful_regressors]
        ].mean(axis=1)

    successful_quantiles: list[str] = []
    for family in config.quantile_families:
        family_ok = True
        for side in ("long", "short"):
            target = labels.iloc[train_idx][f"{side}_net_bps"].to_numpy(float)
            try:
                started = time.monotonic()
                model = quantile_factory(family, 0.20, config.threads)()
                model.fit(x_train, target)
                out[f"q20_{side}_{family}"] = np.asarray(
                    model.predict(x_eval), dtype=float
                )
                del model
                gc.collect()
                log(
                    f"[h={horizon} q20] {side} {family} "
                    f"{time.monotonic() - started:.1f}s"
                )
            except Exception as exc:  # noqa: BLE001 - optional family boundary
                skipped[f"h{horizon}_q20_{side}_{family}"] = str(exc)
                family_ok = False
                log(f"[skip] h={horizon} q20 {side} {family}: {exc}")
        if family_ok:
            successful_quantiles.append(family)
    if not successful_quantiles:
        raise RuntimeError(f"no quantile family succeeded for {horizon}m")
    for side in ("long", "short"):
        out[f"q20_{side}_ensemble"] = out[
            [f"q20_{side}_{family}" for family in successful_quantiles]
        ].mean(axis=1)

    out.attrs["classifier_seats"] = successful_classifiers + ["ensemble"]
    out.attrs["regressor_seats"] = successful_regressors + ["ensemble"]
    out.attrs["quantile_seats"] = successful_quantiles + ["ensemble"]
    return out


def meta_feature_columns(frame: pd.DataFrame) -> list[str]:
    required = [
        "p_long_ensemble",
        "p_short_ensemble",
        "p_long_disagreement",
        "p_short_disagreement",
        "mean_long_ensemble",
        "mean_short_ensemble",
        "q20_long_ensemble",
        "q20_short_ensemble",
    ]
    columns = required + [
        column for column in META_CONTEXT_CANDIDATES if column in frame
    ]
    return list(dict.fromkeys(columns))


def add_act_predictions(
    meta_frame: pd.DataFrame,
    prediction_frames: list[pd.DataFrame],
    *,
    families: list[str],
    threads: int,
    skipped: dict[str, str],
    horizon: int,
) -> list[str]:
    train = meta_frame.copy()
    train["candidate_side"] = np.where(
        train["p_long_ensemble"] >= train["p_short_ensemble"], "LONG", "SHORT"
    )
    train["candidate_net_bps"] = np.where(
        train["candidate_side"] == "LONG",
        train["long_net_bps"],
        train["short_net_bps"],
    )
    target = (train["candidate_net_bps"] > 0.0).to_numpy(np.int8)
    columns = meta_feature_columns(train)
    x_train = train[columns].replace([np.inf, -np.inf], np.nan)
    medians = x_train.median(numeric_only=True).fillna(0.0)
    x_train_values = x_train.fillna(medians).fillna(0.0).to_numpy(np.float32)
    successful: list[str] = []
    for family in families:
        try:
            model = classifier_factory(family, threads)()
            model.fit(x_train_values, target)
            for frame in prediction_frames:
                values = (
                    frame[columns]
                    .replace([np.inf, -np.inf], np.nan)
                    .fillna(medians)
                    .fillna(0.0)
                    .to_numpy(np.float32)
                )
                frame[f"p_act_{family}"] = positive_probability(model, values)
            successful.append(family)
            del model
            gc.collect()
            log(f"[h={horizon} meta] {family} trained rows={len(train):,}")
        except Exception as exc:  # noqa: BLE001 - optional family boundary
            skipped[f"h{horizon}_meta_{family}"] = str(exc)
            log(f"[skip] h={horizon} meta {family}: {exc}")
    return successful


def policy_catalog(
    classifier_seats: list[str],
    regressor_seats: list[str],
    quantile_seats: list[str],
    meta_seats: list[str],
) -> list[PolicySpec]:
    output: list[PolicySpec] = []
    for seat in classifier_seats:
        for threshold in PROBABILITY_THRESHOLDS:
            for gap in PROBABILITY_GAPS:
                for side in SIDE_MODES:
                    output.append(PolicySpec("probability", seat, threshold, gap, side))
    for seat in regressor_seats:
        for threshold in MEAN_THRESHOLDS_BPS:
            for side in SIDE_MODES:
                output.append(PolicySpec("mean_ev", seat, threshold, 0.0, side))
    for seat in quantile_seats:
        for threshold in Q20_THRESHOLDS_BPS:
            for side in SIDE_MODES:
                output.append(PolicySpec("q20", seat, threshold, 0.0, side))
    for seat in meta_seats:
        for threshold in ACT_THRESHOLDS:
            for side in SIDE_MODES:
                output.append(PolicySpec("act_skip", seat, threshold, 0.0, side))
    return output


def apply_policy(frame: pd.DataFrame, spec: PolicySpec) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    if spec.layer == "probability":
        long_score = frame[f"p_long_{spec.seat}"].to_numpy(float)
        short_score = frame[f"p_short_{spec.seat}"].to_numpy(float)
        side = np.where(long_score >= short_score, "LONG", "SHORT")
        best = np.maximum(long_score, short_score)
        act = (best >= spec.threshold) & (np.abs(long_score - short_score) >= spec.gap)
    elif spec.layer == "mean_ev":
        long_score = frame[f"mean_long_{spec.seat}"].to_numpy(float)
        short_score = frame[f"mean_short_{spec.seat}"].to_numpy(float)
        side = np.where(long_score >= short_score, "LONG", "SHORT")
        act = np.maximum(long_score, short_score) >= spec.threshold
    elif spec.layer == "q20":
        long_score = frame[f"q20_long_{spec.seat}"].to_numpy(float)
        short_score = frame[f"q20_short_{spec.seat}"].to_numpy(float)
        side = np.where(long_score >= short_score, "LONG", "SHORT")
        act = np.maximum(long_score, short_score) >= spec.threshold
    elif spec.layer == "act_skip":
        long_score = frame["p_long_ensemble"].to_numpy(float)
        short_score = frame["p_short_ensemble"].to_numpy(float)
        side = np.where(long_score >= short_score, "LONG", "SHORT")
        act = frame[f"p_act_{spec.seat}"].to_numpy(float) >= spec.threshold
    else:
        raise ValueError(spec.layer)
    if spec.side_mode != "BOTH":
        act &= side == spec.side_mode
    out["side"] = side
    out["act"] = act
    out["net_bps"] = np.where(
        side == "LONG",
        frame["long_net_bps"].to_numpy(float),
        frame["short_net_bps"].to_numpy(float),
    )
    return out


def day_block_stats(
    timestamps_ms: np.ndarray,
    pnl_bps: np.ndarray,
    act_mask: np.ndarray,
    *,
    draws: int = 4_000,
    seed: int = 20260728,
) -> dict[str, float]:
    act = np.asarray(act_mask, dtype=bool)
    if not act.any():
        return {"lower": math.nan, "upper": math.nan, "p_value": math.nan}
    sample = pd.DataFrame(
        {
            "day": pd.to_datetime(
                np.asarray(timestamps_ms, dtype=np.int64)[act], unit="ms", utc=True
            ).date,
            "pnl": np.asarray(pnl_bps, dtype=float)[act],
        }
    )
    groups = [group.pnl.to_numpy(float) for _, group in sample.groupby("day")]
    if len(groups) < 5:
        return {"lower": math.nan, "upper": math.nan, "p_value": math.nan}
    rng = np.random.default_rng(seed)
    means = np.empty(draws, dtype=float)
    for draw in range(draws):
        selected = rng.integers(0, len(groups), len(groups))
        means[draw] = float(
            np.concatenate([groups[index] for index in selected]).mean()
        )
    return {
        "lower": float(np.quantile(means, 0.025)),
        "upper": float(np.quantile(means, 0.975)),
        "p_value": float((1 + np.sum(means <= 0.0)) / (draws + 1)),
    }


def weekly_stability(
    timestamps_ms: np.ndarray, pnl_bps: np.ndarray, act_mask: np.ndarray
) -> dict[str, Any]:
    act = np.asarray(act_mask, dtype=bool)
    if not act.any():
        return {
            "weeks": 0,
            "positive_weeks": 0,
            "final_week_positive": False,
            "max_positive_profit_share": 1.0,
        }
    selected_ts = np.asarray(timestamps_ms, dtype=np.int64)[act]
    selected_pnl = np.asarray(pnl_bps, dtype=float)[act]
    week = (
        pd.to_datetime(selected_ts, unit="ms", utc=True)
        .tz_localize(None)
        .to_period("W")
    )
    weekly = pd.Series(selected_pnl).groupby(week).sum()
    positive = weekly.clip(lower=0.0)
    total_positive = float(positive.sum())
    share = float(positive.max() / total_positive) if total_positive > 0.0 else 1.0
    return {
        "weeks": len(weekly),
        "positive_weeks": int((weekly > 0.0).sum()),
        "final_week_positive": bool(float(weekly.iloc[-1]) > 0.0),
        "max_positive_profit_share": share,
    }


def policy_metrics(frame: pd.DataFrame, spec: PolicySpec) -> dict[str, Any]:
    applied = apply_policy(frame, spec)
    economics = economic_metrics(
        frame["timestamp_ms"].to_numpy(np.int64),
        applied["net_bps"].to_numpy(float),
        applied["act"].to_numpy(bool),
    )
    blocks = day_block_stats(
        frame["timestamp_ms"].to_numpy(np.int64),
        applied["net_bps"].to_numpy(float),
        applied["act"].to_numpy(bool),
    )
    stability = weekly_stability(
        frame["timestamp_ms"].to_numpy(np.int64),
        applied["net_bps"].to_numpy(float),
        applied["act"].to_numpy(bool),
    )
    return {
        "policy_id": spec.policy_id,
        **asdict(spec),
        **economics,
        "day_lb_bps": blocks["lower"],
        "day_ub_bps": blocks["upper"],
        "one_sided_p": blocks["p_value"],
        **stability,
    }


def select_policy(
    catalog: list[PolicySpec], selection: pd.DataFrame
) -> tuple[PolicySpec, pd.DataFrame]:
    rows = [policy_metrics(selection, spec) for spec in catalog]
    results = pd.DataFrame(rows)
    min_trades = max(30, math.ceil(0.01 * len(selection)))
    feasible = results[
        (results["trades"] >= min_trades) & (results["coverage"] >= 0.01)
    ].copy()
    if feasible.empty:
        raise RuntimeError("no policy has enough selection-period coverage")
    feasible["_lb_rank"] = pd.to_numeric(
        feasible["day_lb_bps"], errors="coerce"
    ).fillna(-math.inf)
    feasible["_mean_rank"] = pd.to_numeric(
        feasible["mean_net_bps"], errors="coerce"
    ).fillna(-math.inf)
    feasible["_pf_rank"] = pd.to_numeric(
        feasible["profit_factor"], errors="coerce"
    ).fillna(-math.inf)
    winner = feasible.sort_values(
        ["_lb_rank", "_mean_rank", "_pf_rank", "policy_id"],
        ascending=[False, False, False, True],
    ).iloc[0]
    selected = next(spec for spec in catalog if spec.policy_id == winner.policy_id)
    results["selected"] = results["policy_id"] == selected.policy_id
    results["selection_min_trades"] = min_trades
    results["selection_pass"] = (
        (results["trades"] >= min_trades)
        & (results["coverage"] >= 0.01)
        & (results["mean_net_bps"] > 0.0)
        & (results["day_lb_bps"] > 0.0)
        & (results["profit_factor"] > 1.05)
    )
    return selected, results


def shadow_gate(
    frame: pd.DataFrame,
    spec: PolicySpec,
    *,
    stress_extra_bps: float,
) -> dict[str, Any]:
    applied = apply_policy(frame, spec)
    act = applied["act"].to_numpy(bool)
    pnl = applied["net_bps"].to_numpy(float)
    metrics = policy_metrics(frame, spec)
    stressed = economic_metrics(
        frame["timestamp_ms"].to_numpy(np.int64), pnl - stress_extra_bps, act
    )
    checks = {
        "mean_net_positive": bool(
            math.isfinite(float(metrics["mean_net_bps"]))
            and float(metrics["mean_net_bps"]) > 0.0
        ),
        "day_lb_positive": bool(
            math.isfinite(float(metrics["day_lb_bps"]))
            and float(metrics["day_lb_bps"]) > 0.0
        ),
        "profit_factor_gt_1_10": bool(float(metrics["profit_factor"]) > 1.10),
        "trades_ge_100": int(metrics["trades"]) >= 100,
        "coverage_ge_1pct": float(metrics["coverage"]) >= 0.01,
        "positive_weeks_ge_4": int(metrics["positive_weeks"]) >= 4,
        "final_week_positive": bool(metrics["final_week_positive"]),
        "stress_positive": bool(
            math.isfinite(float(stressed["mean_net_bps"]))
            and float(stressed["mean_net_bps"]) > 0.0
        ),
        "profit_not_concentrated": float(metrics["max_positive_profit_share"]) < 0.50,
    }
    return {
        "historical_shadow_candidate": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "stress_mean_net_bps": stressed["mean_net_bps"],
    }


def checkpoint_rows(
    entries: pd.DataFrame,
    frame: pd.DataFrame,
    features: pd.DataFrame,
    *,
    horizon: int,
) -> pd.DataFrame:
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
    high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(float)
    context = [column for column in META_CONTEXT_CANDIDATES if column in features]
    rows: list[dict[str, Any]] = []
    for entry in entries.itertuples(index=False):
        start = int(entry.row_index)
        side_sign = 1.0 if entry.policy_side == "LONG" else -1.0
        entry_price = close[start]
        final = start + horizon
        if final >= len(frame) or not math.isfinite(entry_price):
            continue
        for elapsed in DYNAMIC_CHECKPOINTS[horizon]:
            current = start + elapsed
            if current >= final:
                continue
            observed_high = high[start + 1 : current + 1]
            observed_low = low[start + 1 : current + 1]
            if side_sign > 0:
                path = np.r_[
                    (observed_low / entry_price - 1.0) * 10_000.0,
                    (observed_high / entry_price - 1.0) * 10_000.0,
                ]
            else:
                path = np.r_[
                    -(observed_high / entry_price - 1.0) * 10_000.0,
                    -(observed_low / entry_price - 1.0) * 10_000.0,
                ]
            row: dict[str, Any] = {
                "entry_row_index": start,
                "checkpoint_row_index": current,
                "timestamp_ms": int(frame.ts_ms.iloc[current]),
                "elapsed_minutes": elapsed,
                "elapsed_fraction": elapsed / horizon,
                "side_sign": side_sign,
                "current_signed_return_bps": side_sign
                * (close[current] / entry_price - 1.0)
                * 10_000.0,
                "mfe_so_far_bps": float(np.nanmax(path)),
                "mae_so_far_bps": float(np.nanmin(path)),
                "remaining_signed_return_bps": side_sign
                * (close[final] / close[current] - 1.0)
                * 10_000.0,
                "hold_net_bps": float(entry.policy_net_bps),
            }
            for name in (
                "p_long_ensemble",
                "p_short_ensemble",
                "mean_long_ensemble",
                "mean_short_ensemble",
                "q20_long_ensemble",
                "q20_short_ensemble",
            ):
                row[f"entry_{name}"] = float(getattr(entry, name))
            for name in context:
                row[f"current_{name}"] = float(features.iloc[current][name])
            rows.append(row)
    return pd.DataFrame(rows)


def dynamic_exit_result(
    development_entries: pd.DataFrame,
    test_entries: pd.DataFrame,
    frame: pd.DataFrame,
    features: pd.DataFrame,
    *,
    horizon: int,
    cost_bps: float,
    threads: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    train_rows = checkpoint_rows(development_entries, frame, features, horizon=horizon)
    test_rows = checkpoint_rows(test_entries, frame, features, horizon=horizon)
    if len(train_rows) < 100 or test_rows.empty:
        return {
            "tested": False,
            "reason": "insufficient selected-entry checkpoint rows",
            "train_checkpoint_rows": len(train_rows),
            "test_checkpoint_rows": len(test_rows),
        }, pd.DataFrame()
    feature_columns = [
        column
        for column in train_rows.columns
        if column
        not in {
            "entry_row_index",
            "checkpoint_row_index",
            "timestamp_ms",
            "remaining_signed_return_bps",
            "hold_net_bps",
        }
    ]
    medians = train_rows[feature_columns].median(numeric_only=True).fillna(0.0)
    x_train = (
        train_rows[feature_columns].fillna(medians).fillna(0.0).to_numpy(np.float32)
    )
    x_test = test_rows[feature_columns].fillna(medians).fillna(0.0).to_numpy(np.float32)
    target = train_rows["remaining_signed_return_bps"].to_numpy(float)
    predictions: list[np.ndarray] = []
    for family in ("ridge", "histgb"):
        model = regressor_factory(family, threads)()
        model.fit(x_train, target)
        predictions.append(np.asarray(model.predict(x_test), dtype=float))
        del model
        gc.collect()
    test_rows["predicted_remaining_bps"] = np.mean(predictions, axis=0)
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(float)
    outcomes: list[dict[str, Any]] = []
    for entry_index, checkpoints in test_rows.groupby("entry_row_index", sort=False):
        checkpoints = checkpoints.sort_values("elapsed_minutes")
        exits = checkpoints[
            checkpoints["predicted_remaining_bps"] <= DYNAMIC_EXIT_THRESHOLD_BPS
        ]
        chosen = exits.iloc[0] if not exits.empty else checkpoints.iloc[-1]
        entry = test_entries[test_entries.row_index == entry_index].iloc[0]
        exited = not exits.empty
        exit_row = (
            int(chosen.checkpoint_row_index) if exited else int(entry_index + horizon)
        )
        side_sign = 1.0 if entry.policy_side == "LONG" else -1.0
        exit_net = (
            side_sign * (close[exit_row] / close[int(entry_index)] - 1.0) * 10_000.0
            - cost_bps
        )
        outcomes.append(
            {
                "entry_row_index": int(entry_index),
                "timestamp_ms": int(entry.timestamp_ms),
                "side": entry.policy_side,
                "hold_net_bps": float(entry.policy_net_bps),
                "dynamic_net_bps": float(exit_net),
                "incremental_bps": float(exit_net - entry.policy_net_bps),
                "exited_early": exited,
                "exit_elapsed_minutes": int(exit_row - int(entry_index)),
            }
        )
    outcome = pd.DataFrame(outcomes)
    act = np.ones(len(outcome), dtype=bool)
    dynamic = economic_metrics(
        outcome.timestamp_ms.to_numpy(np.int64),
        outcome.dynamic_net_bps.to_numpy(float),
        act,
    )
    paired = day_block_stats(
        outcome.timestamp_ms.to_numpy(np.int64),
        outcome.incremental_bps.to_numpy(float),
        act,
    )
    result = {
        "tested": True,
        "train_checkpoint_rows": len(train_rows),
        "test_checkpoint_rows": len(test_rows),
        "entries": len(outcome),
        "early_exit_rate": float(outcome.exited_early.mean()),
        "dynamic_economics": dynamic,
        "mean_incremental_vs_hold_bps": float(outcome.incremental_bps.mean()),
        "paired_day_lb_bps": paired["lower"],
        "paired_day_p_value": paired["p_value"],
        "beats_hold": bool(math.isfinite(paired["lower"]) and paired["lower"] > 0.0),
    }
    return result, outcome


def benjamini_hochberg(p_values: dict[str, float]) -> dict[str, float]:
    finite = sorted(
        (
            (key, float(value))
            for key, value in p_values.items()
            if math.isfinite(float(value))
        ),
        key=lambda item: item[1],
    )
    if not finite:
        return {}
    count = len(finite)
    adjusted: dict[str, float] = {}
    running = 1.0
    for rank in range(count, 0, -1):
        key, value = finite[rank - 1]
        running = min(running, value * count / rank)
        adjusted[key] = min(1.0, running)
    return adjusted


def locked_model_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for horizon, horizon_frame in frame.groupby("horizon"):
        for side in ("long", "short"):
            profitable = (horizon_frame[f"{side}_net_bps"] > 0.0).to_numpy(np.int8)
            probability_prefix = f"p_{side}_"
            for column in horizon_frame.columns:
                if not column.startswith(probability_prefix) or column.endswith(
                    "disagreement"
                ):
                    continue
                rows.append(
                    {
                        "horizon": int(horizon),
                        "layer": "economic_classifier",
                        "target": side.upper(),
                        "model": column.removeprefix(probability_prefix),
                        **binary_metrics(
                            profitable, horizon_frame[column].to_numpy(float)
                        ),
                    }
                )
            net = horizon_frame[f"{side}_net_bps"].to_numpy(float)
            mean_prefix = f"mean_{side}_"
            for column in horizon_frame.columns:
                if column.startswith(mean_prefix):
                    rows.append(
                        {
                            "horizon": int(horizon),
                            "layer": "expected_net_regression",
                            "target": side.upper(),
                            "model": column.removeprefix(mean_prefix),
                            **regression_metrics(
                                net, horizon_frame[column].to_numpy(float)
                            ),
                        }
                    )
            q20_prefix = f"q20_{side}_"
            for column in horizon_frame.columns:
                if column.startswith(q20_prefix):
                    rows.append(
                        {
                            "horizon": int(horizon),
                            "layer": "q20_net",
                            "target": side.upper(),
                            "model": column.removeprefix(q20_prefix),
                            **quantile_metrics(
                                net,
                                horizon_frame[column].to_numpy(float),
                                0.20,
                            ),
                        }
                    )
        candidate_side = np.where(
            horizon_frame["p_long_ensemble"] >= horizon_frame["p_short_ensemble"],
            "LONG",
            "SHORT",
        )
        candidate_net = np.where(
            candidate_side == "LONG",
            horizon_frame["long_net_bps"],
            horizon_frame["short_net_bps"],
        )
        candidate_profitable = (candidate_net > 0.0).astype(np.int8)
        for column in horizon_frame.columns:
            if column.startswith("p_act_"):
                rows.append(
                    {
                        "horizon": int(horizon),
                        "layer": "act_skip",
                        "target": "CANDIDATE_PROFITABLE",
                        "model": column.removeprefix("p_act_"),
                        **binary_metrics(
                            candidate_profitable,
                            horizon_frame[column].to_numpy(float),
                        ),
                    }
                )
    return pd.DataFrame(rows)


def run(config: Config, output_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    matrix_path = Path(config.matrix).resolve()
    frame = load_earliest_window(matrix_path, config.window_days)
    boundaries = make_boundaries(frame, config)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="raise").to_numpy(np.int64)
    features = build_causal_features(frame)
    metrics: list[dict[str, Any]] = []
    catalog_parts: list[pd.DataFrame] = []
    locked_predictions: list[pd.DataFrame] = []
    locked_results: dict[str, Any] = {}
    dynamic_results: dict[str, Any] = {}
    dynamic_parts: list[pd.DataFrame] = []
    skipped: dict[str, str] = {}
    selected_specs: dict[str, PolicySpec] = {}

    for horizon in config.horizons:
        labels = build_side_labels(frame, horizon, config.cost_bps)
        train_idx = training_indices(
            timestamps,
            labels,
            train_end_ms=boundaries.train_end_ms,
            horizon=horizon,
            max_rows=config.max_train_rows,
        )
        meta_idx = period_indices(
            timestamps,
            labels,
            start_ms=boundaries.train_end_ms,
            end_ms=boundaries.meta_end_ms,
            horizon=horizon,
        )
        selection_idx = period_indices(
            timestamps,
            labels,
            start_ms=boundaries.meta_end_ms,
            end_ms=boundaries.selection_end_ms,
            horizon=horizon,
        )
        test_idx = period_indices(
            timestamps,
            labels,
            start_ms=boundaries.selection_end_ms,
            end_ms=boundaries.test_end_ms,
            horizon=horizon,
        )
        eval_idx = np.concatenate([meta_idx, selection_idx, test_idx])
        feature_columns = select_features(features, train_idx, config.max_features)
        log(
            f"[h={horizon}] train={len(train_idx):,} meta={len(meta_idx):,} "
            f"select={len(selection_idx):,} locked={len(test_idx):,} "
            f"features={len(feature_columns)}"
        )
        combined = base_output(
            frame,
            features,
            labels,
            timestamps,
            train_idx,
            eval_idx,
            feature_columns,
            config,
            horizon,
            skipped,
            metrics,
        )
        meta = combined.iloc[: len(meta_idx)].copy()
        selection = combined.iloc[
            len(meta_idx) : len(meta_idx) + len(selection_idx)
        ].copy()
        test = combined.iloc[len(meta_idx) + len(selection_idx) :].copy()
        meta_success = add_act_predictions(
            meta,
            [meta, selection, test],
            families=config.meta_families,
            threads=config.threads,
            skipped=skipped,
            horizon=horizon,
        )
        classifier_seats = [
            column.removeprefix("p_long_")
            for column in combined.columns
            if column.startswith("p_long_") and not column.endswith("disagreement")
        ]
        regressor_seats = [
            column.removeprefix("mean_long_")
            for column in combined.columns
            if column.startswith("mean_long_")
        ]
        quantile_seats = [
            column.removeprefix("q20_long_")
            for column in combined.columns
            if column.startswith("q20_long_")
        ]
        catalog = policy_catalog(
            classifier_seats, regressor_seats, quantile_seats, meta_success
        )
        selected, selection_results = select_policy(catalog, selection)
        selection_results.insert(0, "horizon", horizon)
        catalog_parts.append(selection_results)
        selected_specs[str(horizon)] = selected
        gate = shadow_gate(
            test,
            selected,
            stress_extra_bps=config.slippage_bps_per_side,
        )
        locked_results[str(horizon)] = gate
        applied_test = apply_policy(test, selected)
        test["policy_id"] = selected.policy_id
        test["policy_side"] = applied_test.side
        test["policy_act"] = applied_test.act
        test["policy_net_bps"] = applied_test.net_bps
        locked_predictions.append(test)

        development = pd.concat([meta, selection], ignore_index=True)
        applied_development = apply_policy(development, selected)
        development["policy_side"] = applied_development.side
        development["policy_act"] = applied_development.act
        development["policy_net_bps"] = applied_development.net_bps
        development_entries = development[development.policy_act].copy()
        test_entries = test[test.policy_act].copy()
        dynamic, dynamic_frame = dynamic_exit_result(
            development_entries,
            test_entries,
            frame,
            features,
            horizon=horizon,
            cost_bps=config.cost_bps,
            threads=config.threads,
        )
        dynamic_results[str(horizon)] = dynamic
        if not dynamic_frame.empty:
            dynamic_frame.insert(0, "horizon", horizon)
            dynamic_parts.append(dynamic_frame)
        log(
            f"[h={horizon}] selected={selected.policy_id} "
            f"locked_candidate={gate['historical_shadow_candidate']} "
            f"trades={gate['metrics']['trades']} "
            f"net={gate['metrics']['mean_net_bps']}"
        )

    p_values = {
        horizon: result["metrics"]["one_sided_p"]
        for horizon, result in locked_results.items()
    }
    adjusted = benjamini_hochberg(p_values)
    for horizon, result in locked_results.items():
        q_value = adjusted.get(horizon, math.nan)
        result["bh_q_value"] = q_value
        result["bh_q_le_0_10"] = bool(math.isfinite(q_value) and q_value <= 0.10)
        result["historical_shadow_candidate"] = bool(
            result["historical_shadow_candidate"] and result["bh_q_le_0_10"]
        )
        if horizon in dynamic_results and dynamic_results[horizon].get("tested"):
            dynamic_results[horizon]["entry_policy_passed"] = result[
                "historical_shadow_candidate"
            ]
            dynamic_results[horizon]["historical_shadow_candidate"] = bool(
                result["historical_shadow_candidate"]
                and dynamic_results[horizon]["beats_hold"]
                and dynamic_results[horizon]["dynamic_economics"]["mean_net_bps"] > 0.0
                and dynamic_results[horizon]["dynamic_economics"]["profit_factor"]
                > 1.10
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_frame = pd.concat(catalog_parts, ignore_index=True)
    predictions_frame = pd.concat(locked_predictions, ignore_index=True)
    metrics_frame = pd.DataFrame(metrics)
    locked_diagnostics = locked_model_diagnostics(predictions_frame)
    catalog_frame.to_csv(output_dir / "selection_catalog.csv", index=False)
    predictions_frame.to_csv(output_dir / "locked_test_predictions.csv", index=False)
    predictions_frame.to_parquet(
        output_dir / "locked_test_predictions.parquet", index=False
    )
    metrics_frame.to_csv(output_dir / "model_metrics.csv", index=False)
    locked_diagnostics.to_csv(output_dir / "locked_model_diagnostics.csv", index=False)
    if dynamic_parts:
        pd.concat(dynamic_parts, ignore_index=True).to_csv(
            output_dir / "dynamic_exit_predictions.csv", index=False
        )
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "automatic_promotion": False,
        "real_orders_authorized": False,
        "protocol": {
            "window": f"earliest complete {config.window_days} days",
            "base_train_days": config.base_train_days,
            "meta_train_days": config.meta_train_days,
            "selection_days": config.selection_days,
            "locked_test_days": config.locked_test_days,
            "policy_configurations_per_horizon": int(
                len(catalog_parts[0]) if catalog_parts else 0
            ),
            "dynamic_exit_champion": "HOLD",
            "dynamic_exit_rule": (
                "exit at first fixed checkpoint where ensemble predicted remaining "
                f"signed return <= {DYNAMIC_EXIT_THRESHOLD_BPS:g} bps"
            ),
        },
        "config": asdict(config),
        "source": {
            "path": str(matrix_path),
            "sha256": sha256_file(matrix_path),
            "rows_in_window": len(frame),
            "first_ts_ms": int(timestamps[0]),
            "last_ts_ms": int(timestamps[-1]),
        },
        "boundaries": asdict(boundaries),
        "selected_policies": {
            horizon: asdict(spec) | {"policy_id": spec.policy_id}
            for horizon, spec in selected_specs.items()
        },
        "locked_test": locked_results,
        "dynamic_exit": dynamic_results,
        "skipped": skipped,
        "elapsed_seconds": time.monotonic() - started,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(json_safe(manifest), indent=2) + "\n", encoding="utf-8"
    )
    log(
        f"[done] output={output_dir} elapsed={manifest['elapsed_seconds']:.1f}s "
        f"locked={ {h: r['historical_shadow_candidate'] for h, r in locked_results.items()} } "
        f"dynamic={ {h: r.get('historical_shadow_candidate', False) for h, r in dynamic_results.items()} }"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    parser.add_argument("--horizons", nargs="+", type=int, default=[5, 15])
    parser.add_argument("--window-days", type=int, default=180)
    parser.add_argument("--base-train-days", type=int, default=120)
    parser.add_argument("--meta-train-days", type=int, default=15)
    parser.add_argument("--selection-days", type=int, default=15)
    parser.add_argument("--locked-test-days", type=int, default=30)
    parser.add_argument("--fee-bps-per-side", type=float, default=5.0)
    parser.add_argument("--slippage-bps-per-side", type=float, default=1.0)
    parser.add_argument("--max-features", type=int, default=80)
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--classifier-families", default="all")
    parser.add_argument("--regressor-families", default="all")
    parser.add_argument("--quantile-families", default="all")
    parser.add_argument("--meta-families", default="all")
    parser.add_argument("--run-name", default="")
    return parser.parse_args()


def parse_families(value: str, allowed: tuple[str, ...]) -> list[str]:
    if value.strip().lower() == "all":
        return list(allowed)
    values = [part.strip().lower() for part in value.split(",") if part.strip()]
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(f"unknown model families: {unknown}")
    if not values:
        raise ValueError("at least one model family is required")
    return values


def main() -> None:
    global _LOG_FILE
    args = parse_args()
    if sorted(set(args.horizons) - set(DYNAMIC_CHECKPOINTS)):
        raise ValueError("supported horizons are 5 and 15")
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = DEFAULT_OUTPUT_ROOT / run_name
    _LOG_FILE = output_dir / "run.log"
    config = Config(
        matrix=args.matrix,
        horizons=sorted(set(args.horizons)),
        window_days=args.window_days,
        base_train_days=args.base_train_days,
        meta_train_days=args.meta_train_days,
        selection_days=args.selection_days,
        locked_test_days=args.locked_test_days,
        fee_bps_per_side=args.fee_bps_per_side,
        slippage_bps_per_side=args.slippage_bps_per_side,
        max_features=args.max_features,
        max_train_rows=args.max_train_rows,
        threads=max(1, args.threads),
        classifier_families=parse_families(
            args.classifier_families, CLASSIFIER_FAMILIES
        ),
        regressor_families=parse_families(args.regressor_families, REGRESSOR_FAMILIES),
        quantile_families=parse_families(args.quantile_families, QUANTILE_FAMILIES),
        meta_families=parse_families(args.meta_families, META_FAMILIES),
        run_name=run_name,
    )
    log(f"[start] config={json.dumps(asdict(config), sort_keys=True)}")
    run(config, output_dir)


if __name__ == "__main__":
    main()
