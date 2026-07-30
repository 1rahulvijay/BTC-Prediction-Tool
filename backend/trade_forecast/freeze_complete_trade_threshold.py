"""Freeze the one M0 entry threshold from calibration data and a verified champion bundle."""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np

try:
    from artifact_identity import hash_paths
except ImportError:
    from backend.artifact_identity import hash_paths

from .champion_resolver import active_model_bundle
from .forward_evidence import ThresholdArtifact
from .model_common import (
    chronological_purged_split,
    load_verified_dataset,
    predict_classifier,
    predict_member_mean,
)
from .trade_schema import FEATURE_COLUMNS, M0_V2, policy_hash


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)
DEFAULT_OUTPUT = (
    DATA / "research" / "complete_trade_forecast" / "entry_threshold.json"
)


def threshold_from_scores(scores: np.ndarray, target_entry_rate: float) -> float:
    values = np.asarray(scores, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 100:
        raise RuntimeError("at least 100 eligible calibration scores are required")
    if not 0.0 < float(target_entry_rate) <= 1.0:
        raise ValueError("target_entry_rate must be in (0,1]")
    return float(np.quantile(values, 1.0 - float(target_entry_rate)))


def _manifest(path: Path) -> dict:
    target = path.with_suffix(".manifest.json")
    if not target.is_file():
        raise RuntimeError(f"artifact manifest missing: {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def freeze_threshold(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict:
    if output_path.exists():
        raise FileExistsError(f"threshold is already frozen: {output_path}")
    active = active_model_bundle()
    if not active.get("verified"):
        raise RuntimeError(
            "a verified promoted champion bundle is required before threshold freeze"
        )
    bundle_path = Path(active["path"])
    share_path = bundle_path / "complete_trade_share_path.pkl"
    execution_path = bundle_path / "complete_trade_execution_heads.pkl"
    if not share_path.is_file() or not execution_path.is_file():
        raise RuntimeError("champion bundle is missing share or execution artifact")

    frame, dataset_manifest = load_verified_dataset(dataset_path)
    share_manifest = _manifest(share_path)
    execution_manifest = _manifest(execution_path)
    expected_dataset = str(dataset_manifest["dataset_sha256"])
    for name, manifest in (
        ("share", share_manifest),
        ("execution", execution_manifest),
    ):
        if str(manifest.get("dataset_sha256") or "") != expected_dataset:
            raise RuntimeError(
                f"{name} artifact was not trained on the threshold dataset"
            )
        if str(manifest.get("policy_hash") or "") != policy_hash():
            raise RuntimeError(f"{name} artifact policy hash does not match")

    share = _verified_load(share_path)
    execution = _verified_load(execution_path)
    split = chronological_purged_split(frame)
    scores: list[np.ndarray] = []
    timestamps: list[np.ndarray] = []
    for horizon in sorted(int(value) for value in frame["horizon"].dropna().unique()):
        mask = np.asarray(split["calibration"], dtype=bool) & (
            frame["horizon"].to_numpy(dtype=int) == horizon
        )
        selected = frame.loc[mask].copy()
        if "candidate_valid" not in selected or "entry_complete" not in selected:
            raise RuntimeError("dataset lacks mandatory candidate/entry eligibility")
        selected = selected[
            (selected["candidate_valid"] == 1)
            & (selected["entry_complete"] == 1)
        ].dropna(subset=[*FEATURE_COLUMNS, "requested_qty", "round_start_ts"])
        if selected.empty:
            continue
        x = selected.loc[:, FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        finite = np.isfinite(x).all(axis=1)
        selected = selected.iloc[np.flatnonzero(finite)].copy()
        x = x[finite]
        if len(x) == 0:
            continue
        share_head = (
            ((share.get("horizons") or {}).get(horizon) or {}).get("events") or {}
        ).get(M0_V2["score_label"]) or {}
        quantile_heads = (
            ((execution.get("horizons") or {}).get(horizon) or {}).get("quantiles")
            or {}
        )
        capacity_models = (
            (quantile_heads.get("max_executable_qty") or {}).get(0.10)
        )
        cost_models = (
            (quantile_heads.get("entry_arrival_slippage") or {}).get(0.80)
        )
        if not share_head.get("supported") or not capacity_models or not cost_models:
            raise RuntimeError(
                f"horizon {horizon} lacks the direct M0 score, capacity q10, or cost q80 head"
            )
        capacity_q10 = np.asarray(
            predict_member_mean(capacity_models, x), dtype=float
        )
        cost_q80 = np.asarray(predict_member_mean(cost_models, x), dtype=float)
        quantity = selected["requested_qty"].to_numpy(dtype=float)
        eligible = (
            np.isfinite(capacity_q10)
            & np.isfinite(cost_q80)
            & np.isfinite(quantity)
            & (capacity_q10 + 1e-9 >= quantity)
        )
        if not eligible.any():
            continue
        probability = np.asarray(
            predict_classifier(
                share_head["members"], share_head.get("calibrator"), x[eligible]
            ),
            dtype=float,
        )
        scores.append(probability)
        timestamps.append(
            selected.loc[eligible, "round_start_ts"].to_numpy(dtype=float)
        )
    if not scores:
        raise RuntimeError("no eligible calibration candidates produced scores")
    all_scores = np.concatenate(scores)
    all_times = np.concatenate(timestamps)
    target_rate = float(M0_V2["target_entry_rate"])
    threshold = threshold_from_scores(all_scores, target_rate)
    code_hash = hash_paths([
        Path(__file__),
        Path(__file__).with_name("trade_schema.py"),
        Path(__file__).with_name("model_common.py"),
        Path(__file__).with_name("forward_evidence.py"),
    ])
    artifact = ThresholdArtifact(
        threshold=threshold,
        objective=f"P({M0_V2['realized_column']} > 0)",
        target_entry_rate=target_rate,
        calibration_start_ts=float(np.min(all_times)),
        calibration_end_ts=float(np.max(all_times)),
        calibration_rows=int(len(all_scores)),
        dataset_sha256=expected_dataset,
        model_sha256=str(active["bundle_hash"]),
        policy_sha256=policy_hash(),
        code_sha256=code_hash,
        created_at=time.time(),
    )
    artifact.save(output_path)
    return {
        "output": str(output_path),
        "threshold": threshold,
        "threshold_sha256": artifact.threshold_hash(),
        "calibration_rows": len(all_scores),
        "calibration_start_ts": artifact.calibration_start_ts,
        "calibration_end_ts": artifact.calibration_end_ts,
        "model_bundle_sha256": active["bundle_hash"],
        "bundle_manifest_sha256": active.get("bundle_manifest_sha256"),
        "policy_sha256": policy_hash(),
    }


def selftest() -> int:
    values = np.linspace(0.0, 1.0, 101)
    threshold = threshold_from_scores(values, 0.20)
    assert math.isfinite(threshold)
    assert abs(threshold - 0.8) < 1e-12
    print("freeze_complete_trade_threshold self-test: ALL PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    print(json.dumps(
        freeze_threshold(
            dataset_path=args.dataset.resolve(),
            output_path=args.output.resolve(),
        ),
        indent=2,
    ))
    return 0


def _verified_load(path):
    """Hash-check against the sidecar manifest BEFORE deserializing.

    Deserialization executes arbitrary code, so validating after loading has already lost.
    Pre-migration artifacts carry no manifest; they load while BTC_STRICT_ARTIFACT_IDENTITY
    is off and are counted as remaining debt."""
    import sys as _sys
    from pathlib import Path as _Path

    for _up in (1, 2, 3):
        _cand = str(_Path(__file__).resolve().parents[_up - 1])
        if (_Path(_cand) / "verified_io.py").is_file() and _cand not in _sys.path:
            _sys.path.insert(0, _cand)
    from verified_io import verified_load as _vl

    return _vl(path)


if __name__ == "__main__":
    raise SystemExit(main())