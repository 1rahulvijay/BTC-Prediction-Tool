#!/usr/bin/env python
"""Train the portable research-only event scorer used by the repricing shadow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    manifest = {
        "path": str(output),
        "sha256": sha256_file(output),
        "bytes": output.stat().st_size,
        "feature_count": len(FEATURE_NAMES),
        "horizons_seconds": bundle["horizons_seconds"],
        "heads": bundle["heads"],
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
