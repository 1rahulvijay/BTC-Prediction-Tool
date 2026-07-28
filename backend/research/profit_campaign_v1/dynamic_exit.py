"""BINANCE_DYNAMIC_EXIT_V1 on an identical causal entry cohort."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .contracts import Protocol, implementation_sha256, sha256_file
from .execution import ExecutablePath, build_executable_path, load_funding_events
from .features import FEATURE_COLUMNS, build_causal_features, decision_rows
from .models import ProbabilityHead, QuantileHead
from .validation import (
    TrialRegistry,
    chronological_splits,
    deflated_sharpe_from_trials,
    economic_metrics,
    finite_json,
    markdown_table,
    pbo_from_fold_policy_returns,
)


CAMPAIGN_ID = "BINANCE_DYNAMIC_EXIT_V1"
EXIT_STATE_COLUMNS = FEATURE_COLUMNS + (
    "action_sign",
    "elapsed_seconds",
    "remaining_seconds",
    "current_net_pnl_usd",
    "peak_net_pnl_usd",
    "drawdown_from_peak_usd",
    "entry_to_current_bps",
)


def _log(message: str, logger: Callable[[str], None] | None) -> None:
    if logger is not None:
        logger(f"[{CAMPAIGN_ID}] {message}")


def _funding_path(input_paths: dict[str, Path]) -> Path | None:
    root = next(iter(input_paths.values())).parents[1]
    candidates = list(root.glob("raw/funding_rates/symbol=BTCUSDT/**/*.parquet"))
    return candidates[0] if candidates else None


def _path_id(path: ExecutablePath) -> str:
    payload = (
        f"{path.decision_ts_ns}:{path.entry_ts_ns}:{path.action}:"
        f"{path.horizon_seconds}:{path.capital_usd}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _build_entry_paths(
    books: pd.DataFrame,
    features: pd.DataFrame,
    decisions: pd.DataFrame,
    protocol: Protocol,
    funding_events: pd.DataFrame,
    *,
    logger: Callable[[str], None] | None,
) -> list[ExecutablePath]:
    execution = protocol.raw["execution"]
    receive = books["receive_ts_ns"].to_numpy(np.int64)
    mids = books["mid"].to_numpy(float)
    paths = []
    for horizon in protocol.horizons:
        available_after = -1
        horizon_count = 0
        for decision in decisions.itertuples(index=False):
            decision_ts = int(decision.receive_ts_ns)
            if decision_ts < available_after:
                continue
            action = "LONG" if float(decision.ret_30s_bps) >= 0 else "SHORT"
            path = build_executable_path(
                books,
                decision_ts_ns=decision_ts,
                action=action,
                horizon_seconds=horizon,
                latency_ms=protocol.primary_latency_ms,
                capital_usd=protocol.primary_capital_usd,
                fee_bps=protocol.fee_bps,
                impact_bps=protocol.impact_bps,
                signal_expiry_ms=int(execution["signal_expiry_ms"]),
                minimum_fill_fraction=float(execution["minimum_fill_fraction"]),
                maximum_book_age_ms=int(
                    execution["maximum_book_age_ms"]
                ),
                funding_events=funding_events,
                receive_timestamps=receive,
                mid_prices=mids,
            )
            if path is None:
                continue
            paths.append(path)
            available_after = path.timestamps_ns[-1]
            horizon_count += 1
        _log(f"horizon={horizon}s entry paths={horizon_count}", logger)
    return paths


def _future_position(
    timestamps: tuple[int, ...],
    current: int,
    wait_seconds: int,
) -> int | None:
    target = timestamps[current] + wait_seconds * 1_000_000_000
    index = int(np.searchsorted(np.asarray(timestamps), target, side="left"))
    return index if index < len(timestamps) else None


def _state_rows(
    paths: list[ExecutablePath],
    features: pd.DataFrame,
    protocol: Protocol,
) -> pd.DataFrame:
    rows = []
    exit_config = protocol.raw["dynamic_exit"]
    waits = tuple(int(value) for value in exit_config["incremental_wait_seconds"])
    minimum_hold = float(exit_config["minimum_hold_seconds"])
    target_usd = (
        protocol.primary_capital_usd
        * float(exit_config["target_net_bps"])
        / 10_000.0
    )
    stop_usd = (
        -protocol.primary_capital_usd
        * float(exit_config["stop_net_bps"])
        / 10_000.0
    )
    for path in paths:
        identifier = _path_id(path)
        action_sign = 1.0 if path.action == "LONG" else -1.0
        for position, (book_index, timestamp, current_net) in enumerate(
            zip(path.book_indices, path.timestamps_ns, path.net_pnl_usd)
        ):
            elapsed = (timestamp - path.entry_ts_ns) / 1_000_000_000
            if elapsed < minimum_hold:
                continue
            peak = max(path.net_pnl_usd[: position + 1])
            base = features.iloc[book_index]
            row = {
                "path_id": identifier,
                "path_position": position,
                "decision_ts_ns": path.decision_ts_ns,
                "entry_ts_ns": path.entry_ts_ns,
                "receive_ts_ns": timestamp,
                "horizon_seconds": path.horizon_seconds,
                "action": path.action,
                "capital_usd": path.capital_usd,
                "action_sign": action_sign,
                "elapsed_seconds": elapsed,
                "remaining_seconds": max(0.0, path.horizon_seconds - elapsed),
                "current_net_pnl_usd": current_net,
                "peak_net_pnl_usd": peak,
                "drawdown_from_peak_usd": peak - current_net,
                "entry_to_current_bps": (
                    action_sign
                    * (float(base["mid"]) / path.entry_mid - 1.0)
                    * 10_000.0
                ),
                "target_exit_slippage_bps": path.exit_slippage_bps[position],
            }
            for name in FEATURE_COLUMNS:
                row[name] = float(base[name])
            for wait in waits:
                future = _future_position(path.timestamps_ns, position, wait)
                row[f"target_incremental_{wait}s_usd"] = (
                    path.net_pnl_usd[future] - current_net
                    if future is not None
                    else np.nan
                )
            future_30 = _future_position(path.timestamps_ns, position, 30)
            row["target_profit_disappears_30s"] = int(
                current_net > 0
                and future_30 is not None
                and path.net_pnl_usd[future_30] <= 0
            ) if future_30 is not None else np.nan
            remaining_path = path.net_pnl_usd[position:]
            barrier = None
            for value in remaining_path:
                if value >= target_usd:
                    barrier = 0
                    break
                if value <= stop_usd:
                    barrier = 1
                    break
            row["target_stop_before_target"] = int(barrier == 1)
            if future_30 is None:
                row["target_regime_changes_30s"] = np.nan
            else:
                current_regime = float(base["rv_30s_bps"]) > float(
                    base["rv_180s_bps"]
                )
                future_base = features.iloc[path.book_indices[future_30]]
                future_regime = float(future_base["rv_30s_bps"]) > float(
                    future_base["rv_180s_bps"]
                )
                row["target_regime_changes_30s"] = int(
                    current_regime != future_regime
                )
            rows.append(row)
    frame = pd.DataFrame(rows)
    frame.replace([np.inf, -np.inf], np.nan, inplace=True)
    frame.dropna(subset=list(EXIT_STATE_COLUMNS), inplace=True)
    return frame.reset_index(drop=True)


def _fit_exit_heads(
    train: pd.DataFrame,
    test: pd.DataFrame,
    protocol: Protocol,
    *,
    seed: int,
) -> pd.DataFrame:
    matrix = train[list(EXIT_STATE_COLUMNS)].to_numpy(float)
    test_matrix = test[list(EXIT_STATE_COLUMNS)].to_numpy(float)
    waits = tuple(
        int(value)
        for value in protocol.raw["dynamic_exit"]["incremental_wait_seconds"]
    )
    output = test[
        [
            "path_id",
            "path_position",
            "decision_ts_ns",
            "receive_ts_ns",
            "horizon_seconds",
            "action",
            "current_net_pnl_usd",
            "elapsed_seconds",
            "remaining_seconds",
        ]
    ].copy()
    for wait in waits:
        target_name = f"target_incremental_{wait}s_usd"
        observed = train[target_name].notna().to_numpy()
        if observed.sum() == 0:
            for quantile in (20, 50, 80):
                output[f"q{quantile:02d}_incremental_pnl_{wait}s"] = np.nan
            output[f"p_profit_improves_{wait}s"] = np.nan
            continue
        if observed.sum() < 50:
            raise ValueError(
                f"insufficient {wait}s incremental-exit targets"
            )
        quantile_head = QuantileHead.fit(
            matrix[observed],
            train.loc[observed, target_name].to_numpy(float),
            quantiles=(0.20, 0.50, 0.80),
            seed=seed + wait,
        )
        probability_head = ProbabilityHead.fit(
            matrix[observed],
            train.loc[observed, target_name].to_numpy(float) > 0,
            seed=seed + 1_000 + wait,
        )
        valid_test = (
            test["remaining_seconds"].to_numpy(float) >= float(wait)
        )
        quantiles = quantile_head.predict(test_matrix)
        for quantile, values in quantiles.items():
            values = np.asarray(values, dtype=float)
            values[~valid_test] = np.nan
            output[
                f"q{int(quantile * 100):02d}_incremental_pnl_{wait}s"
            ] = values
        probability = probability_head.predict(test_matrix)
        probability[~valid_test] = np.nan
        output[f"p_profit_improves_{wait}s"] = probability
    for target_name in (
        "target_profit_disappears_30s",
        "target_stop_before_target",
        "target_regime_changes_30s",
    ):
        observed = train[target_name].notna().to_numpy()
        output_name = f"p_{target_name.removeprefix('target_')}"
        if observed.sum() == 0:
            output[output_name] = np.nan
            continue
        if observed.sum() < 50:
            raise ValueError(f"insufficient observed rows for {target_name}")
        head = ProbabilityHead.fit(
            matrix[observed],
            train.loc[observed, target_name].to_numpy(int),
            seed=seed + sum(ord(char) for char in target_name),
        )
        probability = head.predict(test_matrix)
        if target_name != "target_stop_before_target":
            probability[
                test["remaining_seconds"].to_numpy(float) < 30.0
            ] = np.nan
        output[output_name] = probability
    slippage = QuantileHead.fit(
        matrix,
        train["target_exit_slippage_bps"].to_numpy(float),
        quantiles=(0.20, 0.50, 0.80),
        seed=seed + 7_000,
    ).predict(test_matrix)
    for quantile, values in slippage.items():
        output[f"exit_slippage_q{int(quantile * 100):02d}_bps"] = values
    return output


def _exit_choice(
    path: ExecutablePath,
    policy: str,
    prediction_rows: pd.DataFrame | None,
    protocol: Protocol,
    features: pd.DataFrame,
) -> tuple[float, int, str, int | None]:
    config = protocol.raw["dynamic_exit"]
    pnl = np.asarray(path.net_pnl_usd, dtype=float)
    target = path.capital_usd * float(config["target_net_bps"]) / 10_000.0
    stop = -path.capital_usd * float(config["stop_net_bps"]) / 10_000.0
    index = len(pnl) - 1
    reason = "maximum_hold"
    if policy == "STATIC_STOP_TARGET":
        for candidate, value in enumerate(pnl):
            if value >= target or value <= stop:
                index = candidate
                reason = "target" if value >= target else "stop"
                break
    elif policy == "MAXIMUM_HOLD":
        pass
    elif policy == "TRAILING_STOP":
        peak = -np.inf
        drawdown = path.capital_usd * float(
            config["trailing_drawdown_bps"]
        ) / 10_000.0
        for candidate, value in enumerate(pnl):
            peak = max(peak, value)
            if peak > 0 and peak - value >= drawdown:
                index = candidate
                reason = "trailing_stop"
                break
    elif policy == "BREAK_EVEN_STOP":
        activation = path.capital_usd * float(
            config["break_even_activation_bps"]
        ) / 10_000.0
        active = False
        for candidate, value in enumerate(pnl):
            active = active or value >= activation
            if active and value <= 0:
                index = candidate
                reason = "break_even"
                break
    elif policy == "PROFIT_LOCK":
        activation = path.capital_usd * float(
            config["profit_lock_activation_bps"]
        ) / 10_000.0
        floor = path.capital_usd * float(
            config["profit_lock_floor_bps"]
        ) / 10_000.0
        active = False
        for candidate, value in enumerate(pnl):
            active = active or value >= activation
            if active and value <= floor:
                index = candidate
                reason = "profit_lock"
                break
    elif policy in ("MODEL_INCREMENTAL_EV", "MODEL_PARTIAL_EXIT"):
        if prediction_rows is None or prediction_rows.empty:
            return float(pnl[-1]), len(pnl) - 1, "model_unavailable", None
        reserve = (
            path.capital_usd
            * float(protocol.raw["selector"]["uncertainty_reserve_bps"])
            / 10_000.0
        )
        waits = tuple(
            int(value) for value in config["incremental_wait_seconds"]
        )
        for row in prediction_rows.sort_values("path_position").itertuples(
            index=False
        ):
            candidate = int(row.path_position)
            if candidate >= len(pnl):
                continue
            q20_values = [
                float(getattr(row, f"q20_incremental_pnl_{wait}s"))
                for wait in waits
                if float(row.remaining_seconds) >= float(wait)
                and np.isfinite(
                    float(getattr(row, f"q20_incremental_pnl_{wait}s"))
                )
            ]
            if not q20_values:
                continue
            q20 = max(q20_values)
            if q20 <= reserve:
                index = candidate
                reason = "model_close"
                break
        if policy == "MODEL_PARTIAL_EXIT" and index < len(pnl) - 1:
            return (
                float(0.5 * pnl[index] + 0.5 * pnl[-1]),
                len(pnl) - 1,
                "model_half_exit_then_hold",
                index,
            )
    elif policy == "OPPOSING_SIGNAL":
        for candidate, book_index in enumerate(path.book_indices):
            momentum = float(features.iloc[book_index]["ret_30s_bps"])
            if (path.action == "LONG" and momentum < 0) or (
                path.action == "SHORT" and momentum > 0
            ):
                index = candidate
                reason = "opposing_momentum"
                break
    else:
        raise ValueError(f"unknown exit policy: {policy}")
    return float(pnl[index]), index, reason, None


def _evaluate_exit_policies(
    paths: list[ExecutablePath],
    prediction_frame: pd.DataFrame,
    protocol: Protocol,
    features: pd.DataFrame,
    *,
    role: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policies = (
        "STATIC_STOP_TARGET",
        "MAXIMUM_HOLD",
        "TRAILING_STOP",
        "BREAK_EVEN_STOP",
        "PROFIT_LOCK",
        "MODEL_INCREMENTAL_EV",
        "MODEL_PARTIAL_EXIT",
        "OPPOSING_SIGNAL",
    )
    trades = []
    for path in paths:
        identifier = _path_id(path)
        predictions = prediction_frame[
            prediction_frame["path_id"] == identifier
        ]
        for policy in policies:
            net, index, reason, partial_index = _exit_choice(
                path, policy, predictions, protocol, features
            )
            if partial_index is None:
                fee = path.entry_fee_usd + path.exit_fee_usd[index]
                impact = (
                    path.entry_impact_reserve_usd
                    + path.exit_impact_reserve_usd[index]
                )
                funding = path.funding_usd[index]
            else:
                fee = (
                    path.entry_fee_usd
                    + 0.5 * path.exit_fee_usd[partial_index]
                    + 0.5 * path.exit_fee_usd[-1]
                )
                impact = (
                    path.entry_impact_reserve_usd
                    + 0.5 * path.exit_impact_reserve_usd[partial_index]
                    + 0.5 * path.exit_impact_reserve_usd[-1]
                )
                funding = (
                    0.5 * path.funding_usd[partial_index]
                    + 0.5 * path.funding_usd[-1]
                )
            trades.append(
                {
                    "role": role,
                    "policy": policy,
                    "path_id": identifier,
                    "decision_ts_ns": path.decision_ts_ns,
                    "entry_ts_ns": path.entry_ts_ns,
                    "exit_ts_ns": path.timestamps_ns[index],
                    "horizon_seconds": path.horizon_seconds,
                    "action": path.action,
                    "capital_usd": path.capital_usd,
                    "net_pnl_usd": net,
                    "gross_pnl_usd": net + fee + impact - funding,
                    "fee_usd": fee,
                    "impact_reserve_usd": impact,
                    "funding_usd": funding,
                    "fill_fraction": (
                        path.filled_quantity / path.requested_quantity
                    ),
                    "status": (
                        "FILLED"
                        if path.filled_quantity
                        >= path.requested_quantity - 1e-9
                        else "PARTIAL"
                    ),
                    "observed_holding_seconds": (
                        path.timestamps_ns[index] - path.entry_ts_ns
                    )
                    / 1_000_000_000,
                    "exit_reason": reason,
                }
            )
    trade_frame = pd.DataFrame(trades)
    metrics = []
    for (horizon, policy), group in trade_frame.groupby(
        ["horizon_seconds", "policy"]
    ):
        metrics.append(
            {
                "role": role,
                "horizon_seconds": int(horizon),
                "policy": policy,
                **economic_metrics(group),
            }
        )
    return trade_frame, pd.DataFrame(metrics)


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
    del force
    campaign_dir = run_dir / CAMPAIGN_ID
    campaign_dir.mkdir(parents=True, exist_ok=True)
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
    funding_path = _funding_path(input_paths)
    funding_events = load_funding_events(
        str(funding_path) if funding_path is not None else None
    )
    paths = _build_entry_paths(
        books,
        features,
        decisions,
        protocol,
        funding_events,
        logger=logger,
    )
    state = _state_rows(paths, features, protocol)
    state.to_parquet(campaign_dir / "exit_state_targets.parquet", index=False)
    _log(f"exit states={len(state):,}; fitting incremental-EV heads", logger)

    all_predictions = []
    all_trades = []
    all_metrics = []
    fold_returns = []
    trial_sharpes = []
    final_trades = []
    for horizon in protocol.horizons:
        horizon_paths = sorted(
            [path for path in paths if path.horizon_seconds == horizon],
            key=lambda path: path.decision_ts_ns,
        )
        path_timestamps = np.asarray(
            [path.decision_ts_ns for path in horizon_paths], dtype=np.int64
        )
        splits, final_split = chronological_splits(
            path_timestamps,
            development_fraction=float(
                protocol.raw["validation"]["development_fraction"]
            ),
            folds=int(protocol.raw["validation"]["walk_forward_folds"]),
            purge_seconds=int(protocol.raw["validation"]["purge_seconds"]),
            embargo_seconds=int(protocol.raw["validation"]["embargo_seconds"]),
        )
        horizon_fold_returns = []
        for fold_index, split in enumerate(splits):
            train_ids = {_path_id(horizon_paths[index]) for index in split.train_indices}
            test_paths = [horizon_paths[index] for index in split.test_indices]
            test_ids = {_path_id(path) for path in test_paths}
            train_state = state[state["path_id"].isin(train_ids)]
            test_state = state[state["path_id"].isin(test_ids)]
            prediction = _fit_exit_heads(
                train_state,
                test_state,
                protocol,
                seed=protocol.random_seed + horizon * 100 + fold_index,
            )
            prediction["role"] = split.role
            all_predictions.append(prediction)
            trades, metrics = _evaluate_exit_policies(
                test_paths,
                prediction,
                protocol,
                features,
                role=split.role,
            )
            all_trades.append(trades)
            all_metrics.append(metrics)
            fold_values = []
            for policy in metrics["policy"]:
                row = metrics[metrics["policy"] == policy].iloc[0]
                fold_values.append(float(row["mean_net_pnl_usd"]))
                trial_sharpes.append(float(row["sample_sharpe"]))
            horizon_fold_returns.append(fold_values)
        fold_returns.extend(horizon_fold_returns)

        train_ids = {
            _path_id(horizon_paths[index]) for index in final_split.train_indices
        }
        test_paths = [horizon_paths[index] for index in final_split.test_indices]
        test_ids = {_path_id(path) for path in test_paths}
        final_prediction = _fit_exit_heads(
            state[state["path_id"].isin(train_ids)],
            state[state["path_id"].isin(test_ids)],
            protocol,
            seed=protocol.random_seed + horizon * 100 + 99,
        )
        final_prediction["role"] = "untouched_test"
        all_predictions.append(final_prediction)
        trades, metrics = _evaluate_exit_policies(
            test_paths,
            final_prediction,
            protocol,
            features,
            role="untouched_test",
        )
        all_trades.append(trades)
        final_trades.append(trades)
        all_metrics.append(metrics)

    predictions = pd.concat(all_predictions, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True)
    metrics = pd.concat(all_metrics, ignore_index=True)
    predictions.to_parquet(campaign_dir / "exit_model_predictions.parquet", index=False)
    trades.to_csv(campaign_dir / "exit_policy_trades.csv", index=False)
    metrics.to_csv(campaign_dir / "exit_policy_metrics.csv", index=False)

    final = pd.concat(final_trades, ignore_index=True)
    model_final = final[final["policy"] == "MODEL_INCREMENTAL_EV"]
    model_metrics = economic_metrics(model_final)
    model_metrics["pbo"] = pbo_from_fold_policy_returns(fold_returns)
    model_metrics["pbo_scope"] = (
        "exit-policy family on one gapped 24-hour archive window; useful as an "
        "overfitting diagnostic, not independent multi-day evidence"
    )
    model_metrics["deflated_sharpe"] = deflated_sharpe_from_trials(
        model_final["net_pnl_usd"].tolist(), trial_sharpes
    )
    hold_final = final[final["policy"] == "MAXIMUM_HOLD"]
    model_metrics["paired_pnl_delta_vs_hold_usd"] = float(
        model_final["net_pnl_usd"].sum() - hold_final["net_pnl_usd"].sum()
    )
    model_metrics["promotion_status"] = "RESEARCH_ONLY_INSUFFICIENT_EVIDENCE"
    model_metrics["promotion_blockers"] = [
        "one gapped approximately 24-hour exact L2 archive window only",
        "no forward-paper exit evidence",
        "entry cohort and exit states are dependent within day",
        "sub-second latency unavailable from five-second receive batches",
    ]

    source_hashes = [
        sha256_file(input_paths["normalized_books"]),
        sha256_file(input_paths["trade_flow"]),
    ]
    if funding_path is not None:
        source_hashes.append(sha256_file(funding_path))
    dataset_hash = hashlib.sha256(
        "".join(source_hashes).encode("ascii")
    ).hexdigest()
    code_sha256 = implementation_sha256()
    registry = TrialRegistry(
        run_dir / "trial_registry.jsonl",
        protocol.sha256,
        code_sha256,
    )
    for row in metrics.to_dict("records"):
        registry.append(
            campaign_id=CAMPAIGN_ID,
            family=str(row["policy"]),
            parameters={
                "role": row["role"],
                "horizon_seconds": row["horizon_seconds"],
                "entry_cohort": protocol.raw["dynamic_exit"]["entry_cohort"],
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
        "entry_paths": len(paths),
        "exit_state_rows": len(state),
        "model_incremental_ev": finite_json(model_metrics),
        "production_permissions_changed": False,
    }
    (campaign_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_report(campaign_dir, summary, metrics)
    return summary


def _write_report(
    campaign_dir: Path,
    summary: dict[str, Any],
    metrics: pd.DataFrame,
) -> None:
    final = metrics[metrics["role"] == "untouched_test"]
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
    table = markdown_table(
        final[[column for column in columns if column in final]]
    )
    model = summary["model_incremental_ev"]
    text = f"""# {CAMPAIGN_ID}

Generated: {summary["generated_at_utc"]}

Status: **RESEARCH ONLY - NOT PROMOTABLE**

All eight policies were evaluated on the same frozen causal momentum entry
cohort. Early exits do not create replacement entries, so policy comparisons do
not gain extra opportunities from optional stopping.

## Economic Verdict

- Model incremental-EV total net PnL: `{model.get("total_net_pnl_usd")}`
- Profit factor: `{model.get("profit_factor")}`
- q20 net PnL: `{model.get("q20_net_pnl_usd")}`
- Paired total delta versus maximum hold:
  `{model.get("paired_pnl_delta_vs_hold_usd")}`
- PBO diagnostic: `{model.get("pbo")}`
- PBO interpretation: `{model.get("pbo_scope")}`
- Deflated Sharpe diagnostic: `{model.get("deflated_sharpe")}`

## Untouched Chronological Test

{table}

## Model Outputs

At each observed exit state the research model emits incremental-net-PnL
q20/q50/q80 and P(profit improves) only for 5/15/30/60-second waits that remain
fully observable before the frozen maximum hold. Inapplicable wait horizons are
stored as null rather than shortened to expiry. It also emits P(current profit
disappears), P(stop before target), P(volatility-state changes), and exit-
slippage q20/q50/q80 where their target is observable. The archive is received
in approximately five-second batches, so the phrase "every second" is not
claimed for this historical run.

## Artifacts

- `exit_state_targets.parquet`
- `exit_model_predictions.parquet`
- `exit_policy_trades.csv`
- `exit_policy_metrics.csv`
- `summary.json`

No production, paper, or live exit behavior was changed.
"""
    (campaign_dir / "REPORT.md").write_text(text, encoding="utf-8")
