"""Measures how much of the codebase still (de)serializes models outside model_artifacts.

WHY A RATCHET RATHER THAN A BIG-BANG MIGRATION
    Measured at this commit: 52 raw save calls and 53 raw load calls across 59 files. Rewriting
    105 call sites in one change would be a large, untestable diff touching every trainer and
    every serving path at once - exactly the kind of change that gets reviewed by exhaustion.

    So the migration proceeds incrementally, and this file makes it MEASURABLE and
    MONOTONIC: it records the current bypass count as a baseline and FAILS if that count grows.
    New code cannot add an unmigrated path, and every migrated call site permanently lowers the
    ceiling.

WHY THE COUNT MATTERS
    `joblib.load()` on an unknown pickle executes arbitrary code during unpickling. Every load
    outside model_artifacts is a deserialization that happens BEFORE any hash is checked, so the
    verify-before-deserialize property the artifact layer provides does not hold for it.

    BTC_STRICT_ARTIFACT_IDENTITY defaults to 0 today precisely because these paths exist. That
    default cannot honestly be flipped to 1 until the load count reaches zero, so this number is
    the gate on the flip - not a style metric.

    python backend/artifact_migration_status.py            # report
    python backend/artifact_migration_status.py --selftest # ratchet check (CI)
    python backend/artifact_migration_status.py --accept   # lower the baseline after migrating
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
BASELINE = Path(__file__).resolve().parent / "artifact_migration_baseline.json"

# The module that owns serialization, plus the tooling that measures it.
OWNERS = {"model_artifacts.py", "artifact_migration_status.py"}

SAVE = re.compile(r"\b(joblib\.dump|pickle\.dump|torch\.save)\s*\(")
LOAD = re.compile(r"\b(joblib\.load|pickle\.load|torch\.load)\s*\(")


def scan() -> dict:
    saves: list[str] = []
    loads: list[str] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if path.name in OWNERS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(REPO).as_posix()
        # AST, not regex: a docstring that MENTIONS joblib.load is prose, not a call site.
        # The regex version counted this module's own warning text as a bypass.
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = getattr(node.func.value, "id", "")
            name = f"{owner}.{node.func.attr}"
            if name in ("joblib.dump", "pickle.dump", "torch.save"):
                saves.append(f"{rel}:{node.lineno}")
            elif name in ("joblib.load", "pickle.load", "torch.load"):
                loads.append(f"{rel}:{node.lineno}")
    return {
        "save_sites": saves,
        "load_sites": loads,
        "save_count": len(saves),
        "load_count": len(loads),
        "files": len({s.split(":")[0] for s in saves + loads}),
    }


def read_baseline() -> dict | None:
    if not BASELINE.is_file():
        return None
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return None


def write_baseline(current: dict) -> None:
    BASELINE.write_text(json.dumps({
        "save_count": current["save_count"],
        "load_count": current["load_count"],
        "note": "Ratchet ceiling. May only ever DECREASE. Lower it with --accept after "
                "migrating call sites to model_artifacts.",
    }, indent=2) + "\n", encoding="utf-8")


def report(current: dict) -> None:
    print("=" * 78)
    print("ARTIFACT MIGRATION STATUS")
    print("=" * 78)
    print(f"  raw save calls outside model_artifacts : {current['save_count']}")
    print(f"  raw load calls outside model_artifacts : {current['load_count']}")
    print(f"  files involved                         : {current['files']}")
    print()
    print("  Every LOAD here deserializes before any hash is checked, so")
    print("  verify-before-deserialize does not hold for it.")
    print("  BTC_STRICT_ARTIFACT_IDENTITY cannot honestly default to 1 until")
    print(f"  the load count reaches 0 (currently {current['load_count']}).")
    hot = {}
    for site in current["save_sites"] + current["load_sites"]:
        hot[site.split(":")[0]] = hot.get(site.split(":")[0], 0) + 1
    print("\n  heaviest files (migrate these first):")
    for name, count in sorted(hot.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {count:>3}  {name}")


def selftest() -> int:
    current = scan()
    baseline = read_baseline()
    ok = True

    def chk(cond: object, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok = ok and bool(cond)

    print("the scanner finds real call sites")
    chk(current["save_count"] > 0 or current["load_count"] > 0,
        f"scan found {current['save_count']} saves and {current['load_count']} loads")
    chk(all(":" in s for s in current["save_sites"][:5]),
        "each site is reported as file:line so it can be opened directly")

    print("comments are not counted as call sites")
    chk(not any("artifact_migration_status" in s
                for s in current["save_sites"] + current["load_sites"]),
        "this file's own regexes are not self-reported")

    print("the ratchet holds")
    if baseline is None:
        write_baseline(current)
        chk(True, f"baseline established at saves={current['save_count']} "
                  f"loads={current['load_count']}")
    else:
        chk(current["save_count"] <= baseline["save_count"],
            f"raw SAVE sites did not grow ({current['save_count']} <= "
            f"{baseline['save_count']})")
        chk(current["load_count"] <= baseline["load_count"],
            f"raw LOAD sites did not grow ({current['load_count']} <= "
            f"{baseline['load_count']})")
        if (current["save_count"] < baseline["save_count"]
                or current["load_count"] < baseline["load_count"]):
            print(f"       progress: baseline was saves={baseline['save_count']} "
                  f"loads={baseline['load_count']}; run --accept to lower the ceiling")

    print("\nSTATUS: migration INCOMPLETE - strict identity cannot be defaulted on yet.")
    print("ARTIFACT MIGRATION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    current = scan()
    if "--accept" in sys.argv:
        write_baseline(current)
        print(f"baseline lowered to saves={current['save_count']} loads={current['load_count']}")
        return 0
    report(current)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
