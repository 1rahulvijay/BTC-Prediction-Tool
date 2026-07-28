"""Validate a frozen Economic V2 campaign without modifying its artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "research" / "economic_v2"
REQUIRED_FILES = {
    "e1_factor_blocks.csv",
    "e1_factor_buckets.csv",
    "e1_factor_summary.csv",
    "e2_checkpoint_dataset.parquet",
    "e2_diagnostics.json",
    "e2_execution_stress.csv",
    "e2_locked_predictions.parquet",
    "e2_probability_metrics.csv",
    "e2_signals.csv",
    "campaign_script_snapshot.py",
    "frozen_protocol_snapshot.json",
    "manifest.json",
    "RESULTS.md",
    "run.log",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_run(output_root: Path) -> Path:
    candidates = sorted(
        path
        for path in output_root.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )
    if not candidates:
        raise FileNotFoundError(f"no Economic V2 runs under {output_root}")
    return candidates[-1]


def finite_unit_interval(frame: pd.DataFrame, columns: list[str]) -> bool:
    values = frame[columns].to_numpy(float)
    return bool(np.isfinite(values).all() and ((values >= 0.0) & (values <= 1.0)).all())


def evaluate_gates(
    factor: pd.DataFrame,
    blocks: pd.DataFrame,
    probability: pd.DataFrame,
    execution: pd.DataFrame,
) -> dict[str, Any]:
    positive_block_fraction = (
        blocks.assign(positive=blocks["residual_direction_ic"] > 0)
        .groupby(["era", "horizon"])["positive"]
        .mean()
    )
    factor_gates = {
        "positive_direction_ic_all_eras": bool(
            (factor["residual_direction_return_spearman"] > 0).all()
        ),
        "positive_in_at_least_75pct_of_blocks_each_slice": bool(
            (positive_block_fraction >= 0.75).all()
        ),
        "positive_bucket_monotonicity_each_slice": bool(
            (factor["direction_bucket_monotonicity"] > 0).all()
        ),
        "positive_top_decile_net_after_costs_each_slice": bool(
            (factor["top_decile_selected_net_bps"] > 0).all()
        ),
    }
    scoped_probability = probability[probability["scope"] == "all"].set_index("model")
    baseline = scoped_probability.loc["market_calibrated"]
    residual = scoped_probability.loc["residual_ensemble"]

    def execution_row(delay: int, slippage: float) -> pd.Series:
        selected = execution[
            (execution["policy"] == "residual_ensemble")
            & (execution["scope"] == "all")
            & (execution["delay_seconds"] == delay)
            & np.isclose(execution["slippage_stress"], slippage)
        ]
        if len(selected) != 1:
            raise AssertionError(
                f"expected one residual execution row for delay={delay}, "
                f"slippage={slippage}; found {len(selected)}"
            )
        return selected.iloc[0]

    immediate = execution_row(0, 0.0)
    stressed = execution_row(2, 0.01)
    residual_gates = {
        "brier_better_than_calibrated_market": bool(residual["brier"] < baseline["brier"]),
        "log_loss_better_than_calibrated_market": bool(
            residual["log_loss"] < baseline["log_loss"]
        ),
        "positive_immediate_mean_pnl_after_fee": bool(
            immediate["mean_pnl_per_share"] > 0
        ),
        "positive_2s_delay_1c_stress_mean_pnl": bool(
            stressed["mean_pnl_per_share"] > 0
        ),
        "at_least_200_locked_test_trades": bool(stressed["trades"] >= 200),
        "positive_bootstrap_lower_95": bool(stressed["bootstrap_lower_95"] > 0),
        "profit_factor_above_1_10": bool(stressed["profit_factor"] > 1.10),
    }
    return {
        "E1_LONG_SHORT_FACTOR_DECOMPOSITION": {
            "passed": bool(all(factor_gates.values())),
            "gates": factor_gates,
            "minimum_positive_block_fraction": float(positive_block_fraction.min()),
        },
        "E2_POLYMARKET_MARKET_RESIDUAL": {
            "passed": bool(all(residual_gates.values())),
            "gates": residual_gates,
            "market_calibrated_brier": float(baseline["brier"]),
            "residual_ensemble_brier": float(residual["brier"]),
            "market_calibrated_log_loss": float(baseline["log_loss"]),
            "residual_ensemble_log_loss": float(residual["log_loss"]),
            "stressed_trades": int(stressed["trades"]),
            "stressed_mean_pnl_per_share": float(stressed["mean_pnl_per_share"]),
            "stressed_profit_factor": float(stressed["profit_factor"]),
            "stressed_bootstrap_lower_95": float(stressed["bootstrap_lower_95"]),
        },
    }


def validate_run(run_dir: Path) -> dict[str, Any]:
    missing = sorted(REQUIRED_FILES - {path.name for path in run_dir.iterdir()})
    if missing:
        raise AssertionError(f"missing campaign artifacts: {missing}")

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    protocol_path = Path(manifest["protocol"])
    script_path = Path(manifest["script"])
    integrity = {
        "protocol_hash_matches_manifest": (
            protocol_path.exists()
            and sha256_file(protocol_path) == manifest["protocol_sha256"]
        ),
        "script_hash_matches_manifest": (
            script_path.exists() and sha256_file(script_path) == manifest["script_sha256"]
        ),
        "production_artifacts_unchanged": (
            manifest.get("production_artifacts_changed") is False
        ),
        "manifest_blocks_production": manifest.get("eligible_for_production") is False,
    }

    checkpoint = pd.read_parquet(run_dir / "e2_checkpoint_dataset.parquet")
    locked = pd.read_parquet(run_dir / "e2_locked_predictions.parquet")
    signals = pd.read_csv(run_dir / "e2_signals.csv")
    factor = pd.read_csv(run_dir / "e1_factor_summary.csv")
    blocks = pd.read_csv(run_dir / "e1_factor_blocks.csv")
    probability = pd.read_csv(run_dir / "e2_probability_metrics.csv")
    execution = pd.read_csv(run_dir / "e2_execution_stress.csv")

    probability_columns = [
        column
        for column in locked.columns
        if column.startswith("p_")
        and column
        not in {
            "p_hold_cur",
            "p_hold_up",
            "p_hold_down",
        }
    ]
    structure = {
        "one_split_per_market": bool(
            (checkpoint.groupby("slug")["split"].nunique() == 1).all()
        ),
        "no_duplicate_market_checkpoint": not bool(
            checkpoint.duplicated(["slug", "checkpoint"]).any()
        ),
        "locked_rows_are_test_only": set(locked["split"].unique()) == {"test"},
        "probabilities_finite_and_bounded": finite_unit_interval(
            locked, probability_columns
        ),
        "one_signal_per_policy_market": not bool(
            signals.duplicated(["policy", "slug"]).any()
        ),
        "signals_are_locked_test_only": set(signals["split"].unique()) == {"test"},
        "expected_execution_grid_rows": len(execution) == 60,
        "factor_slices_complete": len(factor) == 4,
    }
    gates = evaluate_gates(factor, blocks, probability, execution)
    passed_integrity = bool(all(integrity.values()) and all(structure.values()))
    eligible = bool(
        passed_integrity
        and all(experiment["passed"] for experiment in gates.values())
        and manifest.get("eligible_for_production") is True
    )
    report = {
        "run_id": run_dir.name,
        "integrity_passed": passed_integrity,
        "integrity": integrity,
        "structure": structure,
        "experiments": gates,
        "eligible_for_production": eligible,
    }
    (run_dir / "validation.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    return report


def selftest() -> int:
    factor = pd.DataFrame(
        {
            "era": ["a", "b"],
            "horizon": [5, 5],
            "residual_direction_return_spearman": [0.1, 0.1],
            "direction_bucket_monotonicity": [0.8, 0.9],
            "top_decile_selected_net_bps": [1.0, 2.0],
        }
    )
    blocks = pd.DataFrame(
        {
            "era": ["a"] * 4 + ["b"] * 4,
            "horizon": [5] * 8,
            "residual_direction_ic": [0.1, 0.2, 0.3, 0.4] * 2,
        }
    )
    probability = pd.DataFrame(
        {
            "scope": ["all", "all"],
            "model": ["market_calibrated", "residual_ensemble"],
            "brier": [0.20, 0.18],
            "log_loss": [0.60, 0.55],
        }
    )
    execution = pd.DataFrame(
        {
            "policy": ["residual_ensemble", "residual_ensemble"],
            "scope": ["all", "all"],
            "delay_seconds": [0, 2],
            "slippage_stress": [0.0, 0.01],
            "trades": [250, 240],
            "mean_pnl_per_share": [0.02, 0.01],
            "profit_factor": [1.3, 1.2],
            "bootstrap_lower_95": [0.005, 0.002],
        }
    )
    gates = evaluate_gates(factor, blocks, probability, execution)
    if not all(experiment["passed"] for experiment in gates.values()):
        raise AssertionError("gate evaluator should pass the synthetic candidate")
    if not finite_unit_interval(pd.DataFrame({"p": [0.0, 0.5, 1.0]}), ["p"]):
        raise AssertionError("probability bound check failed")
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
    run_dir = args.run_dir or latest_run(args.output_root)
    report = validate_run(run_dir.resolve())
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
