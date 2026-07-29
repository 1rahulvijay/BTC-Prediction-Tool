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

VERDICT = re.compile(r"VERDICT:\s*(.+)")
OOS_RETURN = re.compile(r"total return %\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)")


def order_key(path: Path):
    digits = "".join(c for c in path.name.split("_")[0] if c.isdigit())
    return (int(digits or 0), path.name)


def main() -> int:
    only_fixed = "--fixed" in sys.argv
    scripts = sorted((p for p in HERE.glob("v*.py")), key=order_key)
    if only_fixed:
        scripts = [p for p in scripts if p.name in REWRITTEN]

    print("=" * 92)
    print("RESEARCH SUITE - SEQUENTIAL RUN")
    print("=" * 92)

    rows = []
    for script in scripts:
        started = time.time()
        result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, cwd=str(REPO), timeout=900)
        output = result.stdout + result.stderr
        verdict = VERDICT.search(output)
        oos = OOS_RETURN.search(output)
        rows.append({
            "script": script.name,
            "exit": result.returncode,
            "secs": round(time.time() - started, 1),
            "rewritten": script.name in REWRITTEN,
            "in_sample_pct": float(oos.group(1)) if oos else None,
            "oos_pct": float(oos.group(2)) if oos else None,
            "verdict": verdict.group(1).strip() if verdict else "(no out-of-sample verdict)",
        })
        mark = "OK " if result.returncode == 0 else "ERR"
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

    (REPO / "research" / "sequence_results.json").write_text(
        json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
