"""BINANCE_COST_AWARE_NET_PNL_V1 research campaign."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .contracts import Protocol, implementation_sha256, sha256_file
from .execution import load_funding_events, simulate_round_trip
from .features import FEATURE_COLUMNS, build_causal_features, decision_rows
from .models import EconomicActionHead, ProbabilityHead
from .validation import (
    TrialRegistry,
    chronological_splits,
    deflated_sharpe_from_trials,
    economic_metrics,
    finite_json,
    markdown_table,
    pbo_from_fold_policy_returns,
    positive_profit_concentration,
)


CAMPAIGN_ID = "BINANCE_COST_AWARE_NET_PNL_V1"


def _log(message: str, logger: Callable[[str], None] | None) -> None:
    if logger is not None:
        logger(f"[{CAMPAIGN_ID}] {message}")


def _dataset_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def _funding_path(input_paths: dict[str, Path]) -> Path | None:
    root = next(iter(input_paths.values())).parents[1]
    candidates = list(root.glob("raw/funding_rates/symbol=BTCUSDT/**/*.parquet"))
    return candidates[0] if candidates else None


def _cache_metadata_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".meta.json")


def _load_cached_frame(
    output_path: Path,
    *,
    cache_fingerprint: str,
    force: bool,
) -> pd.DataFrame | None:
    metadata_path = _cache_metadata_path(output_path)
    if force or not output_path.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if metadata.get("cache_fingerprint") != cache_fingerprint:
        return None
    return pd.read_parquet(output_path)


def _write_cached_frame(
    frame: pd.DataFrame,
    output_path: Path,
    *,
    cache_fingerprint: str,
) -> None:
    frame.to_parquet(output_path, index=False)
    _cache_metadata_path(output_path).write_text(
        json.dumps(
            {
                "cache_fingerprint": cache_fingerprint,
                "rows": len(frame),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _build_primary_labels(
    books: pd.DataFrame,
    decisions: pd.DataFrame,
    protocol: Protocol,
    funding_events: pd.DataFrame,
    output_path: Path,
    *,
    cache_fingerprint: str,
    force: bool,
    logger: Callable[[str], None] | None,
) -> pd.DataFrame:
    cached = _load_cached_frame(
        output_path,
        cache_fingerprint=cache_fingerprint,
        force=force,
    )
    if cached is not None:
        return cached
    rows: list[dict[str, Any]] = []
    total = len(decisions) * len(protocol.horizons) * 2
    completed = 0
    execution = protocol.raw["execution"]
    exit_config = protocol.raw["dynamic_exit"]
    receive_timestamps = books["receive_ts_ns"].to_numpy(np.int64)
    mid_prices = books["mid"].to_numpy(float)
    for decision in decisions.itertuples(index=False):
        decision_ts = int(decision.receive_ts_ns)
        for horizon in protocol.horizons:
            for action in ("LONG", "SHORT"):
                trip = simulate_round_trip(
                    books,
                    decision_ts_ns=decision_ts,
                    action=action,
                    horizon_seconds=horizon,
                    latency_ms=protocol.primary_latency_ms,
                    capital_usd=protocol.primary_capital_usd,
                    fee_bps=protocol.fee_bps,
                    impact_bps=protocol.impact_bps,
                    signal_expiry_ms=int(execution["signal_expiry_ms"]),
                    minimum_fill_fraction=float(
                        execution["minimum_fill_fraction"]
                    ),
                    maximum_book_age_ms=int(
                        execution["maximum_book_age_ms"]
                    ),
                    funding_events=funding_events,
                    target_net_bps=float(exit_config["target_net_bps"]),
                    stop_net_bps=float(exit_config["stop_net_bps"]),
                    include_path_metrics=True,
                    receive_timestamps=receive_timestamps,
                    mid_prices=mid_prices,
                )
                row = trip.as_dict()
                row["book_index"] = int(decision.book_index)
                rows.append(row)
                completed += 1
        if completed and completed % 2_000 == 0:
            _log(f"primary labels {completed:,}/{total:,}", logger)
    labels = pd.DataFrame(rows)
    _write_cached_frame(
        labels,
        output_path,
        cache_fingerprint=cache_fingerprint,
    )
    return labels


def _build_execution_surface(
    books: pd.DataFrame,
    decisions: pd.DataFrame,
    protocol: Protocol,
    funding_events: pd.DataFrame,
    output_path: Path,
    *,
    cache_fingerprint: str,
    force: bool,
    logger: Callable[[str], None] | None,
) -> pd.DataFrame:
    cached = _load_cached_frame(
        output_path,
        cache_fingerprint=cache_fingerprint,
        force=force,
    )
    if cached is not None:
        return cached
    rows: list[dict[str, Any]] = []
    combinations = (
        len(decisions)
        * len(protocol.horizons)
        * len(protocol.latencies)
        * len(protocol.capital_sizes)
        * 2
    )
    completed = 0
    execution = protocol.raw["execution"]
    receive_timestamps = books["receive_ts_ns"].to_numpy(np.int64)
    mid_prices = books["mid"].to_numpy(float)
    for decision in decisions.itertuples(index=False):
        for horizon in protocol.horizons:
            for latency in protocol.latencies:
                for capital in protocol.capital_sizes:
                    for action in ("LONG", "SHORT"):
                        trip = simulate_round_trip(
                            books,
                            decision_ts_ns=int(decision.receive_ts_ns),
                            action=action,
                            horizon_seconds=horizon,
                            latency_ms=latency,
                            capital_usd=capital,
                            fee_bps=protocol.fee_bps,
                            impact_bps=protocol.impact_bps,
                            signal_expiry_ms=int(execution["signal_expiry_ms"]),
                            minimum_fill_fraction=float(
                                execution["minimum_fill_fraction"]
                            ),
                            maximum_book_age_ms=int(
                                execution["maximum_book_age_ms"]
                            ),
                            funding_events=funding_events,
                            include_path_metrics=False,
                            receive_timestamps=receive_timestamps,
                            mid_prices=mid_prices,
                        )
                        rows.append(trip.as_dict())
                        completed += 1
        if completed and completed % 25_000 == 0:
            _log(
                f"execution surface {completed:,}/{combinations:,}",
                logger,
            )
    surface = pd.DataFrame(rows)
    _write_cached_frame(
        surface,
        output_path,
        cache_fingerprint=cache_fingerprint,
    )
    return surface


def _wide_labels(
    decisions: pd.DataFrame,
    labels: pd.DataFrame,
    horizon: int,
) -> pd.DataFrame:
    subset = labels[labels["horizon_seconds"] == horizon].copy()
    long = subset[subset["action"] == "LONG"].add_prefix("long_")
    short = subset[subset["action"] == "SHORT"].add_prefix("short_")
    long = long.rename(columns={"long_decision_ts_ns": "receive_ts_ns"})
    short = short.rename(columns={"short_decision_ts_ns": "receive_ts_ns"})
    merged = decisions.merge(long, on="receive_ts_ns", how="inner")
    merged = merged.merge(short, on="receive_ts_ns", how="inner")
    return merged.sort_values("receive_ts_ns").reset_index(drop=True)


def _fit_predict_action(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    action_prefix: str,
    seed: int,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    status = train[f"{action_prefix}_status"].astype(str)
    fill_target = (status != "REJECTED").to_numpy(int)
    fill_head = ProbabilityHead.fit(
        train[list(FEATURE_COLUMNS)].to_numpy(float),
        fill_target,
        seed=seed,
    )
    filled = train[
        train[f"{action_prefix}_net_pnl_usd"].notna()
        & (train[f"{action_prefix}_status"] != "REJECTED")
    ]
    if len(filled) < 50:
        raise ValueError(f"insufficient filled {action_prefix} training rows")
    features = filled[list(FEATURE_COLUMNS)].to_numpy(float)
    slippage = (
        pd.to_numeric(
            filled[f"{action_prefix}_entry_slippage_bps"], errors="coerce"
        ).fillna(0)
        + pd.to_numeric(
            filled[f"{action_prefix}_exit_slippage_bps"], errors="coerce"
        ).fillna(0)
    ).to_numpy(float)
    time_to_profit = pd.to_numeric(
        filled[f"{action_prefix}_time_to_first_profit_seconds"],
        errors="coerce",
    ).to_numpy(float)
    head = EconomicActionHead.fit(
        features,
        net_pnl=filled[f"{action_prefix}_net_pnl_usd"].to_numpy(float),
        slippage_bps=slippage,
        time_to_profit_seconds=time_to_profit,
        seed=seed + 10,
    )
    test_features = test[list(FEATURE_COLUMNS)].to_numpy(float)
    predictions = head.predict(test_features)
    move_head = ProbabilityHead.fit(
        features,
        filled[f"{action_prefix}_mfe_usd"].to_numpy(float) > 0,
        seed=seed + 400,
    )
    predictions["p_move_exceeds_round_trip_cost"] = move_head.predict(
        test_features
    )
    fill_probability = fill_head.predict(test_features)
    return predictions, fill_probability


def _prediction_frame(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    role: str,
    horizon: int,
    seed: int,
    uncertainty_reserve_bps: float,
    capital_usd: float,
) -> pd.DataFrame:
    long_prediction, long_fill = _fit_predict_action(
        train, test, action_prefix="long", seed=seed
    )
    short_prediction, short_fill = _fit_predict_action(
        train, test, action_prefix="short", seed=seed + 1_000
    )
    output = test[
        [
            "receive_ts_ns",
            "book_index",
            "ret_60s_bps",
            "long_entry_ts_ns",
            "long_exit_ts_ns",
            "long_net_pnl_usd",
            "long_fee_usd",
            "long_impact_reserve_usd",
            "long_funding_usd",
            "long_fill_fraction",
            "long_status",
            "short_entry_ts_ns",
            "short_exit_ts_ns",
            "short_net_pnl_usd",
            "short_fee_usd",
            "short_impact_reserve_usd",
            "short_funding_usd",
            "short_fill_fraction",
            "short_status",
        ]
    ].copy()
    output["role"] = role
    output["horizon_seconds"] = horizon
    for name, values in long_prediction.items():
        output[f"long_{name}"] = values
    for name, values in short_prediction.items():
        output[f"short_{name}"] = values
    output["long_fill_probability"] = long_fill
    output["short_fill_probability"] = short_fill
    reserve = capital_usd * uncertainty_reserve_bps / 10_000.0
    long_q20 = output["long_net_q20"].to_numpy(float)
    short_q20 = output["short_net_q20"].to_numpy(float)
    action = np.full(len(output), "WAIT", dtype=object)
    action[(long_q20 > short_q20) & (long_q20 > reserve)] = "LONG"
    action[(short_q20 > long_q20) & (short_q20 > reserve)] = "SHORT"
    output["selector_action"] = action
    output["selector_q20_usd"] = np.maximum.reduce(
        [long_q20, short_q20, np.zeros(len(output))]
    )
    output["uncertainty_reserve_usd"] = reserve
    return output


def _policy_actions(predictions: pd.DataFrame, seed: int) -> dict[str, np.ndarray]:
    count = len(predictions)
    rng = np.random.default_rng(seed)
    momentum = np.where(predictions["ret_60s_bps"].to_numpy(float) >= 0, "LONG", "SHORT")
    return {
        "ALWAYS_LONG": np.full(count, "LONG", dtype=object),
        "ALWAYS_SHORT": np.full(count, "SHORT", dtype=object),
        "RANDOM_SIDE": rng.choice(np.array(["LONG", "SHORT"], dtype=object), count),
        "MOMENTUM": momentum,
        "MEAN_REVERSION": np.where(momentum == "LONG", "SHORT", "LONG"),
        "COST_AWARE_SELECTOR": predictions["selector_action"].to_numpy(object),
        "WAIT": np.full(count, "WAIT", dtype=object),
    }


def _trades_for_policy(
    predictions: pd.DataFrame,
    actions: np.ndarray,
    policy: str,
) -> pd.DataFrame:
    rows = []
    available_after = -1
    for index, action in enumerate(actions):
        if action == "WAIT":
            continue
        prefix = action.lower()
        entry_ts = predictions.iloc[index][f"{prefix}_entry_ts_ns"]
        exit_ts = predictions.iloc[index][f"{prefix}_exit_ts_ns"]
        pnl = predictions.iloc[index][f"{prefix}_net_pnl_usd"]
        if pd.isna(entry_ts) or pd.isna(exit_ts) or pd.isna(pnl):
            continue
        if int(entry_ts) < available_after:
            continue
        available_after = int(exit_ts)
        fee = float(predictions.iloc[index][f"{prefix}_fee_usd"])
        impact = float(
            predictions.iloc[index][f"{prefix}_impact_reserve_usd"]
        )
        funding = float(
            predictions.iloc[index][f"{prefix}_funding_usd"]
        )
        rows.append(
            {
                "policy": policy,
                "role": str(predictions.iloc[index]["role"]),
                "decision_ts_ns": int(predictions.iloc[index]["receive_ts_ns"]),
                "entry_ts_ns": int(entry_ts),
                "exit_ts_ns": int(exit_ts),
                "horizon_seconds": int(
                    predictions.iloc[index]["horizon_seconds"]
                ),
                "action": action,
                "capital_usd": 1_000.0,
                "net_pnl_usd": float(pnl),
                "gross_pnl_usd": float(pnl) + fee + impact - funding,
                "fee_usd": fee,
                "impact_reserve_usd": impact,
                "funding_usd": funding,
                "fill_fraction": float(
                    predictions.iloc[index][f"{prefix}_fill_fraction"]
                ),
                "status": str(predictions.iloc[index][f"{prefix}_status"]),
                "observed_holding_seconds": (
                    int(exit_ts) - int(entry_ts)
                )
                / 1_000_000_000,
            }
        )
    return pd.DataFrame(rows)


def _surface_metrics(surface: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_columns = [
        "horizon_seconds",
        "latency_ms",
        "capital_usd",
        "action",
    ]
    for keys, group in surface.groupby(group_columns, sort=True):
        net = pd.to_numeric(group["net_pnl_usd"], errors="coerce")
        valid = net.dropna().to_numpy(float)
        rows.append(
            {
                **dict(zip(group_columns, keys)),
                "decisions": int(len(group)),
                "fill_rate": float(net.notna().mean()),
                "mean_fill_fraction": float(
                    pd.to_numeric(
                        group["fill_fraction"], errors="coerce"
                    ).fillna(0).mean()
                ),
                "total_net_pnl_usd": (
                    float(valid.sum()) if len(valid) else None
                ),
                "mean_net_pnl_usd": (
                    float(valid.mean()) if len(valid) else None
                ),
                "q20_net_pnl_usd": (
                    float(np.quantile(valid, 0.20)) if len(valid) else None
                ),
                "profit_factor": (
                    float(
                        valid[valid > 0].sum()
                        / max(-valid[valid < 0].sum(), 1e-12)
                    )
                    if len(valid)
                    else None
                ),
                "mean_entry_slippage_bps": float(
                    pd.to_numeric(
                        group["entry_slippage_bps"], errors="coerce"
                    ).fillna(0).mean()
                ),
                "mean_exit_slippage_bps": float(
                    pd.to_numeric(
                        group["exit_slippage_bps"], errors="coerce"
                    ).fillna(0).mean()
                ),
            }
        )
    return pd.DataFrame(rows)


def _evaluate_policies(
    predictions: pd.DataFrame,
    *,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_frames = []
    metric_rows = []
    for policy, actions in _policy_actions(predictions, seed).items():
        trades = _trades_for_policy(predictions, actions, policy)
        if not trades.empty:
            trade_frames.append(trades)
        metrics = economic_metrics(trades)
        metric_rows.append(
            {
                "role": str(predictions["role"].iloc[0]),
                "horizon_seconds": int(predictions["horizon_seconds"].iloc[0]),
                "policy": policy,
                **metrics,
            }
        )
    metric_rows.append(
        {
            "role": str(predictions["role"].iloc[0]),
            "horizon_seconds": int(predictions["horizon_seconds"].iloc[0]),
            "policy": "CURRENT_ENSEMBLE",
            "trades": 0,
            "total_net_pnl_usd": None,
            "mean_net_pnl_usd": None,
            "promotion_evidence_available": False,
            "unavailable_reason": "no timestamp-aligned current-ensemble archive",
        }
    )
    all_trades = (
        pd.concat(trade_frames, ignore_index=True)
        if trade_frames
        else pd.DataFrame()
    )
    return all_trades, pd.DataFrame(metric_rows)


def _stress_selected(
    books: pd.DataFrame,
    selected_predictions: pd.DataFrame,
    protocol: Protocol,
    funding_events: pd.DataFrame,
    *,
    logger: Callable[[str], None] | None,
) -> pd.DataFrame:
    selected = selected_predictions[
        selected_predictions["selector_action"] != "WAIT"
    ].copy()
    if selected.empty:
        return pd.DataFrame()
    stress = protocol.raw["stress"]
    execution = protocol.raw["execution"]
    rows = []
    receive_timestamps = books["receive_ts_ns"].to_numpy(np.int64)
    mid_prices = books["mid"].to_numpy(float)
    configurations = (
        len(stress["fee_multipliers"])
        * len(stress["slippage_multipliers"])
        * len(stress["latencies_ms"])
        * len(stress["capital_usd"])
    )
    completed = 0
    for fee_multiplier in stress["fee_multipliers"]:
        for slippage_multiplier in stress["slippage_multipliers"]:
            for latency in stress["latencies_ms"]:
                for capital in stress["capital_usd"]:
                    trades = []
                    available_after: dict[int, int] = {}
                    for row in selected.itertuples(index=False):
                        horizon = int(row.horizon_seconds)
                        if int(row.receive_ts_ns) < available_after.get(horizon, -1):
                            continue
                        trip = simulate_round_trip(
                            books,
                            decision_ts_ns=int(row.receive_ts_ns),
                            action=str(row.selector_action),
                            horizon_seconds=horizon,
                            latency_ms=int(latency),
                            capital_usd=float(capital),
                            fee_bps=protocol.fee_bps * float(fee_multiplier),
                            impact_bps=protocol.impact_bps
                            * float(slippage_multiplier),
                            signal_expiry_ms=int(execution["signal_expiry_ms"]),
                            minimum_fill_fraction=float(
                                execution["minimum_fill_fraction"]
                            ),
                            maximum_book_age_ms=int(
                                execution["maximum_book_age_ms"]
                            ),
                            funding_events=funding_events,
                            include_path_metrics=False,
                            receive_timestamps=receive_timestamps,
                            mid_prices=mid_prices,
                        )
                        if trip.net_pnl_usd is not None:
                            available_after[horizon] = int(trip.exit_ts_ns or 0)
                            trades.append(trip.as_dict())
                    metrics = economic_metrics(pd.DataFrame(trades))
                    rows.append(
                        {
                            "fee_multiplier": float(fee_multiplier),
                            "slippage_multiplier": float(slippage_multiplier),
                            "latency_ms": int(latency),
                            "capital_usd": float(capital),
                            **metrics,
                        }
                    )
                    completed += 1
                    if completed % 25 == 0:
                        _log(
                            f"stress {completed}/{configurations}",
                            logger,
                        )
    return pd.DataFrame(rows)


def run(
    *,
    books: pd.DataFrame,
    trade_flow: pd.DataFrame,
    input_paths: dict[str, Path],
    run_dir: Path,
    protocol: Protocol,
    force: bool = False,
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    campaign_dir = run_dir / CAMPAIGN_ID
    campaign_dir.mkdir(parents=True, exist_ok=True)
    _log("building causal features", logger)
    features = build_causal_features(
        books,
        trade_flow,
        maximum_gap_ms=int(
            protocol.raw["execution"]["maximum_book_age_ms"]
        ),
    )
    decisions = decision_rows(
        features,
        interval_seconds=protocol.decision_interval_seconds,
        maximum_horizon_seconds=max(protocol.horizons),
    )
    decisions.to_parquet(campaign_dir / "decision_features.parquet", index=False)

    funding_path = _funding_path(input_paths)
    funding_events = load_funding_events(
        str(funding_path) if funding_path is not None else None
    )
    dataset_paths = [
        input_paths["normalized_books"],
        input_paths["trade_flow"],
    ]
    if funding_path is not None:
        dataset_paths.append(funding_path)
    dataset_hash = _dataset_sha256(dataset_paths)
    code_sha256 = implementation_sha256()
    cache_fingerprint = hashlib.sha256(
        (
            protocol.sha256
            + code_sha256
            + dataset_hash
        ).encode("ascii")
    ).hexdigest()
    labels = _build_primary_labels(
        books,
        decisions,
        protocol,
        funding_events,
        campaign_dir / "primary_execution_labels.parquet",
        cache_fingerprint=cache_fingerprint,
        force=force,
        logger=logger,
    )
    surface = _build_execution_surface(
        books,
        decisions,
        protocol,
        funding_events,
        campaign_dir / "latency_capacity_surface.parquet",
        cache_fingerprint=cache_fingerprint,
        force=force,
        logger=logger,
    )
    surface_metrics = _surface_metrics(surface)
    surface_metrics.to_csv(campaign_dir / "surface_metrics.csv", index=False)
    _log(
        f"labels={len(labels):,} surface={len(surface):,}; fitting heads",
        logger,
    )

    prediction_frames = []
    policy_trades = []
    policy_metrics = []
    fold_policy_returns: list[list[float]] = []
    trial_sharpes: list[float] = []
    uncertainty_bps = float(
        protocol.raw["selector"]["uncertainty_reserve_bps"]
    )
    final_prediction_frames = []
    for horizon in protocol.horizons:
        wide = _wide_labels(decisions, labels, horizon)
        splits, final_split = chronological_splits(
            wide["receive_ts_ns"].to_numpy(np.int64),
            development_fraction=float(
                protocol.raw["validation"]["development_fraction"]
            ),
            folds=int(protocol.raw["validation"]["walk_forward_folds"]),
            purge_seconds=int(protocol.raw["validation"]["purge_seconds"]),
            embargo_seconds=int(protocol.raw["validation"]["embargo_seconds"]),
        )
        horizon_fold_returns = []
        for fold_index, split in enumerate(splits):
            prediction = _prediction_frame(
                wide.iloc[split.train_indices],
                wide.iloc[split.test_indices],
                role=split.role,
                horizon=horizon,
                seed=protocol.random_seed + horizon * 100 + fold_index,
                uncertainty_reserve_bps=uncertainty_bps,
                capital_usd=protocol.primary_capital_usd,
            )
            prediction_frames.append(prediction)
            trades, metrics = _evaluate_policies(
                prediction,
                seed=protocol.random_seed + fold_index,
            )
            policy_trades.append(trades)
            policy_metrics.append(metrics)
            returns = []
            for policy in (
                "ALWAYS_LONG",
                "ALWAYS_SHORT",
                "RANDOM_SIDE",
                "MOMENTUM",
                "MEAN_REVERSION",
                "COST_AWARE_SELECTOR",
                "WAIT",
            ):
                row = metrics[metrics["policy"] == policy].iloc[0]
                value = row.get("mean_net_pnl_usd")
                returns.append(float(value) if pd.notna(value) else 0.0)
                sample_sharpe = row.get("sample_sharpe")
                if pd.notna(sample_sharpe):
                    trial_sharpes.append(float(sample_sharpe))
            horizon_fold_returns.append(returns)
        fold_policy_returns.extend(horizon_fold_returns)
        final_prediction = _prediction_frame(
            wide.iloc[final_split.train_indices],
            wide.iloc[final_split.test_indices],
            role=final_split.role,
            horizon=horizon,
            seed=protocol.random_seed + horizon * 100 + 99,
            uncertainty_reserve_bps=uncertainty_bps,
            capital_usd=protocol.primary_capital_usd,
        )
        prediction_frames.append(final_prediction)
        final_prediction_frames.append(final_prediction)
        trades, metrics = _evaluate_policies(
            final_prediction,
            seed=protocol.random_seed + horizon,
        )
        policy_trades.append(trades)
        policy_metrics.append(metrics)

    predictions = pd.concat(prediction_frames, ignore_index=True)
    predictions.to_parquet(campaign_dir / "model_predictions.parquet", index=False)
    trades_frame = pd.concat(
        [frame for frame in policy_trades if not frame.empty],
        ignore_index=True,
    )
    trades_frame.to_csv(campaign_dir / "policy_trades.csv", index=False)
    metrics_frame = pd.concat(policy_metrics, ignore_index=True)
    metrics_frame.to_csv(campaign_dir / "policy_metrics.csv", index=False)

    final_predictions = pd.concat(final_prediction_frames, ignore_index=True)
    stress = _stress_selected(
        books,
        final_predictions,
        protocol,
        funding_events,
        logger=logger,
    )
    stress.to_csv(campaign_dir / "stress_capacity.csv", index=False)

    selected_trades = trades_frame[
        (trades_frame["policy"] == "COST_AWARE_SELECTOR")
        & (trades_frame["role"] == "untouched_test")
    ]
    pbo = pbo_from_fold_policy_returns(fold_policy_returns)
    dsr = deflated_sharpe_from_trials(
        selected_trades["net_pnl_usd"].tolist(),
        trial_sharpes,
    )
    selected_metrics = economic_metrics(selected_trades)
    selected_metrics["single_day_positive_profit_concentration"] = (
        positive_profit_concentration(
            selected_trades.assign(
                day=pd.to_datetime(
                    selected_trades["entry_ts_ns"], unit="ns", utc=True
                ).dt.strftime("%Y-%m-%d")
            ),
            bucket_column="day",
        )
        if not selected_trades.empty
        else None
    )
    selected_metrics["pbo"] = pbo
    selected_metrics["pbo_scope"] = (
        "policy family including WAIT; zero selector trades means this is "
        "abstention stability, not evidence of profitable skill"
    )
    selected_metrics["deflated_sharpe"] = dsr
    selected_metrics["promotion_status"] = "RESEARCH_ONLY_INSUFFICIENT_EVIDENCE"
    selected_metrics["promotion_blockers"] = [
        "fewer than 30 independent trading days",
        "fewer than 8 calendar weeks",
        "no forward paper period",
        "source receive cadence cannot resolve 100-1000ms latency differences",
    ]

    registry = TrialRegistry(
        run_dir / "trial_registry.jsonl",
        protocol.sha256,
        code_sha256,
    )
    for row in metrics_frame.to_dict("records"):
        registry.append(
            campaign_id=CAMPAIGN_ID,
            family=str(row["policy"]),
            parameters={
                "horizon_seconds": row["horizon_seconds"],
                "role": row["role"],
                "latency_ms": protocol.primary_latency_ms,
                "capital_usd": protocol.primary_capital_usd,
            },
            metrics=finite_json(row),
            dataset_sha256=dataset_hash,
        )
    summary = {
        "campaign_id": CAMPAIGN_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": protocol.sha256,
        "implementation_sha256": code_sha256,
        "dataset_sha256": dataset_hash,
        "decision_rows": int(len(decisions)),
        "primary_label_rows": int(len(labels)),
        "execution_surface_rows": int(len(surface)),
        "current_ensemble_comparison": {
            "available": False,
            "reason": "no timestamp-aligned predictions for the archived L2 day",
        },
        "selected_policy": finite_json(selected_metrics),
        "stress_rows": int(len(stress)),
        "surface_metric_rows": int(len(surface_metrics)),
        "production_permissions_changed": False,
    }
    (campaign_dir / "summary.json").write_text(
        json.dumps(finite_json(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_report(campaign_dir, summary, metrics_frame, stress)
    return summary


def _write_report(
    campaign_dir: Path,
    summary: dict[str, Any],
    metrics: pd.DataFrame,
    stress: pd.DataFrame,
) -> None:
    final = metrics[metrics["role"] == "untouched_test"].copy()
    columns = [
        "horizon_seconds",
        "policy",
        "trades",
        "total_net_pnl_usd",
        "mean_net_pnl_usd",
        "q20_net_pnl_usd",
        "profit_factor",
        "maximum_drawdown_usd",
        "day_block_mean_lower_95_usd",
    ]
    available_columns = [column for column in columns if column in final]
    table = markdown_table(final[available_columns])
    stress_table = (
        stress[
            [
                "fee_multiplier",
                "slippage_multiplier",
                "latency_ms",
                "capital_usd",
                "trades",
                "total_net_pnl_usd",
            ]
        ]
        .head(40)
        .pipe(markdown_table)
        if not stress.empty
        else "No selector trades cleared the frozen q20 reserve."
    )
    selected = summary["selected_policy"]
    text = f"""# {CAMPAIGN_ID}

Generated: {summary["generated_at_utc"]}

Status: **RESEARCH ONLY - NOT PROMOTABLE**

## Economic Verdict

- Total selector net PnL: `{selected.get("total_net_pnl_usd")}`
- Selector profit factor: `{selected.get("profit_factor")}`
- Selector q20 net PnL: `{selected.get("q20_net_pnl_usd")}`
- Day-block lower 95%: `{selected.get("day_block_mean_lower_95_usd")}`
- PBO diagnostic: `{selected.get("pbo")}`
- PBO interpretation: `{selected.get("pbo_scope")}`
- Deflated Sharpe diagnostic: `{selected.get("deflated_sharpe")}`

Promotion is blocked because the exact L2 archive covers one 24-hour window
with material receive gaps, there is no forward-paper period,
and the recorder's approximately five-second receive batches cannot distinguish
the frozen 100/250/500/1000 ms latency cells. Sub-second cells are retained as
requested but are marked non-independent.

## Untouched Chronological Test

{table}

`CURRENT_ENSEMBLE` is unavailable rather than synthesized because no current
ensemble prediction archive overlaps this exact L2 day.

## Stress Sample

{stress_table}

## Artifacts

- `decision_features.parquet`
- `primary_execution_labels.parquet`
- `latency_capacity_surface.parquet`
- `surface_metrics.csv`
- `model_predictions.parquet`
- `policy_trades.csv`
- `policy_metrics.csv`
- `stress_capacity.csv`
- `summary.json`

No paper/live policy, production model, or Polymarket campaign was modified.
"""
    (campaign_dir / "REPORT.md").write_text(text, encoding="utf-8")
