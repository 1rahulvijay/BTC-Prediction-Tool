"""Run the executable questions from the August 13 action-value research briefs.

This is a standalone research lane. It imports no serving code and writes only a new,
timestamped JSON result. Existing reports and result files are never replaced.

Protocol frozen before the run:

* chronological 60% train / 10% calibration / 30% untouched test by UTC day;
* a 30-minute purge at both boundaries;
* causal, backward-looking features only;
* 12 bps Binance round-trip cost;
* thresholds learned on calibration data and frozen before test;
* UTC-day block-bootstrap intervals with a family correction;
* no configuration is promotable unless its after-cost lower bound is above zero.

The batch answers seven questions that the current data can answer honestly:

1. What is the OOS value of LONG, SHORT and WAIT?
2. Does a movement gate improve directional economics?
3. Is waiting 1/3/5 minutes better than entering now?
4. How long does a selected thesis remain favorable?
5. After an adverse/favorable move, does a causal HOLD/EXIT/REVERSE policy help?
6. Can a breakout continuation/failure model clear costs?
7. Does minute-resolution spot/perpetual leadership produce a catch-up edge?

Sub-second execution, maker fills, liquidation continuation, open-interest transitions and
multi-venue information leadership remain blocked by missing event-time data. This script
does not fabricate those inputs from one-minute candles.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score


LANES = Path(__file__).resolve().parent
ROOT = LANES.parent
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"
ROUND_MATRIX = ROOT / "data" / "research" / "binance_updown_features.parquet"
RESULTS_DIR = LANES / "results"

COST_BPS = 12.0
HORIZONS = (5, 15, 30)
PURGE_MINUTES = 30
FAMILY_SIZE = 64
FAMILY_ALPHA = 0.05 / FAMILY_SIZE
BOOTSTRAPS = 8_000
SEED = 20260813

FEATURES = [
    "rv_15m", "rv_30m", "rv_60m", "rv_term", "count_accel_5m", "vol_accel",
    "vpin_15m", "vpin_30m", "vpin_50m", "compression_ratio", "range_15m",
    "shock_magnitude", "micro_range_15m", "cvd_change", "cvd_1m", "cvd_5m",
    "delta", "vpin", "large_trade_delta", "large_trade_imbalance",
    "funding_velocity", "cvd_spot", "cvd_perp", "cvd_divergence",
    "perp_spot_basis_bps", "vol_spot", "vol_perp",
]


def _forward_return(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full(len(close), np.nan, dtype=float)
    out[:-horizon] = (close[horizon:] / close[:-horizon] - 1.0) * 10_000.0
    return out


def _shifted_return(close: np.ndarray, start: int, end: int) -> np.ndarray:
    """Return from t+start to t+end, aligned at t."""
    out = np.full(len(close), np.nan, dtype=float)
    if end <= start:
        return out
    out[: -end] = (close[end:] / close[start : len(close) - end + start] - 1.0) * 10_000.0
    return out


def _day_interval(values: np.ndarray, days: np.ndarray, *, seed: int) -> dict:
    frame = pd.DataFrame({"value": np.asarray(values, float), "day": np.asarray(days)})
    frame = frame[np.isfinite(frame["value"])]
    if frame.empty:
        return {"point": None, "lcb": None, "ucb": None, "n": 0, "n_days": 0}
    daily = frame.groupby("day", sort=False)["value"].mean().to_numpy(float)
    rng = np.random.default_rng(seed)
    stats = np.empty(BOOTSTRAPS, dtype=float)
    for position in range(BOOTSTRAPS):
        stats[position] = rng.choice(daily, size=len(daily), replace=True).mean()
    return {
        "point": float(daily.mean()),
        "lcb": float(np.quantile(stats, FAMILY_ALPHA / 2.0)),
        "ucb": float(np.quantile(stats, 1.0 - FAMILY_ALPHA / 2.0)),
        "n": int(len(frame)),
        "n_days": int(len(daily)),
    }


def _score_policy(
    gross: np.ndarray,
    mask: np.ndarray,
    days: np.ndarray,
    *,
    seed: int,
    cost_bps: float = COST_BPS,
) -> dict:
    valid = np.asarray(mask, bool) & np.isfinite(gross)
    net = np.asarray(gross, float)[valid] - cost_bps
    interval = _day_interval(net, days[valid], seed=seed)
    return {
        **interval,
        "gross_bps": float(np.mean(np.asarray(gross)[valid])) if valid.any() else None,
        "win_rate": float((net > 0).mean()) if valid.any() else None,
        "promotable": bool(interval["lcb"] is not None and interval["lcb"] > 0.0),
    }


def _sample_indices(mask: np.ndarray, maximum: int = 220_000) -> np.ndarray:
    idx = np.flatnonzero(mask)
    if len(idx) <= maximum:
        return idx
    return idx[np.linspace(0, len(idx) - 1, maximum, dtype=int)]


def _fit_regressor(x: np.ndarray, y: np.ndarray, mask: np.ndarray, seed: int):
    idx = _sample_indices(mask & np.isfinite(y))
    model = HistGradientBoostingRegressor(
        max_iter=160,
        learning_rate=0.055,
        max_leaf_nodes=15,
        l2_regularization=2.0,
        random_state=seed,
    )
    model.fit(x[idx], y[idx])
    return model


def _fit_classifier(x: np.ndarray, y: np.ndarray, mask: np.ndarray, seed: int):
    idx = _sample_indices(mask & np.isfinite(y))
    model = HistGradientBoostingClassifier(
        max_iter=160,
        learning_rate=0.055,
        max_leaf_nodes=15,
        l2_regularization=2.0,
        random_state=seed,
    )
    model.fit(x[idx], y[idx].astype(int))
    return model


def _split(days: np.ndarray) -> dict:
    unique = np.sort(np.unique(days))
    train_end = unique[int(len(unique) * 0.60)]
    calibration_end = unique[int(len(unique) * 0.70)]
    train = days < train_end
    calibration = (days >= train_end) & (days < calibration_end)
    test = days >= calibration_end

    # Purge rows whose forward labels can overlap an adjacent split.
    for boundary in (train_end, calibration_end):
        boundary_row = np.flatnonzero(days >= boundary)
        if len(boundary_row):
            lo = max(0, int(boundary_row[0]) - PURGE_MINUTES)
            hi = min(len(days), int(boundary_row[0]) + PURGE_MINUTES)
            train[lo:hi] = False
            calibration[lo:hi] = False
            test[lo:hi] = False
    return {
        "train": train,
        "calibration": calibration,
        "test": test,
        "train_end_day": int(train_end),
        "calibration_end_day": int(calibration_end),
    }


def _load_matrix(path: Path) -> pd.DataFrame:
    columns = ["ts_ms", "open", "high", "low", "close", *FEATURES]
    frame = pd.read_parquet(path, columns=columns)
    return frame.replace([np.inf, -np.inf], np.nan)


def _direct_and_movement_gate(
    frame: pd.DataFrame,
    split: dict,
) -> tuple[dict, dict[int, dict]]:
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    x = frame[FEATURES].to_numpy(float)
    rows = []
    state: dict[int, dict] = {}

    for position, horizon in enumerate(HORIZONS):
        target = _forward_return(close, horizon)
        tradeable = (np.abs(target) > COST_BPS).astype(float)
        tradeable[~np.isfinite(target)] = np.nan
        reg = _fit_regressor(x, target, split["train"], SEED + horizon)
        move = _fit_classifier(x, tradeable, split["train"], SEED + 100 + horizon)

        pred_cal = reg.predict(x[split["calibration"]])
        move_cal = move.predict_proba(x[split["calibration"]])[:, 1]
        direction_gate = float(np.quantile(np.abs(pred_cal), 0.95))
        movement_gate = float(np.quantile(move_cal, 0.90))

        pred = np.full(len(frame), np.nan)
        p_move = np.full(len(frame), np.nan)
        score_mask = split["calibration"] | split["test"]
        pred[score_mask] = reg.predict(x[score_mask])
        p_move[score_mask] = move.predict_proba(x[score_mask])[:, 1]
        side = np.sign(pred)
        signed_gross = side * target
        direct_mask = split["test"] & (np.abs(pred) >= direction_gate) & (side != 0)
        movement_mask = direct_mask & (p_move >= movement_gate)

        direct_score = _score_policy(
            signed_gross, direct_mask, days, seed=1000 + position
        )
        movement_score = _score_policy(
            signed_gross, movement_mask, days, seed=1010 + position
        )
        all_test = split["test"] & np.isfinite(target)
        auc = float(roc_auc_score(tradeable[all_test], p_move[all_test]))
        rows.append({
            "horizon_m": horizon,
            "direction_gate_abs_prediction_bps": direction_gate,
            "movement_gate_probability": movement_gate,
            "movement_auc": auc,
            "direct_long_calls": int((direct_mask & (side > 0)).sum()),
            "direct_short_calls": int((direct_mask & (side < 0)).sum()),
            "direct": direct_score,
            "movement_gated": movement_score,
        })
        state[horizon] = {
            "target": target,
            "prediction": pred,
            "p_move": p_move,
            "side": side,
            "direct_mask": direct_mask,
            "calibration_direct_mask": (
                split["calibration"] & (np.abs(pred) >= direction_gate) & (side != 0)
            ),
            "movement_mask": movement_mask,
        }

    promotable = sum(
        int(row[variant]["promotable"])
        for row in rows
        for variant in ("direct", "movement_gated")
    )
    return {
        "description": "Chronological HGB return model; WAIT unless calibration-frozen top-5% absolute prediction. Movement variant also requires top-decile P(|move|>12bps).",
        "rows": rows,
        "promotable_configurations": promotable,
    }, state


def _model_failure_gate(
    frame: pd.DataFrame,
    split: dict,
    state: dict[int, dict],
) -> dict:
    """Test whether a second model can identify when the base direction call is wrong.

    The 35-day calibration era is split chronologically: its first half trains the failure
    model and its second half freezes a top-decile trust threshold. The 109-day test remains
    untouched. This is historical research, not evidence that a live strategy is calibrated.
    """
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    x = frame[FEATURES].to_numpy(float)
    calibration_days = np.sort(np.unique(days[split["calibration"]]))
    meta_boundary = calibration_days[len(calibration_days) // 2]
    meta_train = split["calibration"] & (days < meta_boundary)
    meta_gate = split["calibration"] & (days >= meta_boundary)
    rows = []

    for position, horizon in enumerate(HORIZONS):
        base = state[horizon]
        side = base["side"]
        target = base["target"]
        correct = (side * target > 0).astype(float)
        correct[~np.isfinite(target) | ~np.isfinite(side) | (side == 0)] = np.nan
        meta_x = np.column_stack([
            x,
            base["prediction"],
            np.abs(base["prediction"]),
            base["p_move"],
            side,
        ])
        clf = _fit_classifier(meta_x, correct, meta_train, SEED + 250 + horizon)
        p_gate = clf.predict_proba(meta_x[meta_gate])[:, 1]
        trust_threshold = float(np.quantile(p_gate, 0.90))
        p_correct = np.full(len(frame), np.nan)
        p_correct[split["test"]] = clf.predict_proba(meta_x[split["test"]])[:, 1]
        selected = base["direct_mask"] & (p_correct >= trust_threshold)
        signed_gross = side * target
        score = _score_policy(signed_gross, selected, days, seed=2500 + position)
        valid_test = split["test"] & np.isfinite(correct)
        auc = float(roc_auc_score(correct[valid_test], p_correct[valid_test]))
        baseline = _score_policy(
            signed_gross, base["direct_mask"], days, seed=2510 + position
        )
        rows.append({
            "horizon_m": horizon,
            "test_failure_model_auc": auc,
            "trust_threshold": trust_threshold,
            "trusted_calls": score,
            "ungated_calls": baseline,
            "promotable": bool(score["promotable"]),
        })
    return {
        "description": "Meta-model predicts base-call correctness from a separate calibration-era split; historical only, not a live calibration claim.",
        "rows": rows,
        "promotable_configurations": int(sum(row["promotable"] for row in rows)),
    }


def _entry_delay(
    frame: pd.DataFrame,
    split: dict,
    state: dict[int, dict],
) -> dict:
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    rows = []
    for horizon in HORIZONS:
        selected = state[horizon]["direct_mask"]
        side = state[horizon]["side"]
        now = side * _forward_return(close, horizon) - COST_BPS
        for delay in (1, 3, 5):
            if delay >= horizon:
                continue
            delayed = side * _shifted_return(close, delay, horizon) - COST_BPS
            valid = selected & np.isfinite(now) & np.isfinite(delayed)
            difference = _day_interval(
                delayed[valid] - now[valid], days[valid],
                seed=2000 + horizon * 10 + delay,
            )
            rows.append({
                "horizon_m": horizon,
                "delay_m": delay,
                "now_net_bps": float(np.mean(now[valid])) if valid.any() else None,
                "delayed_net_bps": float(np.mean(delayed[valid])) if valid.any() else None,
                "delay_minus_now": difference,
                "delay_better_with_positive_lcb": bool(
                    difference["lcb"] is not None and difference["lcb"] > 0
                ),
            })
    return {
        "description": "Same frozen signal, deterministic 1/3/5-minute delay, same original exit clock and same round-trip cost.",
        "rows": rows,
        "promotable_configurations": int(sum(row["delay_better_with_positive_lcb"] for row in rows)),
    }


def _thesis_survival(frame: pd.DataFrame, state: dict[int, dict]) -> dict:
    close = frame["close"].to_numpy(float)
    rows = []
    for horizon in HORIZONS:
        selected_idx = np.flatnonzero(state[horizon]["direct_mask"])
        selected_idx = selected_idx[selected_idx + horizon < len(close)]
        side = state[horizon]["side"][selected_idx]
        entry = close[selected_idx]
        checkpoint_rows = []
        paths = []
        for minute in range(1, horizon + 1):
            pnl = side * (close[selected_idx + minute] / entry - 1.0) * 10_000.0
            paths.append(pnl)
            checkpoint_rows.append({
                "minute": minute,
                "favorable_rate": float((pnl > 0).mean()) if len(pnl) else None,
                "above_cost_rate": float((pnl > COST_BPS).mean()) if len(pnl) else None,
                "mean_signed_bps": float(np.mean(pnl)) if len(pnl) else None,
            })
        path = np.column_stack(paths) if paths else np.empty((0, horizon))
        first_failure = np.full(len(selected_idx), horizon + 1, dtype=int)
        for minute in range(horizon):
            newly = (first_failure == horizon + 1) & (path[:, minute] <= -5.0)
            first_failure[newly] = minute + 1
        rows.append({
            "horizon_m": horizon,
            "n_calls": int(len(selected_idx)),
            "median_first_minus5bps_or_timeout_m": float(np.median(first_failure)) if len(first_failure) else None,
            "never_hit_minus5bps_rate": float((first_failure == horizon + 1).mean()) if len(first_failure) else None,
            "checkpoints": checkpoint_rows,
            "authority": "DIAGNOSTIC_ONLY",
        })
    return {
        "description": "Observed close-to-close survival of test calls; descriptive only and not an exit rule.",
        "rows": rows,
        "promotable_configurations": 0,
    }


def _position_management(
    frame: pd.DataFrame,
    split: dict,
    state: dict[int, dict],
) -> dict:
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    x = frame[FEATURES].to_numpy(float)
    rows = []

    for horizon in (15, 30):
        checkpoint = 3 if horizon == 15 else 5
        base = state[horizon]
        side = base["side"]
        entry_to_checkpoint = side * _shifted_return(close, 0, checkpoint)
        checkpoint_to_end = side * _shifted_return(close, checkpoint, horizon)

        # The checkpoint policy is trained only on calibration-era model calls. Features are
        # market state at t+checkpoint plus the original side and realized PnL to checkpoint.
        valid_rows = np.arange(len(frame)) + checkpoint < len(frame)
        x_checkpoint = np.full_like(x, np.nan)
        x_checkpoint[valid_rows] = x[np.arange(len(frame))[valid_rows] + checkpoint]
        policy_x = np.column_stack([x_checkpoint, side, entry_to_checkpoint])
        calibration_calls = split["calibration"] & np.isfinite(side) & (side != 0)
        calibration_calls &= np.isfinite(checkpoint_to_end)
        policy = _fit_regressor(
            policy_x, checkpoint_to_end, calibration_calls, SEED + 300 + horizon
        )
        expected_remaining = np.full(len(frame), np.nan)
        test_calls = base["direct_mask"] & np.isfinite(checkpoint_to_end)
        expected_remaining[test_calls] = policy.predict(policy_x[test_calls])

        for condition, condition_mask in (
            ("ADVERSE_AT_CHECKPOINT", entry_to_checkpoint < 0),
            ("FAVORABLE_AT_CHECKPOINT", entry_to_checkpoint > 0),
        ):
            selected = test_calls & condition_mask
            hold = entry_to_checkpoint + checkpoint_to_end - COST_BPS
            exit_now = entry_to_checkpoint - COST_BPS
            reverse = entry_to_checkpoint - checkpoint_to_end - (2.0 * COST_BPS)
            action = np.full(len(frame), "EXIT", dtype=object)
            action[expected_remaining > 0.0] = "HOLD"
            action[expected_remaining < -COST_BPS] = "REVERSE"
            policy_value = np.where(
                action == "HOLD", hold, np.where(action == "REVERSE", reverse, exit_now)
            )
            policy_interval = _day_interval(policy_value[selected], days[selected], seed=3000 + horizon)
            hold_interval = _day_interval(hold[selected], days[selected], seed=3010 + horizon)
            exit_interval = _day_interval(exit_now[selected], days[selected], seed=3020 + horizon)

            calibration_selected = base["calibration_direct_mask"] & condition_mask
            calibration_hold = float(np.nanmean(hold[calibration_selected]))
            calibration_exit = float(np.nanmean(exit_now[calibration_selected]))
            if calibration_hold >= calibration_exit:
                baseline_name = "ALWAYS_HOLD"
                baseline_value = hold
            else:
                baseline_name = "ALWAYS_EXIT"
                baseline_value = exit_now
            baseline_interval = _day_interval(
                baseline_value[selected], days[selected], seed=3025 + horizon
            )
            lift = _day_interval(
                policy_value[selected] - baseline_value[selected],
                days[selected],
                seed=3030 + horizon,
            )
            action_counts = {
                name: int((selected & (action == name)).sum())
                for name in ("HOLD", "EXIT", "REVERSE")
            }
            rows.append({
                "horizon_m": horizon,
                "checkpoint_m": checkpoint,
                "condition": condition,
                "n": int(selected.sum()),
                "action_counts": action_counts,
                "policy_net": policy_interval,
                "always_hold_net": hold_interval,
                "always_exit_net": exit_interval,
                "calibration_selected_static": baseline_name,
                "selected_static_net": baseline_interval,
                "policy_minus_calibration_selected_static": lift,
                "promotable": bool(
                    policy_interval["lcb"] is not None
                    and policy_interval["lcb"] > 0
                    and lift["lcb"] is not None
                    and lift["lcb"] > 0
                ),
            })
    return {
        "description": "Causal checkpoint model chooses HOLD/EXIT/REVERSE. Reverse pays a second 12bps round trip; promotion also requires beating the better static policy.",
        "rows": rows,
        "promotable_configurations": int(sum(row["promotable"] for row in rows)),
    }


def _breakout(frame: pd.DataFrame, split: dict) -> dict:
    close = frame["close"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    x = frame[FEATURES].to_numpy(float)
    trailing_high = pd.Series(high).shift(1).rolling(60, min_periods=60).max().to_numpy()
    trailing_low = pd.Series(low).shift(1).rolling(60, min_periods=60).min().to_numpy()
    up = close > trailing_high * 1.0002
    down = close < trailing_low * 0.9998
    side = np.where(up, 1.0, np.where(down, -1.0, 0.0))
    rows = []

    for horizon in (5, 15, 30):
        target = side * _forward_return(close, horizon)
        continuation = (target > 0).astype(float)
        continuation[~np.isfinite(target) | (side == 0)] = np.nan
        train_events = split["train"] & (side != 0) & np.isfinite(target)
        calibration_events = split["calibration"] & (side != 0) & np.isfinite(target)
        test_events = split["test"] & (side != 0) & np.isfinite(target)
        if train_events.sum() < 500 or test_events.sum() < 200:
            rows.append({"horizon_m": horizon, "status": "INSUFFICIENT_SAMPLE", "n_test": int(test_events.sum())})
            continue
        clf = _fit_classifier(x, continuation, train_events, SEED + 400 + horizon)
        p_cal = clf.predict_proba(x[calibration_events])[:, 1]
        gate = float(np.quantile(p_cal, 0.90))
        p_test = clf.predict_proba(x[test_events])[:, 1]
        event_idx = np.flatnonzero(test_events)
        selected_idx = event_idx[p_test >= gate]
        selected = np.zeros(len(frame), dtype=bool)
        selected[selected_idx] = True
        score = _score_policy(target, selected, days, seed=4000 + horizon)
        auc = float(roc_auc_score(continuation[test_events], p_test))
        rows.append({
            "horizon_m": horizon,
            "status": "RAN",
            "n_test_events": int(test_events.sum()),
            "continuation_auc": auc,
            "calibration_top_decile_gate": gate,
            "top_decile_continuation": score,
        })
    promotable = sum(
        int(row.get("top_decile_continuation", {}).get("promotable", False)) for row in rows
    )
    return {
        "description": "Entry occurs after a 2bps break beyond the prior 60-minute range. Top-decile continuation probability is frozen on calibration data.",
        "rows": rows,
        "promotable_configurations": promotable,
    }


def _minute_spot_perp_leadership(path: Path) -> dict:
    columns = ["timestamp", "horizon_min", "ret_1m_bps", "fut_ret_1m_bps"]
    frame = pd.read_parquet(path, columns=columns)
    frame = (
        frame[frame["horizon_min"] == 5]
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["timestamp", "ret_1m_bps", "fut_ret_1m_bps"])
        .reset_index(drop=True)
    )
    timestamp = pd.to_datetime(frame["timestamp"], utc=True)
    days = (timestamp.astype("int64").to_numpy() // 86_400_000_000_000).astype("int64")
    unique = np.sort(np.unique(days))
    split_day = unique[int(len(unique) * 0.70)]
    train = days < split_day
    test = days >= split_day
    spot = frame["ret_1m_bps"].to_numpy(float)
    perp = frame["fut_ret_1m_bps"].to_numpy(float)
    spot_gate = float(np.quantile(np.abs(spot[train]), 0.95))
    perp_gate = float(np.quantile(np.abs(perp[train]), 0.95))
    fut_next = np.roll(perp, -1)
    spot_next = np.roll(spot, -1)
    fut_next[-1] = np.nan
    spot_next[-1] = np.nan
    spot_leads = test & (np.abs(spot) >= spot_gate) & (np.abs(perp) <= np.abs(spot) * 0.50)
    perp_leads = test & (np.abs(perp) >= perp_gate) & (np.abs(spot) <= np.abs(perp) * 0.50)
    spot_gross = np.sign(spot) * fut_next
    perp_gross = np.sign(perp) * spot_next
    rows = [
        {
            "leader": "SPOT",
            "shock_gate_bps": spot_gate,
            "follower": "PERPETUAL_NEXT_MINUTE",
            **_score_policy(spot_gross, spot_leads, days, seed=5001),
        },
        {
            "leader": "PERPETUAL",
            "shock_gate_bps": perp_gate,
            "follower": "SPOT_NEXT_MINUTE",
            **_score_policy(perp_gross, perp_leads, days, seed=5002),
        },
    ]
    isolated_events = int(spot_leads.sum() + perp_leads.sum())
    correlation = float(np.corrcoef(spot, perp)[0, 1])
    return {
        "description": "One-minute catch-up diagnostic only; it cannot establish sub-second information leadership.",
        "status": "RAN" if isolated_events else "INSUFFICIENT_RESOLUTION",
        "data_rows": int(len(frame)),
        "data_days": int(len(unique)),
        "spot_perp_same_minute_return_correlation": correlation,
        "isolated_leader_events": isolated_events,
        "rows": rows,
        "promotable_configurations": int(sum(row["promotable"] for row in rows)),
    }


def _blocked_questions() -> list[dict]:
    return [
        {
            "question": "Sub-second Binance/Polymarket repricing, stale quotes and edge half-life",
            "status": "BLOCKED_DATA",
            "missing": "synchronized 50ms-1s event-time quotes on both venues",
        },
        {
            "question": "Maker/taker/wait policy, passive-fill markout and cancellation toxicity",
            "status": "BLOCKED_DATA",
            "missing": "actual order acknowledgements, queue position, fills and post-fill markouts",
        },
        {
            "question": "Liquidation continuation versus exhaustion",
            "status": "BLOCKED_DATA",
            "missing": "historical liquidation events aligned to the matrix",
        },
        {
            "question": "Price/OI/funding positioning state transitions",
            "status": "BLOCKED_DATA",
            "missing": "open-interest history and actual funding payment ledger",
        },
        {
            "question": "Multi-venue information leadership and synchronized shocks",
            "status": "BLOCKED_DATA",
            "missing": "aligned Binance, Coinbase, Bybit and Polymarket event-time prices",
        },
        {
            "question": "Portfolio allocation and capacity",
            "status": "BLOCKED_EVIDENCE",
            "missing": "at least two independently positive-EV strategies and executable depth/fills",
        },
    ]


def run(matrix: Path, round_matrix: Path) -> dict:
    frame = _load_matrix(matrix)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    split = _split(days)
    print(
        f"[data] rows={len(frame):,} days={np.unique(days).size} "
        f"train/cal/test={np.unique(days[split['train']]).size}/"
        f"{np.unique(days[split['calibration']]).size}/"
        f"{np.unique(days[split['test']]).size} cost={COST_BPS:.1f}bps",
        flush=True,
    )

    print("[1/8] direct LONG/SHORT/WAIT + movement gate", flush=True)
    direct, state = _direct_and_movement_gate(frame, split)
    print("[2/8] historical model-failure gate", flush=True)
    failure = _model_failure_gate(frame, split, state)
    print("[3/8] enter now versus fixed delay", flush=True)
    delay = _entry_delay(frame, split, state)
    print("[4/8] thesis survival clock", flush=True)
    survival = _thesis_survival(frame, state)
    print("[5/8] adverse/favorable checkpoint action value", flush=True)
    management = _position_management(frame, split, state)
    print("[6/8] breakout continuation/failure", flush=True)
    breakout = _breakout(frame, split)
    print("[7/8] minute spot/perpetual leadership", flush=True)
    leadership = _minute_spot_perp_leadership(round_matrix)
    print("[8/8] record data-blocked questions", flush=True)
    blocked = _blocked_questions()

    promotable = sum(
        section["promotable_configurations"]
        for section in (direct, failure, delay, survival, management, breakout, leadership)
    )
    return {
        "run": {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "matrix": str(matrix.resolve()),
            "round_matrix": str(round_matrix.resolve()),
            "rows": int(len(frame)),
            "days": int(np.unique(days).size),
            "train_days": int(np.unique(days[split["train"]]).size),
            "calibration_days": int(np.unique(days[split["calibration"]]).size),
            "test_days": int(np.unique(days[split["test"]]).size),
            "train_end_day": split["train_end_day"],
            "calibration_end_day": split["calibration_end_day"],
            "cost_bps": COST_BPS,
            "family_size": FAMILY_SIZE,
            "family_alpha": FAMILY_ALPHA,
            "authority": "RESEARCH_ONLY",
        },
        "direct_action_value": direct,
        "model_failure_gate": failure,
        "entry_delay": delay,
        "thesis_survival": survival,
        "position_management": management,
        "breakout_continuation": breakout,
        "minute_spot_perp_leadership": leadership,
        "blocked_questions": blocked,
        "promotable_configurations": int(promotable),
        "capital_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--round-matrix", type=Path, default=ROUND_MATRIX)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.matrix.exists():
        raise SystemExit(f"matrix not found: {args.matrix}")
    if not args.round_matrix.exists():
        raise SystemExit(f"round matrix not found: {args.round_matrix}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or (RESULTS_DIR / f"action_value_brief_batch_{stamp}.json")
    result = run(args.matrix, args.round_matrix)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=float) + "\n", encoding="utf-8")
    print(
        f"[done] promotable={result['promotable_configurations']} "
        f"capital_authority={result['capital_authority']} output={output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
