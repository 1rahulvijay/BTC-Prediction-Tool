#!/usr/bin/env python
"""
Research-only advanced sequence model bakeoff for BTC 5m/15m targets.

Models:
  - VLSTM: variational-dropout LSTM
  - LPatchTST: lightweight PatchTST-style transformer
  - PatchTST: larger PatchTST-style transformer
  - iTransformer: inverted feature-token transformer
  - Mamba / Mamba2 / VSN+Mamba2: optional; skipped unless mamba_ssm is installed

This script reuses the leak-safe 180d feature/target builder from
train_360d_multitarget_forecaster.py and writes separate research outputs. It does
not modify live app models, production artifacts, DuckDB, or bot logic.
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import train_360d_multitarget_forecaster as base

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = ROOT / "data" / "research"
MODEL_DIR = ROOT / "data" / "saved_models" / "research_advanced_sequence"
LOG_DIR = ROOT / "data" / "logs"


def log(msg: str) -> None:
    print(f"{pd.Timestamp.now().strftime('%H:%M:%S')} {msg}", flush=True)


@dataclass
class AdvConfig:
    days: int
    horizons: list[int]
    models: list[str]
    output_prefix: str
    device: str
    max_features: int
    seq_len: int
    seq_max_rows: int
    batch_size: int
    epochs: int
    lr: float
    save_models: bool
    smoke: bool


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


def append_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
    rows.clear()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def output_path(config: AdvConfig, suffix: str) -> Path:
    return RESEARCH_DIR / f"{config.output_prefix}_{suffix}"


def parse_models(s: str) -> list[str]:
    if s.lower() in {"all", "advanced"}:
        return ["vlstm", "lpatchtst", "patchtst", "itransformer", "mamba", "mamba2", "vsn_mamba2"]
    aliases = {"patchtst": "patchtst", "patchtst": "patchtst", "mamb2": "mamba2", "mamba2": "mamba2", "vsn+mamba2": "vsn_mamba2"}
    out = []
    for item in s.split(","):
        k = item.strip().lower().replace("-", "_")
        if not k:
            continue
        out.append(aliases.get(k, k))
    return out


def device_for(requested: str) -> str:
    import torch

    if requested.lower() in {"gpu", "cuda", "auto"} and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_research_frame(config: AdvConfig) -> tuple[pd.DataFrame, list[str], dict[str, list[str]], np.ndarray, np.ndarray, np.ndarray]:
    cfg = base.RunConfig(
        symbol="BTCUSDT",
        days=config.days,
        horizons=config.horizons,
        models=[],
        output_prefix=config.output_prefix,
        start=None,
        end=None,
        smoke=config.smoke,
        rebuild_cache=False,
        max_features=config.max_features,
        max_train_rows=None,
        n_jobs=2,
        device=config.device,
        save_models=config.save_models,
        skip_regression=True,
        skip_classification=True,
        skip_quantile=True,
        skip_sequence=True,
        quantile_backends=[],
        include_sequence=True,
        sequence_targets="core",
        seq_len=config.seq_len,
        seq_max_features=config.max_features,
        seq_max_rows=config.seq_max_rows,
        seq_epochs=config.epochs,
        seq_batch_size=config.batch_size,
    )
    df = base.build_market_frame(cfg)
    df, feature_cols = base.add_features(df)
    df, target_map = base.add_targets(df, config.horizons)
    max_h = max(config.horizons)
    df = df.iloc[240 : len(df) - max_h].copy().reset_index(drop=True)
    df = df.dropna(subset=target_map["regression"] + target_map["classification"], how="any").reset_index(drop=True)
    train_idx, cal_idx, test_idx = base.chronological_splits(len(df))
    selected = base.select_features(df, feature_cols, train_idx, config.max_features)
    log(f"[data] rows={len(df):,} features={len(selected)} train={len(train_idx):,} cal={len(cal_idx):,} test={len(test_idx):,}")
    return df, selected, target_map, train_idx, cal_idx, test_idx


def metrics_reg(model: str, target: str, y: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    row = base.regression_metric_row(model, target, y, pred)
    row["family"] = "advanced_sequence"
    return row


def metrics_cls(model: str, target: str, y: np.ndarray, prob: np.ndarray) -> dict[str, Any]:
    row = base.classification_metric_row(model, target, y.astype(int), prob)
    row["family"] = "advanced_sequence"
    return row


def add_preds(rows: list[dict[str, Any]], timestamps: pd.Series, target: str, model: str, y: np.ndarray, pred: np.ndarray | None = None, prob: np.ndarray | None = None) -> None:
    for ts, yy, pp in zip(timestamps, y, pred if pred is not None else prob):
        rows.append(
            {
                "timestamp": ts,
                "split": "test",
                "target_name": target,
                "horizon": base.extract_horizon(target),
                "model_name": model,
                "y_true": float(yy),
                "y_pred": float(pp) if pred is not None else np.nan,
                "y_prob": float(pp) if prob is not None else np.nan,
            }
        )


def standardize_frame(df: pd.DataFrame, features: list[str], train_idx: np.ndarray) -> np.ndarray:
    from sklearn.preprocessing import StandardScaler

    work = df[features].replace([np.inf, -np.inf], np.nan).copy()
    med = work.iloc[train_idx].median(numeric_only=True)
    work = work.fillna(med).fillna(0.0)
    scaler = StandardScaler()
    scaler.fit(work.iloc[train_idx].values.astype(np.float32))
    return scaler.transform(work.values.astype(np.float32)).astype(np.float32)


def make_indices(indices: np.ndarray, valid_set: set[int], seq_len: int, max_rows: int | None) -> np.ndarray:
    use = np.array([i for i in indices if i in valid_set and i >= seq_len - 1], dtype=int)
    if max_rows and len(use) > max_rows:
        use = use[-max_rows:]
    return use


def run(config: AdvConfig) -> None:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, Dataset

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "run_config.json").write_text(json.dumps(config.__dict__, indent=2), encoding="utf-8")

    pred_csv = output_path(config, "predictions.csv")
    pred_parquet = output_path(config, "predictions.parquet")
    for p in [pred_csv, pred_parquet]:
        if p.exists():
            p.unlink()

    device = device_for(config.device)
    log(f"[start] advanced sequence models={config.models} device={device} config={config}")
    df, features, target_map, train_idx, cal_idx, test_idx = load_research_frame(config)
    x_all = standardize_frame(df, features, train_idx)
    train_all = np.concatenate([train_idx, cal_idx])
    train_all = train_all[-config.seq_max_rows :] if len(train_all) > config.seq_max_rows else train_all
    test_use = test_idx

    reg_targets = [t for t in target_map["regression"] if "return" in t]
    cls_targets = [t for t in target_map["classification"] if "direction" in t or "big_move" in t]
    targets = reg_targets + cls_targets

    class SeqDataset(Dataset):
        def __init__(self, x: np.ndarray, y: np.ndarray, indices: np.ndarray, seq_len: int):
            self.x = x
            self.y = y
            self.indices = indices
            self.seq_len = seq_len

        def __len__(self) -> int:
            return len(self.indices)

        def __getitem__(self, i: int):
            end = int(self.indices[i])
            start = end - self.seq_len + 1
            return torch.tensor(self.x[start : end + 1], dtype=torch.float32), torch.tensor(self.y[end], dtype=torch.float32)

    class VLSTM(nn.Module):
        def __init__(self, n_features: int):
            super().__init__()
            self.dropout_in = nn.Dropout(0.15)
            self.lstm = nn.LSTM(n_features, 96, num_layers=2, batch_first=True, dropout=0.20)
            self.head = nn.Sequential(nn.LayerNorm(96), nn.Dropout(0.15), nn.Linear(96, 1))

        def forward(self, x):
            out, _ = self.lstm(self.dropout_in(x))
            return self.head(out[:, -1]).squeeze(-1)

    class PatchTSTNet(nn.Module):
        def __init__(self, n_features: int, patch_len: int, stride: int, d_model: int, layers: int):
            super().__init__()
            self.patch_len = patch_len
            self.stride = stride
            self.proj = nn.Linear(n_features * patch_len, d_model)
            enc = nn.TransformerEncoderLayer(d_model=d_model, nhead=4, dim_feedforward=d_model * 3, dropout=0.1, batch_first=True)
            self.encoder = nn.TransformerEncoder(enc, num_layers=layers)
            self.head = nn.Sequential(nn.LayerNorm(d_model), nn.Linear(d_model, 1))

        def forward(self, x):
            patches = x.unfold(dimension=1, size=self.patch_len, step=self.stride)
            patches = patches.contiguous().permute(0, 1, 3, 2).flatten(2)
            z = self.encoder(self.proj(patches))
            return self.head(z.mean(dim=1)).squeeze(-1)

    class ITransformer(nn.Module):
        def __init__(self, n_features: int, seq_len: int):
            super().__init__()
            self.proj = nn.Linear(seq_len, 96)
            enc = nn.TransformerEncoderLayer(d_model=96, nhead=4, dim_feedforward=256, dropout=0.1, batch_first=True)
            self.encoder = nn.TransformerEncoder(enc, num_layers=2)
            self.head = nn.Sequential(nn.LayerNorm(96), nn.Linear(96, 1))

        def forward(self, x):
            z = x.transpose(1, 2)
            z = self.encoder(self.proj(z))
            return self.head(z.mean(dim=1)).squeeze(-1)

    def mamba_model(kind: str, n_features: int):
        try:
            from mamba_ssm import Mamba, Mamba2
        except Exception as exc:
            raise RuntimeError(f"mamba_ssm unavailable: {exc}") from exc

        block_cls = Mamba2 if kind in {"mamba2", "vsn_mamba2"} else Mamba

        class MambaNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.vsn = nn.Sequential(nn.Linear(n_features, n_features), nn.Sigmoid()) if kind == "vsn_mamba2" else None
                self.proj = nn.Linear(n_features, 96)
                self.block = block_cls(d_model=96)
                self.head = nn.Sequential(nn.LayerNorm(96), nn.Linear(96, 1))

            def forward(self, x):
                if self.vsn is not None:
                    x = x * self.vsn(x)
                z = self.block(self.proj(x))
                return self.head(z[:, -1]).squeeze(-1)

        return MambaNet()

    def make_model(name: str, n_features: int):
        if name == "vlstm":
            return VLSTM(n_features)
        if name == "lpatchtst":
            return PatchTSTNet(n_features, patch_len=8, stride=4, d_model=64, layers=1)
        if name == "patchtst":
            return PatchTSTNet(n_features, patch_len=16, stride=8, d_model=96, layers=2)
        if name == "itransformer":
            return ITransformer(n_features, config.seq_len)
        if name in {"mamba", "mamba2", "vsn_mamba2"}:
            return mamba_model(name, n_features)
        raise ValueError(f"unknown model {name}")

    reg_metrics: list[dict[str, Any]] = []
    cls_metrics: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for target_pos, target in enumerate(targets):
        is_cls = target in target_map["classification"]
        y = df[target].values.astype(np.float32)
        valid = np.where(~np.isnan(y))[0]
        valid_set = set(valid.tolist())
        tr_idx = make_indices(train_all, valid_set, config.seq_len, config.seq_max_rows)
        te_idx = make_indices(test_use, valid_set, config.seq_len, None)
        if len(tr_idx) < 1000 or len(te_idx) < 100:
            log(f"[skip] target={target} insufficient sequence rows train={len(tr_idx)} test={len(te_idx)}")
            continue
        train_ds = SeqDataset(x_all, y, tr_idx, config.seq_len)
        test_ds = SeqDataset(x_all, y, te_idx, config.seq_len)
        train_dl = DataLoader(train_ds, batch_size=config.batch_size, shuffle=True, drop_last=False)
        test_dl = DataLoader(test_ds, batch_size=config.batch_size, shuffle=False, drop_last=False)
        log(f"[target] {target} cls={is_cls} train_seq={len(train_ds):,} test_seq={len(test_ds):,}")

        for model_pos, model_name in enumerate(config.models):
            t0 = time.time()
            try:
                model = make_model(model_name, len(features)).to(device)
                loss_fn = nn.BCEWithLogitsLoss() if is_cls else nn.MSELoss()
                opt = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=1e-4)
                best_loss = math.inf
                for epoch in range(1, config.epochs + 1):
                    model.train()
                    total = 0.0
                    nobs = 0
                    for xb, yb in train_dl:
                        xb = xb.to(device)
                        yb = yb.to(device)
                        opt.zero_grad()
                        pred = model(xb)
                        loss = loss_fn(pred, yb)
                        loss.backward()
                        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                        opt.step()
                        total += float(loss.item()) * len(yb)
                        nobs += len(yb)
                    epoch_loss = total / max(1, nobs)
                    best_loss = min(best_loss, epoch_loss)
                    log(f"[train] {target}/{model_name} epoch={epoch}/{config.epochs} loss={epoch_loss:.6f}")

                model.eval()
                outs: list[float] = []
                ys: list[float] = []
                with torch.no_grad():
                    for xb, yb in test_dl:
                        out = model(xb.to(device)).detach().cpu().numpy()
                        if is_cls:
                            out = 1.0 / (1.0 + np.exp(-out))
                        outs.extend(out.tolist())
                        ys.extend(yb.numpy().tolist())
                pred = np.asarray(outs, dtype=float)
                yy = np.asarray(ys, dtype=float)
                ts = df.iloc[te_idx]["timestamp"].reset_index(drop=True)
                pred_rows: list[dict[str, Any]] = []
                if is_cls:
                    row = metrics_cls(model_name, target, yy, pred)
                    cls_metrics.append(row)
                    summary.append({"model_name": model_name, "target_name": target, "horizon": row["horizon"], "metric_main": "auc", "metric_value": row["auc"], "notes": "advanced_sequence"})
                    add_preds(pred_rows, ts, target, model_name, yy, prob=pred)
                else:
                    row = metrics_reg(model_name, target, yy, pred)
                    reg_metrics.append(row)
                    summary.append({"model_name": model_name, "target_name": target, "horizon": row["horizon"], "metric_main": "mae", "metric_value": row["mae"], "notes": "advanced_sequence"})
                    add_preds(pred_rows, ts, target, model_name, yy, pred=pred)
                append_csv(pred_csv, pred_rows, PRED_COLUMNS)
                if config.save_models:
                    path = MODEL_DIR / config.output_prefix / target / f"{model_name}.pt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    torch.save({"state_dict": model.state_dict(), "features": features, "config": config.__dict__}, path)
                fit_seconds = time.time() - t0
                inventory.append({"model_name": model_name, "target_name": target, "status": "ok", "fit_seconds": fit_seconds, "device": device, "train_rows": len(train_ds), "test_rows": len(test_ds), "error": ""})
                if target_pos == len(targets) - 1 and model_pos == len(config.models) - 1:
                    write_csv(output_path(config, "regression_metrics.csv"), reg_metrics)
                    write_csv(output_path(config, "classification_metrics.csv"), cls_metrics)
                    write_csv(output_path(config, "model_inventory.csv"), inventory)
                    write_csv(output_path(config, "summary.csv"), summary)
                    os._exit(0)
                log(f"[done] {target}/{model_name} in {fit_seconds:.1f}s")
            except Exception as exc:
                inventory.append({"model_name": model_name, "target_name": target, "status": "error", "fit_seconds": time.time() - t0, "device": device, "train_rows": len(train_ds), "test_rows": len(test_ds), "error": str(exc)[:500]})
                log(f"[skip] {target}/{model_name}: {exc}")
                if target_pos == len(targets) - 1 and model_pos == len(config.models) - 1:
                    write_csv(output_path(config, "regression_metrics.csv"), reg_metrics)
                    write_csv(output_path(config, "classification_metrics.csv"), cls_metrics)
                    write_csv(output_path(config, "model_inventory.csv"), inventory)
                    write_csv(output_path(config, "summary.csv"), summary)
                    os._exit(0)
            finally:
                try:
                    del model
                except Exception:
                    pass
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            write_csv(output_path(config, "regression_metrics.csv"), reg_metrics)
            write_csv(output_path(config, "classification_metrics.csv"), cls_metrics)
            write_csv(output_path(config, "model_inventory.csv"), inventory)
            write_csv(output_path(config, "summary.csv"), summary)
            if target_pos == len(targets) - 1 and model_pos == len(config.models) - 1:
                os._exit(0)

    # Keep the CUDA research process simple on Windows. A previous automatic
    # CSV->Parquet conversion completed but then triggered a native process
    # shutdown fault. The CSV files are the source of truth; convert to Parquet
    # in a separate non-CUDA process if needed after the run.
    # On Windows with CUDA PyTorch, interpreter teardown can crash after a
    # successful run. Output CSV/metric files are already flushed at this point.
    os._exit(0)


def main() -> None:
    p = argparse.ArgumentParser(description="Advanced sequence model research bakeoff.")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    p.add_argument("--models", default="advanced")
    p.add_argument("--output-prefix", default="forecast_180d_advanced_sequence")
    p.add_argument("--device", choices=["auto", "cpu", "gpu", "cuda"], default="auto")
    p.add_argument("--max-features", type=int, default=96)
    p.add_argument("--seq-len", type=int, default=60)
    p.add_argument("--seq-max-rows", type=int, default=80000)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--lr", type=float, default=0.001)
    p.add_argument("--no-save-models", action="store_true")
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args()
    if args.smoke:
        args.days = min(args.days, 7)
        args.max_features = min(args.max_features, 48)
        args.seq_max_rows = min(args.seq_max_rows, 5000)
        args.epochs = min(args.epochs, 1)
        args.batch_size = min(args.batch_size, 128)
    cfg = AdvConfig(
        days=args.days,
        horizons=sorted(set(args.horizons)),
        models=parse_models(args.models),
        output_prefix=args.output_prefix,
        device=args.device,
        max_features=args.max_features,
        seq_len=args.seq_len,
        seq_max_rows=args.seq_max_rows,
        batch_size=args.batch_size,
        epochs=args.epochs,
        lr=args.lr,
        save_models=not args.no_save_models,
        smoke=args.smoke,
    )
    run(cfg)


if __name__ == "__main__":
    main()
