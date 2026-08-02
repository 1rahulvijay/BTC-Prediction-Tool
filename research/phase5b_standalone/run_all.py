"""Run every Phase 5B standalone experiment in numeric protocol order."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]


def experiments() -> list[tuple[int, Path, dict]]:
    rows = []
    for protocol in ROOT.glob("test_*/frozen_protocol.json"):
        payload = json.loads(protocol.read_text(encoding="utf-8"))
        number = int(payload["experiment_id"].split("_")[1])
        rows.append((number, protocol.parent, payload))
    return sorted(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selftest", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--data-dir", default=str(REPO / "data"))
    parser.add_argument("--output-root")
    parser.add_argument("--maximum-rows", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260802)
    args = parser.parse_args()
    rows = experiments()
    if len(rows) != 46:
        raise SystemExit(f"expected 46 experiments, found {len(rows)}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = Path(args.output_root or (
        REPO / "data" / "research" / "phase5b_standalone" / "_suite_runs" / run_id))
    summary = []
    failed_processes = []
    for index, (number, directory, payload) in enumerate(rows, 1):
        command = [sys.executable, str(directory / "run.py")]
        if args.selftest:
            command.append("--selftest")
        else:
            output = output_root / directory.name
            command += ["--data-dir", args.data_dir, "--output", str(output),
                        "--maximum-rows", str(args.maximum_rows if args.smoke else 0),
                        "--seed", str(args.seed)]
        print(f"[{index:02d}/46] {number:02d} {directory.name}", flush=True)
        result = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            failed_processes.append({"experiment": directory.name, "returncode": result.returncode})
        if not args.selftest:
            report = output_root / directory.name / "report.json"
            status = None
            if report.is_file():
                status = json.loads(report.read_text(encoding="utf-8"))["status"]
            summary.append({"number": number, "experiment": directory.name,
                            "status": status, "returncode": result.returncode})
    if not args.selftest:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "suite_summary.json").write_text(json.dumps({
            "run_id": run_id,
            "mode": "smoke" if args.smoke else "full",
            "maximum_rows": args.maximum_rows if args.smoke else 0,
            "experiments": summary,
            "process_failures": failed_processes,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"summary: {output_root / 'suite_summary.json'}")
    if failed_processes:
        print(f"FAIL: {len(failed_processes)} experiment process(es) failed", file=sys.stderr)
        return 1
    print(f"PASS: all {len(rows)} Phase 5B processes completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
