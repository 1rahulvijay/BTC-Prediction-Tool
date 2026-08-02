#!/usr/bin/env python
"""Train the portable research-only event scorer used by the repricing shadow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (BACKEND, RESEARCH):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from train_event_time_specialists import (
    build_causal_features,
    build_first_barrier_labels,
    capped_indices,
    load_event_window,
    model_factory,
    period_masks,
    positive_probability,
)

from polymarket_repricing_shadow_v1.event_features import FEATURE_NAMES

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "research"
    / "polymarket_repricing_shadow_v1"
    / "event_model_bundle.joblib"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_named_arrays(values: dict[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(values):
        digest.update(name.encode())
        value = values[name]
        array = np.ascontiguousarray(value)
        digest.update(str(array.dtype).encode())
        digest.update(json.dumps(array.shape).encode())
        view = memoryview(array).cast("B")
        for start in range(0, len(view), 4 * 1024 * 1024):
            digest.update(view[start : start + 4 * 1024 * 1024])
    return digest.hexdigest()


def code_identity() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip().lower()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    return commit, dirty


def train(output: Path) -> Path:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    config = protocol["models"]
    dates = (
        pd.date_range(
            config["event_training_start"],
            config["event_training_end"],
            freq="D",
            tz="UTC",
        )
        .strftime("%Y-%m-%d")
        .tolist()
    )
    events = load_event_window(dates)
    features, anchors = build_causal_features(
        events,
        sample_every_seconds=int(config["event_sample_seconds"]),
        max_horizon=max(config["event_horizons_seconds"]),
    )
    if list(features.columns) != FEATURE_NAMES:
        raise ValueError("offline and live event feature schemas differ")
    matrix = features.to_numpy(np.float32)
    timestamps = events["timestamp_s"][anchors]
    dataset_sha256 = sha256_named_arrays(events)
    calibration_cutoffs = []
    for horizon in config["event_horizons_seconds"]:
        masks = period_masks(timestamps, horizon=int(horizon))
        calibration_index = np.flatnonzero(masks["calibration"])
        if not len(calibration_index):
            raise ValueError(f"{horizon}s calibration split is empty")
        calibration_cutoffs.append(
            int(timestamps[calibration_index].max()) + int(horizon)
        )
    commit, dirty = code_identity()
    bundle: dict[str, Any] = {
        "bundle_version": "polymarket-repricing-event-v1",
        "promotion_status": "research_only",
        "serving_enabled": False,
        "paper_enabled": False,
        "live_enabled": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "feature_names": FEATURE_NAMES,
        "horizons_seconds": config["event_horizons_seconds"],
        "heads": config["event_heads"],
        "models": {},
        "training_start": config["event_training_start"],
        "training_end": config["event_training_end"],
        "training_cutoff_ns": max(calibration_cutoffs) * 1_000_000_000,
        "dataset_sha256": dataset_sha256,
        "feature_schema_sha256": hashlib.sha256(
            json.dumps(
                FEATURE_NAMES, separators=(",", ":"), ensure_ascii=True
            ).encode()
        ).hexdigest(),
        "code_commit": commit,
        "code_dirty": dirty,
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
    }
    barriers = {5: 1.0, 15: 1.5}
    for horizon in config["event_horizons_seconds"]:
        labels = build_first_barrier_labels(
            events["spot_last"],
            anchors,
            horizon_seconds=int(horizon),
            barrier_bps=barriers[int(horizon)],
        )
        masks = period_masks(timestamps, horizon=int(horizon))
        bundle["models"][str(horizon)] = {}
        direction = labels["direction"].to_numpy(np.int8)
        for head in config["event_heads"]:
            target = labels[head].to_numpy(np.int8)
            valid = (
                direction >= 0 if head == "direction" else np.ones(len(target), bool)
            )
            train_index = capped_indices(
                np.flatnonzero(masks["train"] & valid),
                int(config["event_max_train_rows"]),
            )
            calibration_index = np.flatnonzero(masks["calibration"] & valid)
            members = []
            for name in config["event_models"]:
                print(f"[train] h={horizon}s head={head} model={name}", flush=True)
                model = model_factory(name, int(config["threads"]))()
                model.fit(matrix[train_index], target[train_index])
                raw = positive_probability(model, matrix[calibration_index])
                calibrator = IsotonicRegression(out_of_bounds="clip")
                calibrator.fit(raw, target[calibration_index])
                members.append({"name": name, "model": model, "calibrator": calibrator})
            bundle["models"][str(horizon)][head] = members
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + f".{os.getpid()}.tmp")
    joblib.dump(bundle, temporary, compress=3)
    os.replace(temporary, output)
    write_integrity_manifest(output)
    manifest = {
        "path": str(output),
        "sha256": sha256_file(output),
        "bytes": output.stat().st_size,
        "feature_count": len(FEATURE_NAMES),
        "horizons_seconds": bundle["horizons_seconds"],
        "heads": bundle["heads"],
        "training_cutoff_ns": bundle["training_cutoff_ns"],
        "dataset_sha256": bundle["dataset_sha256"],
        "feature_schema_sha256": bundle["feature_schema_sha256"],
        "code_commit": bundle["code_commit"],
        "code_dirty": bundle["code_dirty"],
        "promotion_status": "research_only",
        "serving_enabled": False,
        "paper_enabled": False,
        "live_enabled": False,
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"[done] {output} sha256={manifest['sha256']}", flush=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    train(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
