"""Causal Polymarket-round ORB probe for 5m/15m path targets and P(Hold) vetoes.

This is research-only. It does not modify the live 00:00 UTC ORB features or any
saved model. A 5m decision uses one opening bar plus one observation bar; a 15m
decision uses three opening bars plus one observation bar. Every input candle is
complete before the target path starts.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
MATRIX = DATA / "research_matrix_1m.parquet"
PERSISTENCE = DATA / "persistence_dataset.parquet"
PERSIST_MODEL = DATA / "saved_models" / "persistence_model.pkl"
OUT = DATA / "research" / "round_orb"
HORIZONS = (5, 15)
BASE_FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
ORB_FEATURES = [
    "orb_width_bps", "orb_width_vs_rv", "orb_close_position", "orb_anchor_move_bps",
    "breakout_side", "breakout_distance_bps", "failed_up", "failed_down",
    "both_sides_break", "close_back_inside", "orb_expansion", "orb_impulse_quality",
]
TARGETS = ["touch_50", "roundtrip_50", "early_touch_50", "line_cross", "big_drop_50"]


def wilson_low(hits: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = hits / total
    den = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return (centre - margin) / den


def _side(value: float, anchor: float) -> int:
    if value > anchor:
        return 1
    if value < anchor:
        return -1
    return 0


def build_round_rows(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Build one fully causal checkpoint per complete clock-aligned round."""
    h = int(horizon)
    opening_bars = 1 if h == 5 else 3
    decision_bar = opening_bars  # one completed observation bar after the opening range
    work = frame.sort_values("ts_ms").copy()
    work["round_id"] = (work["ts_ms"].astype("int64") // (h * 60_000)) * (h * 60_000)
    rows = []
    required = set(BASE_FEATURES + ["ts_ms", "open", "high", "low", "close"])
    if not required.issubset(work.columns):
        raise ValueError(f"matrix missing {sorted(required - set(work.columns))}")

    for round_id, group in work.groupby("round_id", sort=True):
        group = group.sort_values("ts_ms").reset_index(drop=True)
        if len(group) != h or decision_bar >= len(group) - 1:
            continue
        expected = int(round_id) + np.arange(h, dtype=np.int64) * 60_000
        if not np.array_equal(group["ts_ms"].to_numpy(np.int64), expected):
            continue
        opening = group.iloc[:opening_bars]
        observed = group.iloc[opening_bars:decision_bar + 1]
        future = group.iloc[decision_bar + 1:]
        if future.empty:
            continue

        anchor = float(group.iloc[0]["open"])
        current = float(group.iloc[decision_bar]["close"])
        orb_high = float(opening["high"].max())
        orb_low = float(opening["low"].min())
        width = max(orb_high - orb_low, 1e-9)
        obs_high = float(observed["high"].max())
        obs_low = float(observed["low"].min())
        obs_close = float(observed.iloc[-1]["close"])
        broke_up = obs_high > orb_high
        broke_down = obs_low < orb_low
        failed_up = broke_up and obs_close <= orb_high
        failed_down = broke_down and obs_close >= orb_low
        breakout_side = 0 if broke_up == broke_down else (1 if broke_up else -1)
        close_inside = orb_low <= obs_close <= orb_high
        if breakout_side > 0:
            extension = max(0.0, obs_high - orb_high)
            reversal = max(0.0, obs_high - obs_close)
        elif breakout_side < 0:
            extension = max(0.0, orb_low - obs_low)
            reversal = max(0.0, obs_close - obs_low)
        else:
            extension = max(max(0.0, obs_high - orb_high), max(0.0, orb_low - obs_low))
            reversal = max(0.0, extension)

        future_high = future["high"].to_numpy(float)
        future_low = future["low"].to_numpy(float)
        up_touch = future_high >= current + 50.0
        down_touch = future_low <= current - 50.0
        touch = bool(up_touch.any() or down_touch.any())
        first_touch = None
        for offset, (up, down) in enumerate(zip(up_touch, down_touch), start=1):
            if up or down:
                first_touch = offset
                break
        current_side = _side(current, anchor)
        final_side = _side(float(group.iloc[-1]["close"]), anchor)
        line_cross = current_side != 0 and final_side != 0 and current_side != final_side
        base = group.iloc[decision_bar]
        rv_dollars = max(abs(float(base["rv_15m"])) * current, 1e-9)
        row = {
            "round_id": int(round_id), "horizon": h, "decision_seconds": int((decision_bar + 1) * 60),
            "anchor": anchor, "current": current, "current_side": current_side,
            "orb_high": orb_high, "orb_low": orb_low, "orb_width_usd": width,
            "orb_width_bps": width / current * 10_000.0,
            "orb_width_vs_rv": width / rv_dollars,
            "orb_close_position": (obs_close - (orb_high + orb_low) / 2.0) / width,
            "orb_anchor_move_bps": (obs_close - anchor) / anchor * 10_000.0,
            "breakout_side": breakout_side,
            "breakout_distance_bps": extension / current * 10_000.0,
            "failed_up": int(failed_up), "failed_down": int(failed_down),
            "both_sides_break": int(broke_up and broke_down),
            "close_back_inside": int(close_inside and (broke_up or broke_down)),
            "orb_expansion": (max(obs_high, orb_high) - min(obs_low, orb_low)) / width,
            "orb_impulse_quality": (extension - reversal) / width,
            "touch_50": int(touch),
            "roundtrip_50": int(up_touch.any() and down_touch.any()),
            "early_touch_50": int(first_touch is not None and first_touch <= max(1, len(future) // 2)),
            "line_cross": int(line_cross),
            "big_drop_50": int(down_touch.any()),
            "held": int(current_side != 0 and current_side == final_side),
        }
        row.update({name: float(base[name]) for name in BASE_FEATURES})
        rows.append(row)
    return pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan).dropna()


def evaluate_feature_lift(rows: pd.DataFrame) -> list[dict]:
    results = []
    for horizon in HORIZONS:
        data = rows[rows["horizon"] == horizon].sort_values("round_id").reset_index(drop=True)
        split = int(len(data) * 0.70)
        for target in TARGETS:
            y = data[target].to_numpy(int)
            if split < 500 or len(np.unique(y[:split])) < 2 or len(np.unique(y[split:])) < 2:
                continue
            for name, feats in (("baseline", BASE_FEATURES), ("baseline_plus_orb", BASE_FEATURES + ORB_FEATURES)):
                model = Pipeline([
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(class_weight="balanced", max_iter=1000, C=0.2)),
                ])
                model.fit(data.loc[:split - 1, feats], y[:split])
                prob = model.predict_proba(data.loc[split:, feats])[:, 1]
                results.append({
                    "horizon": horizon, "target": target, "model": name,
                    "n_train": split, "n_test": len(y) - split,
                    "base_rate_test": float(y[split:].mean()),
                    "auc": float(roc_auc_score(y[split:], prob)),
                    "brier": float(brier_score_loss(y[split:], prob)),
                })
    return results


def phold_veto(rows: pd.DataFrame) -> list[dict]:
    if not PERSISTENCE.exists() or not PERSIST_MODEL.exists():
        return []
    p = pd.read_parquet(PERSISTENCE)
    checkpoints = rows[["round_id", "horizon", "decision_seconds", "failed_up", "failed_down",
                        "both_sides_break", "orb_impulse_quality"]].copy()
    p = p.merge(checkpoints, left_on=["window_start_ms", "horizon"], right_on=["round_id", "horizon"])
    p = p[np.abs(p["seconds_elapsed"] - p["decision_seconds"]) <= 1.0].copy()
    if p.empty:
        return []
    p["abs_distance_pct"] = p["distance_pct"].abs()
    p["dist_vol_ratio"] = p["abs_distance_pct"] / (p["vol_60s_pct"] + 1e-6)
    bundle = joblib.load(PERSIST_MODEL)
    feats = bundle["features"]
    raw = bundle["clf"].predict_proba(p[feats].to_numpy(float))[:, 1]
    p["p_hold"] = bundle["iso"].predict(raw)
    p["failed_against_side"] = np.where(p["position"].astype(str).str.upper().eq("UP"),
                                         p["failed_up"], p["failed_down"]).astype(int)
    selected = p[p["p_hold"] >= 0.93].copy()
    rows_out = []
    filters = {
        "baseline_phold_093": np.ones(len(selected), dtype=bool),
        "exclude_failed_against_side": selected["failed_against_side"].to_numpy() == 0,
        "exclude_both_side_break": selected["both_sides_break"].to_numpy() == 0,
        "exclude_orb_fragile": ((selected["failed_against_side"].to_numpy() == 0)
                                & (selected["both_sides_break"].to_numpy() == 0)
                                & (selected["orb_impulse_quality"].to_numpy() > -0.5)),
    }
    baseline_n = len(selected)
    for name, mask in filters.items():
        subset = selected.loc[mask]
        hits = int(subset["label"].sum())
        rows_out.append({
            "filter": name, "n": len(subset), "coverage_vs_baseline": len(subset) / baseline_n if baseline_n else 0,
            "held_rate": hits / len(subset) if len(subset) else 0,
            "wilson_low": wilson_low(hits, len(subset)),
            "bad_avoided": int((selected.loc[~mask, "label"] == 0).sum()),
            "good_lost": int((selected.loc[~mask, "label"] == 1).sum()),
        })
    return rows_out


def selftest() -> None:
    ts = np.arange(15, dtype=np.int64) * 60_000
    close = np.array([100, 101, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126], float)
    frame = pd.DataFrame({
        "ts_ms": ts, "open": close - 1, "high": close + 1, "low": close - 1, "close": close,
        "rv_15m": .001, "rv_30m": .001, "rv_60m": .001,
        "compression_ratio": 1.0, "shock_magnitude": 0.0,
    })
    rows = build_round_rows(frame, 15)
    assert len(rows) == 1 and rows.iloc[0]["decision_seconds"] == 240
    assert np.isfinite(rows.iloc[0][ORB_FEATURES]).all()
    assert 0.0 < wilson_low(8, 10) < .8
    print("ROUND ORB SELFTEST PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    selftest()
    if args.selftest:
        return 0
    if not MATRIX.exists():
        raise SystemExit(f"missing {MATRIX}")
    matrix = pd.read_parquet(MATRIX)
    round_rows = pd.concat([build_round_rows(matrix, h) for h in HORIZONS], ignore_index=True)
    lifts = evaluate_feature_lift(round_rows)
    vetoes = phold_veto(round_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    round_rows.to_parquet(args.output_dir / "round_orb_rows.parquet", index=False)
    pd.DataFrame(lifts).to_csv(args.output_dir / "orb_feature_lift.csv", index=False)
    pd.DataFrame(vetoes).to_csv(args.output_dir / "orb_phold_veto.csv", index=False)
    summary = {"rounds": int(len(round_rows)), "feature_lift": lifts, "phold_veto": vetoes}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(pd.DataFrame(lifts).round(4).to_string(index=False))
    print("\nP(Hold) vetoes\n" + (pd.DataFrame(vetoes).round(4).to_string(index=False) if vetoes else "unavailable"))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
