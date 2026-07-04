"""Causal VWAP, Bollinger, and mechanical-level path-feature research.

This research lane evaluates completed 1-minute candles at fixed checkpoints in
clock-aligned 5m and 15m rounds. It does not train or overwrite any live model.
The targets start strictly after the decision candle.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from test_round_orb_features import (
    BASE_FEATURES,
    HORIZONS,
    MATRIX,
    ORB_FEATURES,
    PERSISTENCE,
    PERSIST_MODEL,
    TARGETS,
    build_round_rows,
    wilson_low,
)


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
OUT = DATA / "research" / "vwap_bollinger_path"

VWAP_FEATURES = [
    "vwap_dist_bps",
    "vwap_dist_z",
    "vwap_slope_5_bps",
    "vwap_cross_1m",
    "vwap_failed_up_reclaim",
    "vwap_failed_down_reclaim",
    "vwap_reversion_1m",
]
BB_FEATURES = [
    "bb_zscore",
    "bb_width_bps",
    "bb_width_percentile",
    "bb_upper_touch",
    "bb_lower_touch",
    "bb_band_expansion_5",
    "bb_squeeze_ratio",
    "bb_upper_reentry",
    "bb_lower_reentry",
]
LEVEL_FEATURES = [
    "support_dist_bps",
    "resistance_dist_bps",
    "support_slope_5_bps",
    "resistance_slope_5_bps",
    "support_touch",
    "resistance_touch",
    "support_break",
    "resistance_break",
    "failed_breakdown",
    "failed_breakout",
]
PATH_FEATURES = VWAP_FEATURES + BB_FEATURES + LEVEL_FEATURES


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def build_causal_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Build trailing-only indicators; the current completed bar is observable."""
    required = {"ts_ms", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise ValueError(f"matrix missing {sorted(required - set(frame.columns))}")
    work = frame.sort_values("ts_ms").reset_index(drop=True).copy()
    if work["ts_ms"].duplicated().any():
        raise ValueError("duplicate timestamps in research matrix")
    gaps = work["ts_ms"].diff().dropna().ne(60_000)
    if gaps.any():
        raise ValueError(f"research matrix has {int(gaps.sum())} non-1m gaps; segment before testing")

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    volume = work["volume"].astype(float).clip(lower=0.0)
    typical = (high + low + close) / 3.0

    # Rolling session proxy. Unlike an exchange-day VWAP, this remains useful in
    # BTC's continuous market and never resets using a future round boundary.
    vwap_num = (typical * volume).rolling(30, min_periods=10).sum()
    vwap_den = volume.rolling(30, min_periods=10).sum()
    vwap = _safe_div(vwap_num, vwap_den)
    vwap_dist_bps = _safe_div(close - vwap, close) * 10_000.0
    dist_mean = vwap_dist_bps.rolling(240, min_periods=60).mean()
    dist_std = vwap_dist_bps.rolling(240, min_periods=60).std(ddof=0)
    vwap_dist_z = _safe_div(vwap_dist_bps - dist_mean, dist_std)
    previous_dist = vwap_dist_bps.shift(1)

    work["vwap_dist_bps"] = vwap_dist_bps
    work["vwap_dist_z"] = vwap_dist_z
    work["vwap_slope_5_bps"] = _safe_div(vwap - vwap.shift(5), close) * 10_000.0
    work["vwap_cross_1m"] = ((vwap_dist_bps * previous_dist) < 0.0).astype(float)
    work["vwap_failed_up_reclaim"] = (
        (close.shift(1) < vwap.shift(1)) & (high >= vwap) & (close < vwap)
    ).astype(float)
    work["vwap_failed_down_reclaim"] = (
        (close.shift(1) > vwap.shift(1)) & (low <= vwap) & (close > vwap)
    ).astype(float)
    work["vwap_reversion_1m"] = (vwap_dist_bps.abs() < previous_dist.abs()).astype(float)

    bb_mid = close.rolling(20, min_periods=20).mean()
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_width_bps = _safe_div(bb_upper - bb_lower, close) * 10_000.0
    width_median = bb_width_bps.rolling(240, min_periods=60).median()
    work["bb_zscore"] = _safe_div(close - bb_mid, bb_std)
    work["bb_width_bps"] = bb_width_bps
    work["bb_width_percentile"] = bb_width_bps.rolling(720, min_periods=120).rank(pct=True)
    work["bb_upper_touch"] = (high >= bb_upper).astype(float)
    work["bb_lower_touch"] = (low <= bb_lower).astype(float)
    work["bb_band_expansion_5"] = _safe_div(bb_width_bps - bb_width_bps.shift(5), bb_width_bps.shift(5))
    work["bb_squeeze_ratio"] = _safe_div(bb_width_bps, width_median)
    work["bb_upper_reentry"] = (
        (close.shift(1) > bb_upper.shift(1)) & (close <= bb_upper)
    ).astype(float)
    work["bb_lower_reentry"] = (
        (close.shift(1) < bb_lower.shift(1)) & (close >= bb_lower)
    ).astype(float)

    # Support/resistance is derived from prior bars only. No hand-drawn line and
    # no use of the current candle to define the level it is testing.
    support = low.shift(1).rolling(20, min_periods=10).min()
    resistance = high.shift(1).rolling(20, min_periods=10).max()
    tolerance = close * 0.0002  # two basis points
    work["support_dist_bps"] = _safe_div(close - support, close) * 10_000.0
    work["resistance_dist_bps"] = _safe_div(resistance - close, close) * 10_000.0
    work["support_slope_5_bps"] = _safe_div(support - support.shift(5), close) * 10_000.0
    work["resistance_slope_5_bps"] = _safe_div(resistance - resistance.shift(5), close) * 10_000.0
    work["support_touch"] = (low <= support + tolerance).astype(float)
    work["resistance_touch"] = (high >= resistance - tolerance).astype(float)
    work["support_break"] = (close < support).astype(float)
    work["resistance_break"] = (close > resistance).astype(float)
    work["failed_breakdown"] = ((low < support) & (close >= support)).astype(float)
    work["failed_breakout"] = ((high > resistance) & (close <= resistance)).astype(float)

    return work[["ts_ms"] + PATH_FEATURES].replace([np.inf, -np.inf], np.nan)


def load_round_rows(matrix: pd.DataFrame, round_path: Path) -> pd.DataFrame:
    cache_is_current = (
        round_path.exists()
        and MATRIX.exists()
        and round_path.stat().st_mtime >= MATRIX.stat().st_mtime
    )
    if cache_is_current:
        print(f"[rounds] using current cache {round_path}", flush=True)
        rows = pd.read_parquet(round_path)
    else:
        if round_path.exists():
            print(f"[rounds] ignoring stale cache {round_path}; rebuilding labels", flush=True)
        else:
            print("[rounds] no cache; rebuilding labels", flush=True)
        rows = pd.concat([build_round_rows(matrix, h) for h in HORIZONS], ignore_index=True)
        round_path.parent.mkdir(parents=True, exist_ok=True)
        rows.to_parquet(round_path, index=False)
        print(f"[rounds] wrote current cache {round_path}", flush=True)
    rows = rows.copy()
    rows["decision_ts_ms"] = rows["round_id"] + (rows["decision_seconds"] - 60) * 1_000
    indicators = build_causal_indicators(matrix)
    rows = rows.merge(indicators, left_on="decision_ts_ms", right_on="ts_ms", how="left", validate="one_to_one")
    rows = rows.drop(columns=["ts_ms"]).replace([np.inf, -np.inf], np.nan)
    return rows.dropna(subset=BASE_FEATURES + ORB_FEATURES + PATH_FEATURES).reset_index(drop=True)


def _model(kind: str):
    if kind == "logreg":
        return Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(class_weight="balanced", max_iter=1000, C=0.2)),
        ])
    if kind == "histgb":
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=120,
            max_leaf_nodes=15,
            min_samples_leaf=50,
            l2_regularization=2.0,
            class_weight="balanced",
            random_state=42,
        )
    raise ValueError(kind)


def evaluate_feature_lift(rows: pd.DataFrame) -> list[dict]:
    configurations = {
        "baseline": BASE_FEATURES,
        "baseline_plus_vwap": BASE_FEATURES + VWAP_FEATURES,
        "baseline_plus_bollinger": BASE_FEATURES + BB_FEATURES,
        "baseline_plus_levels": BASE_FEATURES + LEVEL_FEATURES,
        "baseline_plus_all_path": BASE_FEATURES + PATH_FEATURES,
        "baseline_plus_orb": BASE_FEATURES + ORB_FEATURES,
        "baseline_plus_orb_all_path": BASE_FEATURES + ORB_FEATURES + PATH_FEATURES,
    }
    results: list[dict] = []
    total_jobs = len(HORIZONS) * len(TARGETS) * (len(configurations) + 3)
    job = 0
    for horizon in HORIZONS:
        data = rows[rows["horizon"] == horizon].sort_values("round_id").reset_index(drop=True)
        split = int(len(data) * 0.70)
        for target in TARGETS:
            y = data[target].to_numpy(int)
            if split < 500 or len(np.unique(y[:split])) < 2 or len(np.unique(y[split:])) < 2:
                continue
            jobs = [("logreg", name, feats) for name, feats in configurations.items()]
            jobs += [
                ("histgb", "baseline", configurations["baseline"]),
                ("histgb", "baseline_plus_orb", configurations["baseline_plus_orb"]),
                ("histgb", "baseline_plus_orb_all_path", configurations["baseline_plus_orb_all_path"]),
            ]
            for kind, name, feats in jobs:
                job += 1
                print(f"[fit {job}/{total_jobs}] h={horizon}m target={target} model={kind} features={name}", flush=True)
                model = _model(kind)
                model.fit(data.loc[: split - 1, feats], y[:split])
                probability = model.predict_proba(data.loc[split:, feats])[:, 1]
                y_test = y[split:]
                cutoff = float(np.quantile(probability, 0.90))
                top = probability >= cutoff
                top_rate = float(y_test[top].mean()) if top.any() else 0.0
                base_rate = float(y_test.mean())
                results.append({
                    "horizon": horizon,
                    "target": target,
                    "algorithm": kind,
                    "feature_set": name,
                    "n_train": split,
                    "n_test": len(y_test),
                    "base_rate_test": base_rate,
                    "auc": float(roc_auc_score(y_test, probability)),
                    "brier": float(brier_score_loss(y_test, probability)),
                    "top_decile_n": int(top.sum()),
                    "top_decile_event_rate": top_rate,
                    "top_decile_lift": top_rate / base_rate if base_rate > 0 else 0.0,
                })
    return results


def add_fade_ride_flags(rows: pd.DataFrame) -> pd.DataFrame:
    data = rows.copy()
    up_stretched = (data["vwap_dist_z"] >= 1.5) | (data["bb_zscore"] >= 1.75)
    down_stretched = (data["vwap_dist_z"] <= -1.5) | (data["bb_zscore"] <= -1.75)
    up_reversing = (
        data["vwap_reversion_1m"].eq(1)
        | data["bb_upper_reentry"].eq(1)
        | data["vwap_failed_up_reclaim"].eq(1)
        | data["failed_up"].eq(1)
        | data["failed_breakout"].eq(1)
    )
    down_reversing = (
        data["vwap_reversion_1m"].eq(1)
        | data["bb_lower_reentry"].eq(1)
        | data["vwap_failed_down_reclaim"].eq(1)
        | data["failed_down"].eq(1)
        | data["failed_breakdown"].eq(1)
    )
    data["fade_up"] = ((data["current_side"] > 0) & up_stretched & up_reversing).astype(int)
    data["fade_down"] = ((data["current_side"] < 0) & down_stretched & down_reversing).astype(int)
    data["fade_signal"] = ((data["fade_up"] == 1) | (data["fade_down"] == 1)).astype(int)
    data["ride_up"] = (
        (data["current_side"] > 0)
        & (data["breakout_side"] > 0)
        & (data["vwap_slope_5_bps"] > 0)
        & (data["bb_band_expansion_5"] > 0)
        & (data["failed_up"] == 0)
        & (data["bb_upper_reentry"] == 0)
    ).astype(int)
    data["ride_down"] = (
        (data["current_side"] < 0)
        & (data["breakout_side"] < 0)
        & (data["vwap_slope_5_bps"] < 0)
        & (data["bb_band_expansion_5"] > 0)
        & (data["failed_down"] == 0)
        & (data["bb_lower_reentry"] == 0)
    ).astype(int)
    data["ride_signal"] = ((data["ride_up"] == 1) | (data["ride_down"] == 1)).astype(int)
    return data


def evaluate_rules(rows: pd.DataFrame) -> list[dict]:
    output = []
    for horizon in HORIZONS:
        data = rows[rows["horizon"] == horizon].sort_values("round_id")
        test = data.iloc[int(len(data) * 0.70):].copy()
        masks = {
            "all_non_neutral": test["current_side"].ne(0),
            "fade_signal": test["fade_signal"].eq(1),
            "fade_up": test["fade_up"].eq(1),
            "fade_down": test["fade_down"].eq(1),
            "ride_signal": test["ride_signal"].eq(1),
            "ride_up": test["ride_up"].eq(1),
            "ride_down": test["ride_down"].eq(1),
        }
        for name, mask in masks.items():
            subset = test.loc[mask]
            if subset.empty:
                continue
            output.append({
                "horizon": horizon,
                "rule": name,
                "n": len(subset),
                "coverage": len(subset) / len(test),
                "line_cross_rate": float(subset["line_cross"].mean()),
                "roundtrip_50_rate": float(subset["roundtrip_50"].mean()),
                "touch_50_rate": float(subset["touch_50"].mean()),
                "held_rate": float(subset["held"].mean()),
                "big_drop_50_rate": float(subset["big_drop_50"].mean()),
            })
    return output


def evaluate_phold_veto(rows: pd.DataFrame) -> list[dict]:
    if not PERSISTENCE.exists() or not PERSIST_MODEL.exists():
        return []
    columns = [
        "round_id", "horizon", "decision_seconds", "fade_up", "fade_down",
        "bb_upper_reentry", "bb_lower_reentry", "vwap_failed_up_reclaim",
        "vwap_failed_down_reclaim",
    ]
    persistence = pd.read_parquet(PERSISTENCE)
    persistence = persistence.merge(
        rows[columns], left_on=["window_start_ms", "horizon"], right_on=["round_id", "horizon"]
    )
    persistence = persistence[
        np.abs(persistence["seconds_elapsed"] - persistence["decision_seconds"]) <= 1.0
    ].copy()
    if persistence.empty:
        return []
    persistence["abs_distance_pct"] = persistence["distance_pct"].abs()
    persistence["dist_vol_ratio"] = persistence["abs_distance_pct"] / (persistence["vol_60s_pct"] + 1e-6)
    bundle = joblib.load(PERSIST_MODEL)
    raw = bundle["clf"].predict_proba(persistence[bundle["features"]].to_numpy(float))[:, 1]
    persistence["p_hold"] = bundle["iso"].predict(raw)
    is_up = persistence["position"].astype(str).str.upper().eq("UP")
    persistence["fade_against_side"] = np.where(is_up, persistence["fade_up"], persistence["fade_down"])
    persistence["bb_reentry_against_side"] = np.where(
        is_up, persistence["bb_upper_reentry"], persistence["bb_lower_reentry"]
    )
    persistence["vwap_failure_against_side"] = np.where(
        is_up, persistence["vwap_failed_up_reclaim"], persistence["vwap_failed_down_reclaim"]
    )
    selected = persistence[persistence["p_hold"] >= 0.93].copy()
    output = []
    scopes = [("all", selected)] + [(f"{h}m", selected[selected["horizon"] == h]) for h in HORIZONS]
    for scope, scoped in scopes:
        filters = {
            "baseline_phold_093": np.ones(len(scoped), dtype=bool),
            "exclude_fade_against_side": scoped["fade_against_side"].to_numpy() == 0,
            "exclude_bb_reentry_against_side": scoped["bb_reentry_against_side"].to_numpy() == 0,
            "exclude_vwap_failure_against_side": scoped["vwap_failure_against_side"].to_numpy() == 0,
            "exclude_any_path_failure": (
                (scoped["fade_against_side"].to_numpy() == 0)
                & (scoped["bb_reentry_against_side"].to_numpy() == 0)
                & (scoped["vwap_failure_against_side"].to_numpy() == 0)
            ),
        }
        baseline_n = len(scoped)
        for name, mask in filters.items():
            subset = scoped.loc[mask]
            hits = int(subset["label"].sum())
            output.append({
                "scope": scope,
                "filter": name,
                "n": len(subset),
                "coverage_vs_baseline": len(subset) / baseline_n if baseline_n else 0.0,
                "held_rate": hits / len(subset) if len(subset) else 0.0,
                "wilson_low": wilson_low(hits, len(subset)),
                "bad_avoided": int((scoped.loc[~mask, "label"] == 0).sum()),
                "good_lost": int((scoped.loc[~mask, "label"] == 1).sum()),
            })
    return output


def selftest() -> None:
    count = 240
    ts = np.arange(count, dtype=np.int64) * 60_000
    close = 60_000.0 + np.sin(np.arange(count) / 8.0) * 50.0 + np.arange(count) * 0.2
    frame = pd.DataFrame({
        "ts_ms": ts,
        "open": close - 1.0,
        "high": close + 4.0,
        "low": close - 4.0,
        "close": close,
        "volume": 10.0 + np.cos(np.arange(count) / 7.0),
    })
    original = build_causal_indicators(frame)
    changed = frame.copy()
    changed.loc[181:, ["open", "high", "low", "close"]] += 10_000.0
    replay = build_causal_indicators(changed)
    left = original.loc[180, PATH_FEATURES].to_numpy(float)
    right = replay.loc[180, PATH_FEATURES].to_numpy(float)
    assert np.allclose(left, right, equal_nan=True), "future mutation changed a past feature"
    assert np.isfinite(original.loc[180, PATH_FEATURES].to_numpy(float)).all()
    assert 0.0 <= float(original.loc[180, "bb_width_percentile"]) <= 1.0
    print("VWAP/BOLLINGER PATH SELFTEST PASS", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--round-rows", type=Path, default=OUT / "round_base_rows.parquet")
    args = parser.parse_args()
    selftest()
    if args.selftest:
        return 0
    print(f"[load] {MATRIX}", flush=True)
    matrix = pd.read_parquet(MATRIX)
    print(f"[features] rows={len(matrix):,}; building trailing VWAP/Bollinger/levels", flush=True)
    rows = add_fade_ride_flags(load_round_rows(matrix, args.round_rows))
    print(f"[rounds] usable checkpoints={len(rows):,}", flush=True)
    lifts = evaluate_feature_lift(rows)
    rules = evaluate_rules(rows)
    vetoes = evaluate_phold_veto(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(args.output_dir / "round_path_rows.parquet", index=False)
    pd.DataFrame(lifts).to_csv(args.output_dir / "feature_lift.csv", index=False)
    pd.DataFrame(rules).to_csv(args.output_dir / "fade_ride_rules.csv", index=False)
    pd.DataFrame(vetoes).to_csv(args.output_dir / "phold_veto.csv", index=False)
    summary = {
        "method": "chronological 70/30 holdout; completed-candle decision features",
        "rounds": int(len(rows)),
        "features": {
            "vwap": VWAP_FEATURES,
            "bollinger": BB_FEATURES,
            "mechanical_levels": LEVEL_FEATURES,
        },
        "feature_lift": lifts,
        "fade_ride_rules": rules,
        "phold_veto": vetoes,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nFEATURE LIFT\n" + pd.DataFrame(lifts).round(4).to_string(index=False), flush=True)
    print("\nFADE/RIDE RULES\n" + pd.DataFrame(rules).round(4).to_string(index=False), flush=True)
    print("\nP(HOLD) VETO\n" + (pd.DataFrame(vetoes).round(4).to_string(index=False) if vetoes else "unavailable"), flush=True)
    print(f"\nWrote {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
