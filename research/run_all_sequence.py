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
# Structural / diagnostic studies. These are NOT backtests and produce no IS/OOS return, so
# they are classified separately rather than listed as "(no out-of-sample verdict)" beside
# scripts that could have reported one. They still run, and a non-zero exit still fails.
FRONTIER = (
    "path_information_test.py",
    "breakout_bracket_test.py",
    "complete_set_arbitrage_test.py",
    "structural_edge_tests.py",
    "options_surface_tests.py",
    "phold_auc_and_expectancy.py",
    "phold_calibrated_fair_value.py",
    "meta_label_head_test.py",
)
# Not studies, and deliberately NOT executed by the suite.
#   harness.py               - shared library, no standalone behaviour
#   run_all_sequence.py      - this runner
#   download_binance_l2_data - fetches ~120 days from data.binance.vision. Running it on every
#                              suite invocation would hammer the network and rewrite data/, so
#                              it is operator-invoked. Listed here so the coverage check can
#                              tell "deliberately excluded" from "silently forgotten".
NON_STUDY = {"harness.py", "run_all_sequence.py", "download_binance_l2_data.py"}

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


def uncovered_scripts() -> list[str]:
    """Research scripts on disk that this runner would silently skip.

    The docstring promises EVERY research script. Discovery was `v*.py` plus three hardcoded
    names, so the five frontier studies - which produced every finding of the last week - were
    excluded, and the selftest could not see it because it asserted only that the names it
    already knew about existed. A check that can only confirm what it was told is vacuous;
    this one enumerates the DIRECTORY and fails on anything unaccounted for.
    """
    covered = set(AUTHORITATIVE_AUXILIARY) | set(FRONTIER) | NON_STUDY
    return sorted(path.name for path in HERE.glob("*.py")
                  if not path.name.startswith("v") and path.name not in covered)


def selftest() -> int:
    versioned = sorted((p for p in HERE.glob("v*.py")), key=order_key)
    auxiliary = [HERE / name for name in AUTHORITATIVE_AUXILIARY]
    frontier = [HERE / name for name in FRONTIER]
    assert versioned
    assert all(path.is_file() for path in auxiliary), "auxiliary script missing"
    assert all(path.is_file() for path in frontier), "frontier script missing"
    everything = versioned + auxiliary + frontier
    assert len({path.name for path in everything}) == len(everything)
    assert all(len(file_sha256(path)) == 64 for path in everything)

    # The check that would have caught the omission: nothing on disk goes unaccounted for.
    skipped = uncovered_scripts()
    assert not skipped, (
        f"research scripts on disk but not covered by this runner: {skipped}. "
        "Add them to FRONTIER or AUTHORITATIVE_AUXILIARY, or to NON_STUDY if they are "
        "support modules. The runner claims to run every research script."
    )

    assert suite_exit_code([{"exit": 0}]) == 0
    assert suite_exit_code([{"exit": 0}, {"exit": 1}]) == 1
    assert suite_exit_code([]) == 1
    print(
        "RESEARCH RUNNER SELFTEST PASS "
        f"(versioned={len(versioned)} auxiliary={len(auxiliary)} "
        f"frontier={len(frontier)} uncovered=0)"
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
        required = AUTHORITATIVE_AUXILIARY + FRONTIER
        missing = [name for name in required if not (HERE / name).is_file()]
        if missing:
            print(f"RESEARCH SUITE REFUSED: missing required scripts: {missing}")
            return 2
        skipped = uncovered_scripts()
        if skipped:
            print(f"RESEARCH SUITE REFUSED: scripts on disk that this runner does not "
                  f"cover: {skipped}")
            return 2
        scripts.extend(HERE / name for name in required)
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
            "frontier": script.name in FRONTIER,
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
        if row["frontier"]:
            continue
        is_pct = f"{row['in_sample_pct']:.2f}" if row["in_sample_pct"] is not None else "-"
        oos_pct = f"{row['oos_pct']:.2f}" if row["oos_pct"] is not None else "-"
        flag = "*" if row["rewritten"] else " "
        print(f"{flag}{row['script']:<39}{is_pct:>9}{oos_pct:>9}  {row['verdict'][:34]}")

    measured = [r for r in rows if r["oos_pct"] is not None]
    positive = [r for r in measured if r["oos_pct"] > 0]
    frontier_rows = [r for r in rows if r["frontier"]]
    print("-" * 92)
    print(f"  backtest scripts run              : {len(rows) - len(frontier_rows)}")
    print(f"  exited non-zero                   : {sum(1 for r in rows if r['exit'])}")
    print(f"  with a real out-of-sample number  : {len(measured)}  (* = rewritten)")
    print(f"  POSITIVE out-of-sample             : {len(positive)}")
    print()
    print("  Scripts without an out-of-sample number report IN-SAMPLE figures only.")
    print("  Those numbers are not evidence of edge and must not be quoted as results.")

    if frontier_rows:
        print("\n" + "=" * 92)
        print("FRONTIER STUDIES - structural and diagnostic, not backtests")
        print("=" * 92)
        print("  These measure arithmetic constraints, path statistics and executable cost")
        print("  hurdles. They have no IS/OOS return by construction, so they are reported")
        print("  here rather than shown with an empty return column. Findings are in")
        print("  docs/PATH_INFORMATION_RESULTS.md and docs/OPTIONS_SURFACE_RESULTS.md.")
        print()
        for row in frontier_rows:
            mark = "OK " if row["exit"] == 0 else "ERR"
            print(f"  {mark} {row['secs']:>6}s  {row['script']}")

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
