"""Run the new, currently answerable tests from the multi-engine research brief.

Standalone research only. No serving imports, no model publishing, and only timestamped output.

Tests:

* recorded app reference versus causally available Binance spot/perpetual price;
* spot/perpetual CVD disagreement;
* pre/post-funding event effects and next-funding-rate forecast;
* psychological round-number breakout continuation/failure;
* direction-score threshold economics.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score


LANES = Path(__file__).resolve().parent
ROOT = LANES.parent
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"
ROUND_MATRIX = ROOT / "data" / "research" / "binance_updown_features.parquet"
PM_SNAPSHOTS = ROOT / "data" / "pm_export_snapshots.parquet"
PM_SETTLEMENTS = ROOT / "data" / "pm_export_settlements.parquet"
RESULTS_DIR = LANES / "results"

COST_BPS = 12.0
HORIZONS = (5, 15, 30)
FAMILY_SIZE = 96
FAMILY_ALPHA = 0.05 / FAMILY_SIZE
BOOTSTRAPS = 8_000
SEED = 20260814

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


def _day_interval(values: np.ndarray, days: np.ndarray, *, seed: int) -> dict:
    frame = pd.DataFrame({"value": np.asarray(values, float), "day": np.asarray(days)})
    frame = frame[np.isfinite(frame["value"])]
    if frame.empty:
        return {"point": None, "lcb": None, "ucb": None, "n": 0, "n_days": 0}
    daily = frame.groupby("day", sort=False)["value"].mean().to_numpy(float)
    rng = np.random.default_rng(seed)
    draws = np.empty(BOOTSTRAPS, dtype=float)
    for position in range(BOOTSTRAPS):
        draws[position] = rng.choice(daily, size=len(daily), replace=True).mean()
    return {
        "point": float(daily.mean()),
        "lcb": float(np.quantile(draws, FAMILY_ALPHA / 2.0)),
        "ucb": float(np.quantile(draws, 1.0 - FAMILY_ALPHA / 2.0)),
        "n": int(len(frame)),
        "n_days": int(len(daily)),
    }


def _score(gross: np.ndarray, mask: np.ndarray, days: np.ndarray, *, seed: int) -> dict:
    valid = np.asarray(mask, bool) & np.isfinite(gross)
    net = np.asarray(gross, float)[valid] - COST_BPS
    interval = _day_interval(net, days[valid], seed=seed)
    return {
        **interval,
        "gross_bps": float(np.mean(np.asarray(gross)[valid])) if valid.any() else None,
        "win_rate_after_cost": float((net > 0).mean()) if valid.any() else None,
        "promotable": bool(interval["lcb"] is not None and interval["lcb"] > 0.0),
    }


def _wilson(successes: int, total: int, z: float = 1.96) -> dict:
    if total <= 0:
        return {"rate": None, "lcb": None, "ucb": None, "n": 0}
    p = successes / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    radius = z * np.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denom
    return {
        "rate": float(p), "lcb": float(center - radius), "ucb": float(center + radius),
        "n": int(total),
    }


def _split_days(days: np.ndarray, train_fraction: float = 0.70) -> tuple[np.ndarray, np.ndarray, int]:
    unique = np.sort(np.unique(days))
    boundary = unique[int(len(unique) * train_fraction)]
    return days < boundary, days >= boundary, int(boundary)


def _load_matrix(path: Path) -> pd.DataFrame:
    columns = ["ts_ms", "open", "high", "low", "close", *FEATURES]
    return pd.read_parquet(path, columns=columns).replace([np.inf, -np.inf], np.nan)


def _resolution_source_basis(
    snapshots_path: Path,
    settlements_path: Path,
    matrix_path: Path,
) -> dict:
    snapshots = pd.read_parquet(
        snapshots_path,
        columns=[
            "ts", "slug", "horizon", "seconds_left", "anchor_price", "btc_price",
            "price_source",
        ],
    )
    settlements = pd.read_parquet(
        settlements_path,
        columns=["slug", "settled_side", "resolution_source"],
    ).drop_duplicates("slug", keep="last")
    market = pd.read_parquet(
        matrix_path, columns=["ts_ms", "close", "perp_spot_basis_bps"]
    ).replace([np.inf, -np.inf], np.nan)

    snapshots["snapshot_ts"] = pd.to_datetime(snapshots["ts"], unit="s", utc=True)
    market["available_ts"] = pd.to_datetime(market["ts_ms"], unit="ms", utc=True) + pd.Timedelta(minutes=1)
    market["perp_price"] = market["close"] * (1.0 + market["perp_spot_basis_bps"] / 10_000.0)
    joined = snapshots.merge(settlements, on="slug", how="inner")
    joined = pd.merge_asof(
        joined.sort_values("snapshot_ts"),
        market[["available_ts", "close", "perp_price"]].sort_values("available_ts"),
        left_on="snapshot_ts",
        right_on="available_ts",
        direction="backward",
        tolerance=pd.Timedelta(seconds=90),
    )
    joined = joined[
        joined[["anchor_price", "btc_price", "close", "perp_price", "settled_side"]]
        .notna().all(axis=1)
    ].copy()
    joined = joined[~joined["price_source"].astype(str).str.contains("binance", case=False)]
    joined["recorded_side"] = (joined["btc_price"] >= joined["anchor_price"]).astype(int)
    joined["spot_side"] = (joined["close"] >= joined["anchor_price"]).astype(int)
    joined["perp_side"] = (joined["perp_price"] >= joined["anchor_price"]).astype(int)
    joined["settled_side"] = pd.to_numeric(joined["settled_side"], errors="coerce").astype(int)

    rows = []
    for horizon in (5, 15):
        horizon_frame = joined[joined["horizon"] == horizon]
        checkpoints = (300, 120, 60, 30, 15) if horizon == 5 else (600, 300, 120, 60, 30, 15)
        for checkpoint in checkpoints:
            candidate = horizon_frame[
                (horizon_frame["seconds_left"] - checkpoint).abs() <= 12
            ].copy()
            if candidate.empty:
                continue
            candidate["checkpoint_error"] = (candidate["seconds_left"] - checkpoint).abs()
            candidate = (
                candidate.sort_values(["slug", "checkpoint_error", "snapshot_ts"])
                .drop_duplicates("slug", keep="first")
            )
            outcome = candidate["settled_side"].to_numpy(int)
            recorded = candidate["recorded_side"].to_numpy(int)
            spot = candidate["spot_side"].to_numpy(int)
            perp = candidate["perp_side"].to_numpy(int)
            conflict = recorded != spot
            conflict_n = int(conflict.sum())
            recorded_wins = int(((recorded == outcome) & conflict).sum())
            rows.append({
                "horizon_m": horizon,
                "seconds_left": checkpoint,
                "n_rounds": int(len(candidate)),
                "recorded_reference_accuracy": _wilson(int((recorded == outcome).sum()), len(candidate)),
                "completed_binance_spot_accuracy": _wilson(int((spot == outcome).sum()), len(candidate)),
                "derived_perpetual_accuracy": _wilson(int((perp == outcome).sum()), len(candidate)),
                "recorded_vs_spot_conflicts": conflict_n,
                "recorded_reference_wins_conflict": _wilson(recorded_wins, conflict_n),
            })
    return {
        "description": (
            "Recorded Pyth/Chainlink app reference is compared with the latest causally completed "
            "Binance minute close and a basis-derived perpetual price. Official PM outcomes are "
            "used, but the exact rule-specified oracle is not archived. This is an input-selection "
            "diagnostic, not proof of a resolution-source arbitrage or a trading strategy."
        ),
        "joined_rows": int(len(joined)),
        "joined_rounds": int(joined["slug"].nunique()),
        "price_sources": {str(k): int(v) for k, v in joined["price_source"].value_counts().items()},
        "resolution_sources": {
            str(k): int(v) for k, v in joined["resolution_source"].value_counts().items()
        },
        "exact_rule_oracle_archived": False,
        "rows": rows,
        "authority": "DIAGNOSTIC_ONLY",
        "promotable_configurations": 0,
    }


def _spot_perp_flow_disagreement(frame: pd.DataFrame) -> dict:
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    train, test, boundary = _split_days(days)
    spot = frame["cvd_spot"].to_numpy(float)
    perp = frame["cvd_perp"].to_numpy(float)
    spot_scale = float(np.nanquantile(np.abs(spot[train]), 0.75))
    perp_scale = float(np.nanquantile(np.abs(perp[train]), 0.75))
    disagree = (
        test & np.isfinite(spot) & np.isfinite(perp)
        & (spot * perp < 0)
        & (np.abs(spot) >= spot_scale)
        & (np.abs(perp) >= perp_scale)
    )
    rows = []
    for position, horizon in enumerate(HORIZONS):
        forward = _forward_return(close, horizon)
        spot_policy = _score(np.sign(spot) * forward, disagree, days, seed=100 + position)
        perp_policy = _score(np.sign(perp) * forward, disagree, days, seed=110 + position)
        selected_move = np.abs(forward[disagree & np.isfinite(forward)])
        baseline_move = np.abs(forward[test & np.isfinite(forward)])
        rows.append({
            "horizon_m": horizon,
            "n_disagreements": int((disagree & np.isfinite(forward)).sum()),
            "spot_flow_direction": spot_policy,
            "perp_flow_direction": perp_policy,
            "mean_absolute_move_bps": float(np.mean(selected_move)) if len(selected_move) else None,
            "baseline_absolute_move_bps": float(np.mean(baseline_move)) if len(baseline_move) else None,
        })
    promotable = sum(
        int(row[key]["promotable"])
        for row in rows
        for key in ("spot_flow_direction", "perp_flow_direction")
    )
    return {
        "description": "Strong, opposite-sign spot/perpetual CVD states; each venue's sign is scored independently after 12 bps.",
        "split_day": boundary,
        "train_spot_abs_p75": spot_scale,
        "train_perp_abs_p75": perp_scale,
        "rows": rows,
        "promotable_configurations": promotable,
    }


def _funding_study(path: Path) -> dict:
    columns = [
        "timestamp", "horizon_min", "close", "funding_rate_bps", "hours_to_funding",
        "premium_index", "fut_basis_bps", "mark_basis_bps", "spot_fut_ret_diff_sum_5m_bps",
        "realized_vol_15m_bps", "taker_delta_ratio_5m", "fut_volume", "ret_sum_5m_bps",
    ]
    frame = pd.read_parquet(path, columns=columns)
    frame = (
        frame[frame["horizon_min"] == 5]
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .replace([np.inf, -np.inf], np.nan)
        .reset_index(drop=True)
    )
    reset_idx = np.flatnonzero(frame["hours_to_funding"].diff().to_numpy(float) > 1.0)
    reset_idx = reset_idx[(reset_idx >= 30) & (reset_idx + 15 < len(frame))]
    event_days = (
        pd.to_datetime(frame.loc[reset_idx, "timestamp"], utc=True).astype("int64").to_numpy()
        // 86_400_000_000_000
    ).astype("int64")
    unique_days = np.sort(np.unique(event_days))
    split_day = unique_days[int(len(unique_days) * 0.70)]
    event_train = event_days < split_day
    event_test = event_days >= split_day
    pre_idx = reset_idx - 30

    feature_names = [
        "funding_rate_bps", "premium_index", "fut_basis_bps", "mark_basis_bps",
        "spot_fut_ret_diff_sum_5m_bps", "realized_vol_15m_bps", "taker_delta_ratio_5m",
        "fut_volume", "ret_sum_5m_bps",
    ]
    x = frame.loc[pre_idx, feature_names].to_numpy(float)
    target = frame.loc[reset_idx, "funding_rate_bps"].to_numpy(float)
    naive = frame.loc[pre_idx, "funding_rate_bps"].to_numpy(float)
    model = HistGradientBoostingRegressor(
        max_iter=120, learning_rate=0.05, max_leaf_nodes=7,
        min_samples_leaf=12, l2_regularization=4.0, random_state=SEED,
    )
    model.fit(x[event_train], target[event_train])
    prediction = model.predict(x[event_test])
    model_mae = float(mean_absolute_error(target[event_test], prediction))
    naive_mae = float(mean_absolute_error(target[event_test], naive[event_test]))
    improvement = np.abs(naive[event_test] - target[event_test]) - np.abs(
        prediction - target[event_test]
    )
    improvement_interval = _day_interval(
        improvement, event_days[event_test], seed=200
    )

    close = frame["close"].to_numpy(float)
    premium = frame["premium_index"].to_numpy(float)
    premium_threshold = float(np.nanquantile(np.abs(premium[pre_idx[event_train]]), 0.75))
    extreme = np.abs(premium[pre_idx]) >= premium_threshold
    event_rows = []
    for position, window in enumerate((30, 15, 5, 1)):
        pre_return = (close[reset_idx] / close[reset_idx - window] - 1.0) * 10_000.0
        signed_unwind = -np.sign(premium[pre_idx]) * pre_return
        mask = event_test & extreme & np.isfinite(signed_unwind)
        event_rows.append({
            "phase": f"PRE_{window}M_UNWIND",
            **_score(signed_unwind, mask, event_days, seed=210 + position),
        })
    for position, window in enumerate((1, 5, 15)):
        post_return = (close[reset_idx + window] / close[reset_idx] - 1.0) * 10_000.0
        signed_reversal = np.sign(premium[pre_idx]) * post_return
        mask = event_test & extreme & np.isfinite(signed_reversal)
        event_rows.append({
            "phase": f"POST_{window}M_REVERSAL",
            **_score(signed_reversal, mask, event_days, seed=220 + position),
        })
    return {
        "description": (
            "Actual hours-to-funding resets define 270 events. The rate model uses only T-30m "
            "features. Event trades are conditioned on a train-frozen extreme premium and pay 12 bps."
        ),
        "events": int(len(reset_idx)),
        "train_events": int(event_train.sum()),
        "test_events": int(event_test.sum()),
        "next_funding_rate": {
            "model_mae_bps": model_mae,
            "naive_current_rate_mae_bps": naive_mae,
            "mae_improvement_naive_minus_model": improvement_interval,
            "promotable": False,
            "authority": "DIAGNOSTIC_ONLY",
        },
        "train_extreme_premium_abs_threshold": premium_threshold,
        "event_rows": event_rows,
        "promotable_configurations": int(sum(row["promotable"] for row in event_rows)),
    }


def _psychological_levels(frame: pd.DataFrame) -> dict:
    close = frame["close"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    _, test, _ = _split_days(days)
    rows = []
    for step_position, step in enumerate((100.0, 500.0, 1_000.0)):
        previous = np.roll(close, 1)
        up = np.floor(previous / step) < np.floor(close / step)
        down = np.floor(previous / step) > np.floor(close / step)
        side = np.where(up, 1.0, np.where(down, -1.0, 0.0))
        events = np.flatnonzero(test & (side != 0))
        kept = []
        last = -10_000
        for idx in events:
            if idx - last >= 30:
                kept.append(idx)
                last = idx
        event_mask = np.zeros(len(frame), dtype=bool)
        event_mask[np.asarray(kept, dtype=int)] = True
        for horizon_position, horizon in enumerate(HORIZONS):
            forward = _forward_return(close, horizon)
            gross = side * forward
            selected_idx = np.flatnonzero(event_mask & np.isfinite(forward))
            failures = []
            for idx in selected_idx:
                if idx + horizon >= len(frame):
                    continue
                if side[idx] > 0:
                    level = np.floor(close[idx] / step) * step
                    failures.append(float(np.nanmin(low[idx + 1 : idx + horizon + 1]) <= level))
                else:
                    level = np.ceil(close[idx] / step) * step
                    failures.append(float(np.nanmax(high[idx + 1 : idx + horizon + 1]) >= level))
            rows.append({
                "level_step_usd": int(step),
                "horizon_m": horizon,
                "failure_rate": float(np.mean(failures)) if failures else None,
                **_score(
                    gross, event_mask, days,
                    seed=300 + step_position * 10 + horizon_position,
                ),
            })
    return {
        "description": "First crossing after a 30-minute cooldown; continuation enters after the observed close and pays 12 bps.",
        "rows": rows,
        "promotable_configurations": int(sum(row["promotable"] for row in rows)),
    }


def _confidence_threshold(frame: pd.DataFrame) -> dict:
    close = frame["close"].to_numpy(float)
    days = (frame["ts_ms"].to_numpy("int64") // 86_400_000).astype("int64")
    unique = np.sort(np.unique(days))
    train_end = unique[int(len(unique) * 0.60)]
    calibration_end = unique[int(len(unique) * 0.70)]
    train = days < train_end
    calibration = (days >= train_end) & (days < calibration_end)
    test = days >= calibration_end
    x = frame[FEATURES].to_numpy(float)
    thresholds = np.arange(0.50, 0.951, 0.05)
    output = []

    for horizon_position, horizon in enumerate(HORIZONS):
        forward = _forward_return(close, horizon)
        label = (forward > 0).astype(float)
        label[~np.isfinite(forward)] = np.nan
        idx = np.flatnonzero(train & np.isfinite(label))
        if len(idx) > 220_000:
            idx = idx[np.linspace(0, len(idx) - 1, 220_000, dtype=int)]
        model = HistGradientBoostingClassifier(
            max_iter=160, learning_rate=0.055, max_leaf_nodes=15,
            l2_regularization=2.0, random_state=SEED + horizon,
        )
        model.fit(x[idx], label[idx].astype(int))
        calibration_resolved = calibration & np.isfinite(label)
        test_resolved = test & np.isfinite(label)
        p_cal = model.predict_proba(x[calibration_resolved])[:, 1]
        p_test = model.predict_proba(x[test_resolved])[:, 1]
        cal_idx = np.flatnonzero(calibration_resolved)
        test_idx = np.flatnonzero(test_resolved)
        cal_rows = []
        test_rows = []
        for threshold_position, threshold in enumerate(thresholds):
            cal_side = np.where(p_cal >= threshold, 1.0, np.where(p_cal <= 1.0 - threshold, -1.0, 0.0))
            test_side = np.where(p_test >= threshold, 1.0, np.where(p_test <= 1.0 - threshold, -1.0, 0.0))
            cal_mask = cal_side != 0
            test_mask_local = test_side != 0
            cal_net = cal_side[cal_mask] * forward[cal_idx[cal_mask]] - COST_BPS
            cal_value = float(np.nanmean(cal_net)) if len(cal_net) else float("-inf")
            full_side = np.zeros(len(frame), dtype=float)
            full_mask = np.zeros(len(frame), dtype=bool)
            full_side[test_idx] = test_side
            full_mask[test_idx] = test_mask_local
            score = _score(
                full_side * forward,
                full_mask,
                days,
                seed=400 + horizon_position * 20 + threshold_position,
            )
            cal_rows.append({"threshold": float(threshold), "calls": int(cal_mask.sum()), "net_bps": cal_value})
            test_rows.append({"threshold": float(threshold), **score})
        eligible = [row for row in cal_rows if row["calls"] >= 250]
        selected = max(eligible, key=lambda row: row["net_bps"]) if eligible else cal_rows[0]
        selected_test = next(row for row in test_rows if abs(row["threshold"] - selected["threshold"]) < 1e-9)
        auc = float(roc_auc_score(label[test_resolved], p_test))
        output.append({
            "horizon_m": horizon,
            "test_auc": auc,
            "calibration_selected_threshold": selected,
            "selected_threshold_test": selected_test,
            "test_grid": test_rows,
        })
    return {
        "description": (
            "Raw direction-score thresholds 0.50-0.95. Threshold is selected on a separate "
            "10% calibration era with at least 250 calls, then scored on the final 30%."
        ),
        "rows": output,
        "promotable_configurations": int(
            sum(row["selected_threshold_test"]["promotable"] for row in output)
        ),
    }


def _blocked_register() -> list[dict]:
    return [
        {"questions": "Options fair value, IV/skew and tri-market consensus", "missing": "historical strike-level options chain aligned to PM rounds"},
        {"questions": "Maker toxicity, queue value, cancel policy and sub-second alpha decay", "missing": "actual quote attempts, queue, fills and 50ms-5s markouts"},
        {"questions": "Price/OI/funding states and liquidation prediction/clusters", "missing": "causal OI and liquidation event histories"},
        {"questions": "Multi-venue leader and PM overshoot at 1s-60s", "missing": "synchronized event-time prices and PM revisions"},
        {"questions": "Capacity, portfolio/tail correlation and opportunity auction", "missing": "positive executable strategies plus realized fills and joint PnL"},
        {"questions": "Alpha stop/restart/decay after promotion", "missing": "a promoted strategy with independent forward outcomes"},
    ]


def run(args: argparse.Namespace) -> dict:
    frame = _load_matrix(args.matrix)
    print(f"[data] matrix rows={len(frame):,}", flush=True)
    print("[1/6] resolution-source price comparison", flush=True)
    resolution = _resolution_source_basis(args.pm_snapshots, args.pm_settlements, args.matrix)
    print("[2/6] spot/perpetual flow disagreement", flush=True)
    flow = _spot_perp_flow_disagreement(frame)
    print("[3/6] funding event/rate study", flush=True)
    funding = _funding_study(args.round_matrix)
    print("[4/6] psychological-level crossings", flush=True)
    levels = _psychological_levels(frame)
    print("[5/6] confidence-threshold economics", flush=True)
    confidence = _confidence_threshold(frame)
    print("[6/6] blocked-question register", flush=True)
    blocked = _blocked_register()
    promotable = sum(
        section["promotable_configurations"]
        for section in (resolution, flow, funding, levels, confidence)
    )
    return {
        "run": {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "matrix": str(args.matrix.resolve()),
            "round_matrix": str(args.round_matrix.resolve()),
            "pm_snapshots": str(args.pm_snapshots.resolve()),
            "pm_settlements": str(args.pm_settlements.resolve()),
            "cost_bps": COST_BPS,
            "family_size": FAMILY_SIZE,
            "family_alpha": FAMILY_ALPHA,
            "authority": "RESEARCH_ONLY",
        },
        "resolution_source_basis": resolution,
        "spot_perp_flow_disagreement": flow,
        "funding_study": funding,
        "psychological_levels": levels,
        "confidence_threshold": confidence,
        "blocked_questions": blocked,
        "promotable_configurations": int(promotable),
        "capital_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--round-matrix", type=Path, default=ROUND_MATRIX)
    parser.add_argument("--pm-snapshots", type=Path, default=PM_SNAPSHOTS)
    parser.add_argument("--pm-settlements", type=Path, default=PM_SETTLEMENTS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    for path in (args.matrix, args.round_matrix, args.pm_snapshots, args.pm_settlements):
        if not path.exists():
            raise SystemExit(f"required input not found: {path}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or RESULTS_DIR / f"multi_engine_brief_batch_{stamp}.json"
    result = run(args)
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
