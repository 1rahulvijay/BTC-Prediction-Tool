"""Run every research script in sequence and summarise what each actually established.

    python research/run_all_sequence.py            # all scripts
    python research/run_all_sequence.py --fixed    # only the rewritten ones

WHAT THE SUMMARY MEANS
    The audit (backend/research/audit_research_claims.py) flags four disqualifying patterns.
    This runner executes the scripts and reports, per script, whether the audit still flags it
    and what the out-of-sample verdict was where one exists.

    A script that reports a large positive number AND is still flagged has not established
    anything. A script reporting a loss out-of-sample HAS established something: that this
    idea, at this horizon, on this data, does not pay after costs.
"""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REWRITTEN = {"v17_structural_arbitrage_test.py", "v19_god_mode_test.py",
             "v26_genetic_algorithm_test.py"}
AUTHORITATIVE_AUXILIARY = (
    "ceiling_analysis.py",
    "ceiling_levers_test.py",
    "maker_lever_test.py",
)

VERDICT = re.compile(r"VERDICT:\s*(.+)")
OOS_RETURN = re.compile(r"total return %\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)")


def order_key(path: Path):
    digits = "".join(c for c in path.name.split("_")[0] if c.isdigit())
    return (int(digits or 0), path.name)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def suite_exit_code(rows: list[dict]) -> int:
    return 0 if rows and all(row.get("exit") == 0 for row in rows) else 1


def selftest() -> int:
    versioned = sorted((p for p in HERE.glob("v*.py")), key=order_key)
    auxiliary = [HERE / name for name in AUTHORITATIVE_AUXILIARY]
    assert versioned
    assert all(path.is_file() for path in auxiliary)
    assert len({path.name for path in versioned + auxiliary}) == len(
        versioned + auxiliary
    )
    assert all(len(file_sha256(path)) == 64 for path in versioned + auxiliary)
    assert suite_exit_code([{"exit": 0}]) == 0
    assert suite_exit_code([{"exit": 0}, {"exit": 1}]) == 1
    assert suite_exit_code([]) == 1
    print(
        "RESEARCH RUNNER SELFTEST PASS "
        f"(versioned={len(versioned)} auxiliary={len(auxiliary)})"
    )
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    only_fixed = "--fixed" in sys.argv
    scripts = sorted((p for p in HERE.glob("v*.py")), key=order_key)
    if only_fixed:
        scripts = [p for p in scripts if p.name in REWRITTEN]
    else:
        missing = [
            name for name in AUTHORITATIVE_AUXILIARY if not (HERE / name).is_file()
        ]
        if missing:
            print(f"RESEARCH SUITE REFUSED: missing required scripts: {missing}")
            return 2
        scripts.extend(HERE / name for name in AUTHORITATIVE_AUXILIARY)
    if "--list" in sys.argv:
        for script in scripts:
            print(f"{file_sha256(script)}  {script.name}")
        return 0 if scripts else 1

    print("=" * 92)
    print("RESEARCH SUITE - SEQUENTIAL RUN")
    print("=" * 92)

    rows = []
    for script in scripts:
        started = time.time()
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                cwd=str(REPO),
                timeout=900,
            )
            output = result.stdout + result.stderr
            return_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            return_code = 124
        verdict = VERDICT.search(output)
        oos = OOS_RETURN.search(output)
        rows.append({
            "script": script.name,
            "sha256": file_sha256(script),
            "exit": return_code,
            "secs": round(time.time() - started, 1),
            "rewritten": script.name in REWRITTEN,
            "in_sample_pct": float(oos.group(1)) if oos else None,
            "oos_pct": float(oos.group(2)) if oos else None,
            "verdict": verdict.group(1).strip() if verdict else "(no out-of-sample verdict)",
        })
        mark = "OK " if return_code == 0 else "ERR"
        print(f"  {mark} {rows[-1]['secs']:>6}s  {script.name}")

    print("\n" + "=" * 92)
    print(f"{'script':<40}{'IS %':>9}{'OOS %':>9}  verdict")
    print("-" * 92)
    for row in rows:
        is_pct = f"{row['in_sample_pct']:.2f}" if row["in_sample_pct"] is not None else "-"
        oos_pct = f"{row['oos_pct']:.2f}" if row["oos_pct"] is not None else "-"
        flag = "*" if row["rewritten"] else " "
        print(f"{flag}{row['script']:<39}{is_pct:>9}{oos_pct:>9}  {row['verdict'][:34]}")

    measured = [r for r in rows if r["oos_pct"] is not None]
    positive = [r for r in measured if r["oos_pct"] > 0]
    print("-" * 92)
    print(f"  scripts run                       : {len(rows)}")
    print(f"  exited non-zero                   : {sum(1 for r in rows if r['exit'])}")
    print(f"  with a real out-of-sample number  : {len(measured)}  (* = rewritten)")
    print(f"  POSITIVE out-of-sample             : {len(positive)}")
    print()
    print("  Scripts without an out-of-sample number report IN-SAMPLE figures only.")
    print("  Those numbers are not evidence of edge and must not be quoted as results.")

    report = {
        "runner_sha256": file_sha256(Path(__file__)),
        "python": sys.version,
        "generated_at_unix_s": time.time(),
        "all_children_passed": all(row["exit"] == 0 for row in rows),
        "rows": rows,
    }
    (REPO / "research" / "sequence_results.json").write_text(
        json.dumps(report, indent=1) + "\n", encoding="utf-8"
    )
    return suite_exit_code(rows)


if __name__ == "__main__":
    raise SystemExit(main())
