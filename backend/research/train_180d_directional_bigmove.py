#!/usr/bin/env python
"""
Research-only directional big-move bakeoff for BTC.

This script tests the net-new labels that were proposed but not yet measured:

  - big_up
  - big_down
  - big_drop

It reuses the leak-safe Binance feature builder from
train_360d_multitarget_forecaster.py, runs a chronological split, trains tabular
classification models, and writes metrics/predictions under data/research.

It does not modify live app models, DuckDB state, production artifacts, or bot
logic.
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import pickle
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import train_360d_multitarget_forecaster as base

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = ROOT / "data" / "research"
MODEL_DIR = ROOT / "data" / "saved_models" / "research_directional_bigmove"
LOG_DIR = ROOT / "data" / "logs"


@dataclass
class DirectionalBigMoveConfig:
    symbol: str
    days: int
    horizons: list[int]
    models: list[str]
    output_prefix: str
    start: str | None
    end: str | None
    smoke: bool
    rebuild_cache: bool
    max_features: int
    max_train_rows: int | None
    n_jobs: int
    device: str
    save_models: bool
    threshold_5m_bps: float
    threshold_15m_bps: float
    drop_threshold_5m_bps: float
    drop_threshold_15m_bps: float


PRED_COLUMNS = [
    "timestamp",
    "split",
    "target_name",
    "horizon",
    "model_name",
    "y_true",
    "y_pred",
    "y_prob",
]


def log(msg: str) -> None:
    print(f"{pd.Timestamp.now().strftime('%H:%M:%S')} {msg}", flush=True)


def output_path(config: DirectionalBigMoveConfig, suffix: str) -> Path:
    return RESEARCH_DIR / f"{config.output_prefix}_{suffix}"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def save_model(model: Any, path: Path, enabled: bool) -> None:
    if not enabled:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(model, f)


def free_memory(*objs: Any) -> None:
    for obj in objs:
        try:
            del obj
        except Exception:
            pass
    gc.collect()


def threshold_for_horizon(config: DirectionalBigMoveConfig, horizon: int) -> float:
    if horizon <= 5:
        return float(config.threshold_5m_bps)
    if horizon <= 15:
        return float(config.threshold_15m_bps)
    return float(config.threshold_15m_bps)


def drop_threshold_for_horizon(config: DirectionalBigMoveConfig, horizon: int) -> float:
    if horizon <= 5:
        return float(config.drop_threshold_5m_bps)
    if horizon <= 15:
        return float(config.drop_threshold_15m_bps)
    return float(config.drop_threshold_15m_bps)


def add_directional_bigmove_targets(
    df: pd.DataFrame,
    horizons: list[int],
    config: DirectionalBigMoveConfig,
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    out = df.copy()
    targets: list[str] = []
    meta: dict[str, Any] = {"thresholds_bps": {}, "target_definitions": {}}
    for h in horizons:
        ret_col = f"target_return_{h}m_bps"
        if ret_col not in out.columns:
            raise ValueError(f"missing required return target column: {ret_col}")
        low_col = f"target_low_{h}m_bps"
        if low_col not in out.columns:
            raise ValueError(f"missing required future-low target column: {low_col}")
        thr = threshold_for_horizon(config, h)
        drop_thr = drop_threshold_for_horizon(config, h)
        meta["thresholds_bps"][f"{h}m"] = thr
        meta["thresholds_bps"][f"{h}m_drop"] = drop_thr

        up = f"target_big_up_{h}m"
        down = f"target_big_down_{h}m"
        drop = f"target_big_drop_{h}m"

        out[up] = (out[ret_col] >= thr).astype(float)
        out[down] = (out[ret_col] <= -thr).astype(float)
        # big_drop is path-aware: did BTC trade down enough at any point inside
        # the horizon, even if it later bounced before the close?
        out[drop] = (out[low_col] <= -drop_thr).astype(float)

        targets.extend([up, down, drop])
        meta["target_definitions"][up] = f"{ret_col} >= +{thr:g} bps"
        meta["target_definitions"][down] = f"{ret_col} <= -{thr:g} bps"
        meta["target_definitions"][drop] = f"{low_col} <= -{drop_thr:g} bps; path-aware downside risk label"
    return out, targets, meta


def add_prediction_rows(
    rows: list[dict[str, Any]],
    meta: pd.DataFrame,
    target: str,
    model_name: str,
    y_true: np.ndarray,
    prob: np.ndarray,
) -> None:
    pred = (prob >= 0.5).astype(int)
    for i in range(min(len(meta), len(y_true), len(prob))):
        rows.append(
            {
                "timestamp": meta["timestamp"].iloc[i],
                "split": "test",
                "target_name": target,
                "horizon": base.extract_horizon(target),
                "model_name": model_name,
                "y_true": int(y_true[i]),
                "y_pred": int(pred[i]),
                "y_prob": float(prob[i]),
            }
        )


def top_edge_rows(y_true: np.ndarray, prob: np.ndarray) -> dict[str, float]:
    from sklearn.metrics import precision_score

    y = y_true.astype(int)
    p = np.clip(prob.astype(float), 1e-6, 1 - 1e-6)
    out: dict[str, float] = {}
    for pct in [0.01, 0.05, 0.10, 0.20]:
        k = max(1, int(len(y) * pct))
        idx = np.argsort(p)[::-1][:k]
        pred = np.ones(len(idx), dtype=int)
        out[f"top_{int(pct * 100)}_precision"] = float(precision_score(y[idx], pred, zero_division=0))
        out[f"top_{int(pct * 100)}_recall_share"] = float(y[idx].sum() / max(1, y.sum()))
    return out


def no_trade_baseline_metrics(target: str, y_true: np.ndarray) -> dict[str, Any]:
    p = np.full(len(y_true), float(np.mean(y_true)))
    row = base.classification_metric_row("baseline_train_rate", target, y_true, p)
    row.update(top_edge_rows(y_true, p))
    return row


def train_directional_bigmove(config: DirectionalBigMoveConfig) -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    base_config = base.RunConfig(
        symbol=config.symbol,
        days=config.days,
        horizons=config.horizons,
        models=config.models,
        output_prefix=config.output_prefix,
        start=config.start,
        end=config.end,
        smoke=config.smoke,
        rebuild_cache=config.rebuild_cache,
        max_features=config.max_features,
        max_train_rows=config.max_train_rows,
        n_jobs=config.n_jobs,
        device=config.device,
        save_models=config.save_models,
        skip_regression=True,
        skip_classification=False,
        skip_quantile=True,
        skip_sequence=True,
        quantile_backends=["lightgbm"],
        include_sequence=False,
        sequence_targets="core",
        seq_len=60,
        seq_max_features=48,
        seq_max_rows=100000,
        seq_epochs=3,
        seq_batch_size=384,
    )

    log(f"[start] directional big-move config={json.dumps(asdict(config), sort_keys=True)}")
    df = base.build_market_frame(base_config)
    df, feature_cols = base.add_features(df)
    df, target_map = base.add_targets(df, config.horizons)
    df, targets, target_meta = add_directional_bigmove_targets(df, config.horizons, config)

    max_h = max(config.horizons)
    df = df.iloc[240 : len(df) - max_h].copy().reset_index(drop=True)
    required = target_map["regression"] + target_map["classification"] + targets
    df = df.dropna(subset=required, how="any").reset_index(drop=True)

    train_idx, cal_idx, test_idx = base.chronological_splits(len(df))
    selected_features = base.select_features(df, feature_cols, train_idx, config.max_features)

    manifest = {
        "config": asdict(config),
        "rows": len(df),
        "split_rows": {"train": len(train_idx), "calibration": len(cal_idx), "test": len(test_idx)},
        "feature_cols": selected_features,
        "n_features": len(selected_features),
        "targets": targets,
        "target_meta": target_meta,
        "leakage_rules": [
            "features use current/past data only",
            "targets use future return columns only",
            "chronological 64/16/20 split",
            "feature medians fit on train only",
            "research-only artifacts do not affect live app",
        ],
    }
    (MODEL_DIR / f"{config.output_prefix}_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    metrics: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    pred_csv = output_path(config, "predictions.csv")
    if pred_csv.exists():
        pred_csv.unlink()

    models = base.classification_models(config.models, config.n_jobs, config.device)
    log(f"[features] selected={len(selected_features)} rows={len(df):,} train={len(train_idx):,} cal={len(cal_idx):,} test={len(test_idx):,}")
    log(f"[models] {list(models.keys())}")
    log(f"[targets] {targets}")

    for target in targets:
        log(f"[target] {target}")
        Xtr, ytr, _, _, Xte, yte, meta = base.prepare_xy(
            df, selected_features, target, train_idx, cal_idx, test_idx, config.max_train_rows
        )
        ytr = ytr.astype(int)
        yte = yte.astype(int)
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            log(f"[skip] {target}: single-class split train={np.unique(ytr)} test={np.unique(yte)}")
            continue

        base_prob = np.full(len(yte), float(np.mean(ytr)))
        base_row = base.classification_metric_row("baseline_train_rate", target, yte, base_prob)
        base_row.update(top_edge_rows(yte, base_prob))
        metrics.append(base_row)
        summary.append(
            {
                "model_name": "baseline_train_rate",
                "target_name": target,
                "horizon": base_row["horizon"],
                "metric_main": "auc",
                "metric_value": base_row["auc"],
                "notes": "baseline",
            }
        )
        rows: list[dict[str, Any]] = []
        add_prediction_rows(rows, meta, target, "baseline_train_rate", yte, base_prob)
        append_csv(pred_csv, rows, PRED_COLUMNS)
        inventory.append(
            {
                "family": "classification",
                "target_name": target,
                "model_name": "baseline_train_rate",
                "status": "ok",
                "fit_seconds": 0.0,
                "train_rows": len(ytr),
                "test_rows": len(yte),
                "notes": "baseline",
                "error": "",
            }
        )

        for name, model in models.items():
            t0 = time.time()
            try:
                model.fit(Xtr, ytr)
                if hasattr(model, "predict_proba"):
                    prob = np.asarray(model.predict_proba(Xte)[:, 1], dtype=float)
                else:
                    prob = np.asarray(model.predict(Xte), dtype=float)
                row = base.classification_metric_row(name, target, yte, prob)
                row.update(top_edge_rows(yte, prob))
                metrics.append(row)
                summary.append(
                    {
                        "model_name": name,
                        "target_name": target,
                        "horizon": row["horizon"],
                        "metric_main": "auc",
                        "metric_value": row["auc"],
                        "notes": "directional_bigmove",
                    }
                )
                rows = []
                add_prediction_rows(rows, meta, target, name, yte, prob)
                append_csv(pred_csv, rows, PRED_COLUMNS)
                save_model(model, MODEL_DIR / config.output_prefix / target / f"{name}.pkl", config.save_models)
                fit_seconds = time.time() - t0
                inventory.append(
                    {
                        "family": "classification",
                        "target_name": target,
                        "model_name": name,
                        "status": "ok",
                        "fit_seconds": fit_seconds,
                        "train_rows": len(ytr),
                        "test_rows": len(yte),
                        "notes": "",
                        "error": "",
                    }
                )
                log(
                    f"[done] {target}/{name} auc={row['auc']:.4f} "
                    f"p@5={row['top_5_precision']:.3f} brier={row['brier']:.4f} in {fit_seconds:.1f}s"
                )
            except Exception as exc:
                inventory.append(
                    {
                        "family": "classification",
                        "target_name": target,
                        "model_name": name,
                        "status": "error",
                        "fit_seconds": time.time() - t0,
                        "train_rows": len(ytr),
                        "test_rows": len(yte),
                        "notes": "",
                        "error": str(exc)[:500],
                    }
                )
                log(f"[skip] {target}/{name}: {exc}")
            finally:
                free_memory(model)

        free_memory(Xtr, ytr, Xte, yte, meta)
        write_csv(output_path(config, "classification_metrics.csv"), metrics)
        write_csv(output_path(config, "model_inventory.csv"), inventory)
        write_csv(output_path(config, "summary.csv"), base.rank_summary(summary))

    analysis_rows = build_analysis_summary(metrics)
    write_csv(output_path(config, "analysis_summary.csv"), analysis_rows)
    write_markdown_report(config, manifest, metrics, analysis_rows)
    log(f"[done] directional big-move run complete prefix={config.output_prefix}")


def build_analysis_summary(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not metrics:
        return []
    df = pd.DataFrame(metrics)
    rows: list[dict[str, Any]] = []
    for target, g in df.groupby("target_name"):
        gg = g[~g["model_name"].astype(str).str.startswith("baseline")].sort_values("auc", ascending=False)
        if gg.empty:
            continue
        best = gg.iloc[0]
        base_rate = float(best["base_rate"])
        rows.append(
            {
                "target_name": target,
                "horizon": int(best["horizon"]),
                "base_rate": base_rate,
                "majority_baseline_accuracy": max(base_rate, 1.0 - base_rate),
                "best_model": best["model_name"],
                "best_auc": best["auc"],
                "best_accuracy": best["accuracy"],
                "best_precision": best["precision"],
                "best_recall": best["recall"],
                "best_f1": best["f1"],
                "best_brier": best["brier"],
                "best_top_1_precision": best["top_1_precision"],
                "best_top_5_precision": best["top_5_precision"],
                "best_top_10_precision": best["top_10_precision"],
                "best_top_20_precision": best["top_20_precision"],
            }
        )
    return rows


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            if isinstance(val, float):
                vals.append(f"{val:.4f}")
            else:
                vals.append(str(val))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def write_markdown_report(
    config: DirectionalBigMoveConfig,
    manifest: dict[str, Any],
    metrics: list[dict[str, Any]],
    analysis_rows: list[dict[str, Any]],
) -> None:
    path = output_path(config, "report.md")
    lines = [
        "# Directional Big-Move Research Report",
        "",
        f"Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "This research tests the labels that were previously proposed but not measured: `big_up`, `big_down`, and `big_drop`.",
        "",
        "## Run Config",
        "",
        f"- Days: `{config.days}`",
        f"- Horizons: `{config.horizons}`",
        f"- Threshold 5m: `{config.threshold_5m_bps:g} bps`",
        f"- Threshold 15m: `{config.threshold_15m_bps:g} bps`",
        f"- Drop threshold 5m: `{config.drop_threshold_5m_bps:g} bps`",
        f"- Drop threshold 15m: `{config.drop_threshold_15m_bps:g} bps`",
        f"- Models: `{', '.join(config.models)}`",
        f"- Features: `{manifest['n_features']}`",
        f"- Rows: `{manifest['rows']:,}`",
        f"- Split: `{manifest['split_rows']}`",
        "",
        "## Targets",
        "",
        markdown_table(
            [
                {"target": k, "definition": v}
                for k, v in manifest["target_meta"]["target_definitions"].items()
            ],
            ["target", "definition"],
        ),
        "",
        "## Best Model Per Target",
        "",
        markdown_table(
            analysis_rows,
            [
                "target_name",
                "horizon",
                "base_rate",
                "best_model",
                "best_auc",
                "best_precision",
                "best_recall",
                "best_top_5_precision",
                "best_brier",
            ],
        ),
        "",
        "## Interpretation Rules",
        "",
        "- `big_up_*` asks whether a meaningful upward move happens.",
        "- `big_down_*` asks whether a meaningful downward move happens.",
        "- `big_drop_*` asks whether BTC traded down enough inside the window, even if it later bounced.",
        "- Promote only if AUC, top-confidence precision, and calibration beat existing baselines.",
        "",
        "## Output Files",
        "",
        f"- `{output_path(config, 'classification_metrics.csv').relative_to(ROOT)}`",
        f"- `{output_path(config, 'analysis_summary.csv').relative_to(ROOT)}`",
        f"- `{output_path(config, 'predictions.csv').relative_to(ROOT)}`",
        f"- `{output_path(config, 'model_inventory.csv').relative_to(ROOT)}`",
        f"- `{MODEL_DIR / (config.output_prefix + '_manifest.json')}`",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Directional big-up/big-down/big-drop research bakeoff.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    parser.add_argument("--models", default="logistic,histgb,rf,extra_trees,lightgbm,xgboost,catboost")
    parser.add_argument("--output-prefix", default="forecast_180d_directional_bigmove")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--rebuild-cache", action="store_true")
    parser.add_argument("--max-features", type=int, default=160)
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--n-jobs", type=int, default=max(1, min(4, (os.cpu_count() or 4) - 1)))
    parser.add_argument("--device", choices=["auto", "cpu", "gpu", "cuda"], default="gpu")
    parser.add_argument("--no-save-models", action="store_true")
    parser.add_argument("--threshold-5m-bps", type=float, default=10.0)
    parser.add_argument("--threshold-15m-bps", type=float, default=15.0)
    parser.add_argument("--drop-threshold-5m-bps", type=float, default=10.0)
    parser.add_argument("--drop-threshold-15m-bps", type=float, default=15.0)
    args = parser.parse_args()

    if args.smoke:
        args.days = min(args.days, 14)
        args.max_features = min(args.max_features, 64)
        args.max_train_rows = args.max_train_rows or 15000
        args.models = "logistic,histgb"

    config = DirectionalBigMoveConfig(
        symbol=args.symbol,
        days=args.days,
        horizons=sorted(set(args.horizons)),
        models=base.parse_models(args.models),
        output_prefix=args.output_prefix,
        start=args.start,
        end=args.end,
        smoke=args.smoke,
        rebuild_cache=args.rebuild_cache,
        max_features=args.max_features,
        max_train_rows=args.max_train_rows or None,
        n_jobs=args.n_jobs,
        device=args.device,
        save_models=not args.no_save_models,
        threshold_5m_bps=args.threshold_5m_bps,
        threshold_15m_bps=args.threshold_15m_bps,
        drop_threshold_5m_bps=args.drop_threshold_5m_bps,
        drop_threshold_15m_bps=args.drop_threshold_15m_bps,
    )
    train_directional_bigmove(config)


if __name__ == "__main__":
    main()
