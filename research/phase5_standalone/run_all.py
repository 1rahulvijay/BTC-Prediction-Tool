"""Run or validate every Phase 5 standalone experiment in protocol order."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def experiments() -> list[Path]:
    packages = []
    for path in ROOT.glob("test_*/frozen_protocol.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        number = int(str(payload["experiment_id"]).split("_")[1])
        packages.append((number, path.parent))
    return [path for _, path in sorted(packages)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--data-dir", default=str(REPO / "data"))
    parser.add_argument("--output-root", default=str(REPO / "data" / "research" /
                                                     "phase5_standalone"))
    parser.add_argument("--maximum-rows", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260802)
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    args = parser.parse_args()

    packages = experiments()
    if len(packages) != 42:
        print(f"ERROR: expected 42 experiment packages, found {len(packages)}")
        return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    failures = 0
    for index, package in enumerate(packages, 1):
        protocol = json.loads((package / "frozen_protocol.json").read_text(encoding="utf-8"))
        experiment_id = protocol["experiment_id"]
        print(f"[{index:02d}/42] {experiment_id}", flush=True)
        if args.selftest:
            command = [sys.executable, str(package / "selftest.py")]
            report_path = None
        else:
            output = Path(args.output_root) / package.name / run_id
            command = [
                sys.executable, str(package / "run.py"),
                "--data-dir", str(args.data_dir),
                "--output", str(output),
                "--seed", str(args.seed),
                "--cost-multiplier", str(args.cost_multiplier),
                "--maximum-rows", str(args.maximum_rows if args.smoke else 0),
            ]
            report_path = output / "report.json"
        completed = subprocess.run(command, cwd=str(REPO), capture_output=True, text=True)
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        status = None
        if report_path and report_path.is_file():
            status = json.loads(report_path.read_text(encoding="utf-8")).get("status")
        rows.append({"experiment_id": experiment_id, "returncode": completed.returncode,
                     "status": status, "report": str(report_path) if report_path else None})
        failures += int(completed.returncode != 0)
    if not args.selftest:
        summary_dir = Path(args.output_root) / "_suite_runs" / run_id
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "suite_summary.json").write_text(
            json.dumps({"run_id": run_id, "mode": "smoke" if args.smoke else "full",
                        "experiments": rows}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        print(f"suite summary: {summary_dir / 'suite_summary.json'}")
    print(f"PHASE 5 SUITE: {'PASS' if failures == 0 else 'FAIL'} ({42 - failures}/42)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
