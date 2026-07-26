"""Forward scorer for the W90/W400/W1265 multi-window experts.

The training harness writes shadow-only models. This program applies every saved
expert to the same rows that arrive after its training boundary and persists one
canonical probability per model and prediction timestamp. It never promotes or
routes a live decision.
"""
from __future__ import annotations

import argparse
import gc
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from artifact_identity import artifact_compatibility, load_json  # noqa: E402
from research.multiwindow_experiment import (  # noqa: E402
    build_causal_features,
    target_for_horizon,
)


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
RUN_ROOT = DATA / "research" / "multiwindow_experts"
MATRIX = DATA / "research_matrix_1m.parquet"
OUTPUT_NAME = "forward_shadow_predictions.parquet"
MODEL_PATTERN = re.compile(r"^(?P<expert>.+)__(?P<family>.+)\.joblib$")


def _log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def _latest_valid_run(root: Path) -> Path:
    candidates = sorted(
        (
            path for path in root.iterdir()
            if path.is_dir()
            and (path / "artifact_manifest.json").is_file()
            and (path / "run_manifest.json").is_file()
            and not path.name.startswith("INVALID_")
        ),
        reverse=True,
    ) if root.is_dir() else []
    if not candidates:
        raise FileNotFoundError(f"no valid multi-window run under {root}")
    return candidates[0]


def _verify_run(run_dir: Path) -> dict[str, Any]:
    run_manifest = load_json(run_dir / "run_manifest.json")
    if not run_manifest:
        raise RuntimeError(f"missing or invalid run manifest: {run_dir}")
    ok, reasons = artifact_compatibility(run_dir, run_manifest, strict=True)
    if not ok:
        raise RuntimeError("shadow run failed artifact verification: " + "; ".join(reasons))
    if not run_manifest.get("shadow_only") or run_manifest.get("automatic_promotion"):
        raise RuntimeError("refusing a run that is not explicitly shadow-only")
    return run_manifest


def _model_files(run_dir: Path, run_manifest: dict[str, Any]) -> list[Path]:
    files = [
        run_dir / relative
        for relative in run_manifest.get("model_files", [])
        if str(relative).endswith(".joblib")
    ]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing shadow model files: " + ", ".join(missing[:5]))
    if not files:
        raise RuntimeError("run manifest contains no shadow model files")
    return files


def _existing_state(output_path: Path) -> dict[tuple[str, str], bool]:
    if not output_path.is_file():
        return {}
    old = pd.read_parquet(
        output_path, columns=["prediction_id", "model_id", "resolved"]
    )
    return {
        (str(row.prediction_id), str(row.model_id)): bool(row.resolved)
        for row in old.itertuples(index=False)
    }


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def score_once(run_dir: Path, matrix_path: Path) -> dict[str, int]:
    run_manifest = _verify_run(run_dir)
    models = _model_files(run_dir, run_manifest)
    trained_through = []
    for path in models:
        payload = joblib.load(path)
        trained_through.append(int(payload["trained_through_ts_ms"]))
        del payload
    context_start = min(trained_through) - 2 * 86_400_000
    frame = pd.read_parquet(
        matrix_path,
        filters=[("ts_ms", ">=", int(context_start))],
    )
    frame = (
        frame.sort_values("ts_ms")
        .drop_duplicates("ts_ms", keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        return {"new_rows": 0, "models": len(models)}

    features = build_causal_features(frame)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="coerce").to_numpy(dtype=np.int64)
    output_path = run_dir / OUTPUT_NAME
    existing = _existing_state(output_path)
    new_rows: list[dict[str, Any]] = []

    for number, path in enumerate(models, start=1):
        payload = joblib.load(path)
        model = payload["model"]
        feature_names = list(payload["features"])
        missing = sorted(set(feature_names).difference(features.columns))
        if missing:
            raise RuntimeError(f"{path.name} missing scoring features: {missing}")
        target = str(payload["target"])
        horizon_match = re.search(r"_(\d+)m$", target)
        if not horizon_match:
            raise RuntimeError(f"cannot derive horizon from target {target!r}")
        horizon = int(horizon_match.group(1))
        target_values = target_for_horizon(frame, horizon)
        eligible = np.flatnonzero(
            timestamps > int(payload["trained_through_ts_ms"])
        )
        if not len(eligible):
            del payload, model
            gc.collect()
            continue
        probability = np.asarray(
            model.predict_proba(features.iloc[eligible][feature_names])[:, 1],
            dtype=float,
        )
        model_match = MODEL_PATTERN.match(path.name)
        model_id = path.relative_to(run_dir).as_posix()
        expert = str(payload.get("expert") or (
            model_match.group("expert") if model_match else ""
        ))
        family = (
            model_match.group("family") if model_match else path.stem
        )
        for idx, p_up in zip(eligible, probability):
            prediction_id = f"{target}-{int(timestamps[idx])}"
            key = (prediction_id, model_id)
            actual = int(target_values[idx])
            if existing.get(key, False) or (key in existing and actual < 0):
                continue
            new_rows.append(
                {
                    "prediction_id": prediction_id,
                    "model_id": model_id,
                    "run_id": run_manifest.get("run_id"),
                    "ts_ms": int(timestamps[idx]),
                    "target": target,
                    "horizon": horizon,
                    "expert": expert,
                    "family": family,
                    "p_up": float(p_up),
                    "actual_up": actual if actual >= 0 else None,
                    "resolved": bool(actual >= 0),
                    "trained_through_ts_ms": int(payload["trained_through_ts_ms"]),
                    "scored_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "shadow_only": True,
                }
            )
        _log(
            f"[{number}/{len(models)}] {target} {expert}/{family}: "
            f"eligible={len(eligible):,}"
        )
        del payload, model, probability
        gc.collect()

    if new_rows:
        additions = pd.DataFrame(new_rows)
        if output_path.is_file():
            previous = pd.read_parquet(output_path)
            combined = pd.concat([previous, additions], ignore_index=True)
        else:
            combined = additions
        combined = (
            combined.sort_values(["ts_ms", "target", "model_id"])
            .drop_duplicates(["prediction_id", "model_id"], keep="last")
        )
        _atomic_parquet(combined, output_path)
    return {"new_rows": len(new_rows), "models": len(models)}


def selftest() -> None:
    assert MODEL_PATTERN.match("W90__logreg.joblib")
    assert not MODEL_PATTERN.match("not-a-model.txt")
    print("window_expert_shadow self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir")
    parser.add_argument("--matrix", default=str(MATRIX))
    parser.add_argument(
        "--watch-seconds",
        type=int,
        default=0,
        help="0 scores once; positive values rescore after this interval",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0

    run_dir = Path(args.run_dir) if args.run_dir else _latest_valid_run(RUN_ROOT)
    matrix_path = Path(args.matrix)
    if not matrix_path.is_file():
        raise SystemExit(f"matrix not found: {matrix_path}")
    while True:
        result = score_once(run_dir, matrix_path)
        _log(
            f"[shadow] run={run_dir.name} models={result['models']} "
            f"new_predictions={result['new_rows']}"
        )
        if args.watch_seconds <= 0:
            return 0
        time.sleep(max(30, args.watch_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
