"""Validate an Event Evidence Accumulator campaign result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "research" / "event_evidence_accumulator"
REQUIRED_FILES = {
    "RESULTS.md",
    "campaign_script_snapshot.py",
    "candidate_by_day.csv",
    "configuration_selection.csv",
    "effective_sample_size.json",
    "frozen_protocol_snapshot.json",
    "locked_candidate_metrics.csv",
    "locked_candidates.parquet",
    "locked_state_transitions.parquet",
    "manifest.json",
    "run.log",
    "selected_research_aggregators.joblib",
}


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_run(output_root: Path) -> Path:
    runs = sorted(
        path
        for path in output_root.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )
    if not runs:
        raise FileNotFoundError(f"no accumulator runs under {output_root}")
    return runs[-1]


def validate_run(run_dir: Path) -> dict[str, Any]:
    missing = sorted(REQUIRED_FILES - {path.name for path in run_dir.iterdir()})
    if missing:
        raise AssertionError(f"missing accumulator artifacts: {missing}")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    protocol_path = Path(manifest["protocol"])
    script_path = Path(manifest["script"])
    inputs = manifest["inputs"]
    integrity = {
        "protocol_hash_matches": (
            protocol_path.exists()
            and sha256_file(protocol_path) == manifest["protocol_sha256"]
        ),
        "script_hash_matches": (
            script_path.exists() and sha256_file(script_path) == manifest["script_sha256"]
        ),
        "development_input_hash_matches": (
            Path(inputs["development_predictions"]).exists()
            and sha256_file(Path(inputs["development_predictions"]))
            == inputs["development_sha256"]
        ),
        "locked_input_hash_matches": (
            Path(inputs["locked_predictions"]).exists()
            and sha256_file(Path(inputs["locked_predictions"])) == inputs["locked_sha256"]
        ),
        "production_artifacts_unchanged": (
            manifest["production_artifacts_changed"] is False
        ),
        "production_eligibility_blocked": manifest["eligible_for_production"] is False,
        "economic_evidence_unavailable": (
            manifest["economic_evidence_available"] is False
        ),
    }

    selection = pd.read_csv(run_dir / "configuration_selection.csv")
    candidates = pd.read_parquet(run_dir / "locked_candidates.parquet")
    metrics = pd.read_csv(run_dir / "locked_candidate_metrics.csv")
    transitions = pd.read_parquet(run_dir / "locked_state_transitions.parquet")
    probability_columns = ["p_baseline", "p_evidence"]
    probabilities = candidates[probability_columns].to_numpy(float)
    structure = {
        "nine_frozen_configurations": (
            selection[["scheme", "half_life_seconds"]].drop_duplicates().shape[0] == 9
        ),
        "one_candidate_per_market": not bool(candidates.duplicated("market_id").any()),
        "candidate_market_count_matches_rows": (
            candidates["market_id"].nunique() == len(candidates)
        ),
        "candidate_probabilities_finite_and_bounded": bool(
            np.isfinite(probabilities).all()
            and ((probabilities >= 0.0) & (probabilities <= 1.0)).all()
        ),
        "candidate_horizons_exact": (
            set(candidates["market_horizon_minutes"].unique()) == {5, 15}
        ),
        "confirmed_transition_per_candidate": (
            transitions["state_after"].str.startswith("CONFIRMED_").sum()
            == len(candidates)
        ),
        "metric_scopes_exact": set(metrics["scope"]) == {"all", "5m", "15m"},
        "all_candidates_have_positive_seconds_left": bool(
            (candidates["seconds_left"] > 0).all()
        ),
    }

    target = candidates["settlement_up"].to_numpy(int)
    candidate_side = (candidates["candidate_side_value"].to_numpy(float) > 0).astype(int)
    current_side = (candidates["current_side"].to_numpy(float) > 0).astype(int)
    baseline_side = (candidates["p_baseline"].to_numpy(float) >= 0.5).astype(int)
    evidence_side = (candidates["p_evidence"].to_numpy(float) >= 0.5).astype(int)
    diagnostics = {
        "candidates": len(candidates),
        "candidate_direction_accuracy": float((candidate_side == target).mean()),
        "current_anchor_side_accuracy": float((current_side == target).mean()),
        "baseline_probability_accuracy": float((baseline_side == target).mean()),
        "evidence_probability_accuracy": float((evidence_side == target).mean()),
        "candidate_agreement_with_current_side": float(
            (candidate_side == current_side).mean()
        ),
        "candidate_agreement_with_baseline": float(
            (candidate_side == baseline_side).mean()
        ),
        "incremental_accuracy_vs_current_side": float(
            (candidate_side == target).mean() - (current_side == target).mean()
        ),
        "incremental_accuracy_vs_baseline": float(
            (candidate_side == target).mean() - (baseline_side == target).mean()
        ),
    }
    report = {
        "run_id": run_dir.name,
        "integrity_passed": bool(all(integrity.values()) and all(structure.values())),
        "integrity": integrity,
        "structure": structure,
        "diagnostics": diagnostics,
        "continuation_gates_passed": bool(manifest["continuation_gates_passed"]),
        "eligible_for_production": False,
    }
    (run_dir / "validation.json").write_text(
        json.dumps(json_safe(report), indent=2, allow_nan=False), encoding="utf-8"
    )
    return json_safe(report)


def selftest() -> int:
    candidate = np.array([1, 0, 1, 0])
    target = np.array([1, 0, 0, 1])
    if float((candidate == target).mean()) != 0.5:
        raise AssertionError("accuracy comparison failed")
    print("SELFTEST PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    run_dir = args.run_dir.resolve() if args.run_dir else latest_run(args.output_root)
    report = validate_run(run_dir)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
