"""CLI and immutable report lifecycle for every Phase 5B experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from research.phase5_standalone.common import (
    block_bootstrap,
    cost_model,
    matched_controls,
    metrics,
    modeling,
    protocol,
    temporal_split,
)
from research.phase5_standalone.common.engine_types import EngineContext
from research.phase5_standalone.common.report_writer import (
    git_commit,
    git_worktree_dirty,
    source_tree_hash,
    write_report,
)

from . import data
from .engines import ENGINES, execute


REPO = Path(__file__).resolve().parents[3]


def parser_for(experiment_id: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Phase 5B standalone: {experiment_id}")
    parser.add_argument("--data-dir", default=str(REPO / "data"))
    parser.add_argument("--output", default=str(
        REPO / "data" / "research" / "phase5b_standalone" / experiment_id / "latest"))
    parser.add_argument("--seed", type=int, default=20260802)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--train-end")
    parser.add_argument("--calibration-end")
    parser.add_argument("--policy-end")
    parser.add_argument("--test-end")
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    parser.add_argument("--maximum-rows", type=int, default=100_000)
    return parser


def _selftest(protocol_path: Path) -> int:
    frozen = protocol.load_protocol(protocol_path)
    assert frozen.engine in ENGINES, f"unregistered engine {frozen.engine}"
    protocol.selftest()
    temporal_split.selftest()
    cost_model.selftest()
    matched_controls.selftest()
    block_bootstrap.selftest()
    metrics.selftest()
    modeling.selftest()
    with tempfile.TemporaryDirectory(prefix="phase5b-selftest-") as temp:
        data.selftest(Path(temp))
    print(f"SELFTEST PASS: {frozen.experiment_id} protocol={frozen.sha256[:12]}")
    return 0


def run_cli(protocol_path: str | Path, argv: list[str] | None = None) -> int:
    frozen = protocol.load_protocol(protocol_path)
    args = parser_for(frozen.experiment_id).parse_args(argv)
    if args.selftest:
        return _selftest(Path(protocol_path))
    if args.maximum_rows < 0:
        raise SystemExit("--maximum-rows must be >= 0")
    if args.cost_multiplier <= 0:
        raise SystemExit("--cost-multiplier must be > 0")
    context = EngineContext(
        protocol=frozen,
        data_dir=Path(args.data_dir).resolve(),
        maximum_rows=int(args.maximum_rows),
        seed=int(args.seed),
        cost_multiplier=float(args.cost_multiplier),
        split_args={
            "train_end": args.train_end,
            "calibration_end": args.calibration_end,
            "policy_end": args.policy_end,
            "test_end": args.test_end,
        },
        dry_run=bool(args.dry_run),
    )
    result = execute(context)
    payload = {
        "experiment_id": frozen.experiment_id,
        "question": frozen.payload["question"],
        "status": result.status,
        "summary": result.summary,
        "reasons": result.reasons,
        "protocol_sha256": frozen.sha256,
        "code_commit": git_commit(REPO),
        "git_worktree_dirty": git_worktree_dirty(REPO),
        "suite_code_sha256": source_tree_hash(REPO / "research" / "phase5b_standalone"),
        "cost_multiplier": context.cost_multiplier,
        "maximum_rows": context.maximum_rows,
        "split_manifest": result.split_manifest,
        "data_identity": result.data_identity,
        "causal_summary": result.causal_summary,
        "diagnostics": result.diagnostics,
        "economics": result.economics,
        "controls_declared": frozen.payload["controls"],
        "capital_authority": False,
    }
    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
        return 0
    output = Path(args.output).resolve()
    report = write_report(output, payload)
    shutil.copy2(Path(protocol_path), output / "frozen_protocol_snapshot.json")
    print(f"{frozen.experiment_id}: {result.status}")
    print(f"report: {report}")
    for reason in result.reasons:
        print(f"  - {reason}")
    return 0


def standalone_entry(script_file: str, argv: list[str] | None = None) -> int:
    return run_cli(Path(script_file).with_name("frozen_protocol.json"), argv)
