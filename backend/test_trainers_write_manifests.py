"""Every trainer that writes a model artifact must write its integrity manifest too.

WHY THIS IS A GATE AND NOT A CONVENTION
    A missing manifest is not bookkeeping. It is the live blocker on the only measured
    candidate edge in this repository:

        data/research/phold_challenger/phold_calibrators.json
          deployable = False,  both horizons
          reason     = SOURCE_MODEL_REQUIRES_RETRAINING - 12/12 source artifacts fail identity
          ...while  beats_raw_on_all_three = True  and  ECE 0.0883 -> 0.0136

    The P(hold) calibrator wins on Brier AND log-loss AND ECE and is still refused, because a
    calibrator for a model whose identity cannot be proven is not deployable - if the model
    silently changes, the calibrator becomes silently wrong. That refusal disables
    PM_CALIBRATED_FAIR_VALUE_V1.

    So an overnight retrain that saves an artifact without a manifest does not merely leave a
    field blank. It leaves the strategy switched off, and nothing in the run would say so.

    backend/train_round_state_heads.py - the trainer for P(hold) itself - did exactly that:
    `joblib.dump(...)` with no manifest anywhere in the file. Fixed, and this gate stops it
    recurring in any trainer.

THE RULE
    A module under backend/ whose name starts with `train_` and which calls joblib.dump (or
    verified_io.atomic_dump) must also reference a manifest writer. Static analysis, so it costs
    nothing and cannot be skipped by a code path that did not execute.

    python backend/test_trainers_write_manifests.py
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"

DUMP_NAMES = {"dump", "atomic_dump"}
MANIFEST_NAMES = {"write_manifest", "write_integrity_manifest", "publish_bundle",
                  "publish_champion", "save_bundle"}

# Trainers that legitimately write no artifact of their own. Each must state why, so the list
# cannot quietly become a place to hide a real offender.
EXEMPT = {
    # writes CSV/JSON reports only, no model artifact
    "train_heads.py": "orchestrator - delegates saving to the per-head trainers it calls",
}


def _calls(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name:
                found.add(name)
    return found


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def offenders() -> list[tuple[str, str]]:
    found = []
    for path in sorted(BACKEND.rglob("train_*.py")):
        relative = path.relative_to(REPO).as_posix()
        if path.name in EXEMPT:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        calls = _calls(tree)
        if not (calls & DUMP_NAMES):
            continue                                   # writes no artifact
        vocabulary = calls | _imported_names(tree)
        if not (vocabulary & MANIFEST_NAMES):
            found.append((relative, "dumps an artifact but never references a manifest writer"))
    return found


def selftest() -> None:
    """The check must be able to detect an offender, not merely return an empty list."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "train_probe.py"
        bad.write_text("import joblib\ndef save(b, p):\n    joblib.dump(b, p)\n", encoding="utf-8")
        tree = ast.parse(bad.read_text(encoding="utf-8"))
        assert _calls(tree) & DUMP_NAMES, "a joblib.dump must be detected"
        assert not ((_calls(tree) | _imported_names(tree)) & MANIFEST_NAMES), \
            "a file with no manifest writer must be reported as an offender"

        good = Path(tmp) / "train_ok.py"
        good.write_text("import joblib\nfrom verified_io import write_manifest\n"
                        "def save(b, p):\n    joblib.dump(b, p)\n    write_manifest(p)\n",
                        encoding="utf-8")
        tree = ast.parse(good.read_text(encoding="utf-8"))
        assert (_calls(tree) | _imported_names(tree)) & MANIFEST_NAMES, \
            "a file that writes a manifest must pass"


def main() -> int:
    selftest()
    stale = sorted(name for name in EXEMPT
                   if not any(p.name == name for p in BACKEND.rglob("train_*.py")))
    bad = offenders()

    print("=" * 92)
    print("TRAINERS MUST WRITE MANIFESTS")
    print("=" * 92)
    trainers = [p for p in BACKEND.rglob("train_*.py")]
    print(f"  trainers scanned           : {len(trainers)}")
    print(f"  declared exempt            : {len(EXEMPT)}")
    print(f"  dump an artifact, no manifest : {len(bad)}")

    if stale:
        print("\n  STALE EXEMPTIONS - named but no longer present:")
        for name in stale:
            print(f"    {name}")
        return 1
    if bad:
        print()
        for name, reason in bad:
            print(f"    {name}: {reason}")
        print()
        print("  An artifact without a manifest reads as UNKNOWN identity, and the P(hold)")
        print("  calibrator refuses to deploy while ANY source artifact fails identity. A")
        print("  retrain that skips the manifest leaves PM_CALIBRATED_FAIR_VALUE_V1 switched")
        print("  off and says nothing about it. Write the manifest in the same step as the dump.")
        return 1

    print("\n  PASS - every artifact-writing trainer also writes a manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
