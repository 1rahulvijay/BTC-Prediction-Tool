"""A file named test_*.py must actually verify something, or be named for what it is.

WHY THIS EXISTS
    A validation sweep found 14 files named `test_*.py` containing ZERO assertions. They are
    research studies - they load data, compute statistics and write a markdown report. Nothing
    in them can fail. A runner that executes them records "OK", and a reader who sees 30 green
    test files believes 30 things were verified when 14 verified nothing.

    That is the same family as the three coverage defects already fixed this week:
      * run_all_sequence.py claimed to run every research script and ran 34 of 39;
      * the extraction test found 0 invocations and printed ALL PASS;
      * pytest was never invoked by CI, hiding 93 tests including one file with 29
        assertions that had never executed.

    Each was invisible for the same reason: the check could not see what it was missing. This
    one enumerates the tree and fails on anything unaccounted for.

THE RULE
    Every `test_*.py` (or `*_test.py`) under a source directory must either
      (a) contain an assertion, a `self.assert*` call, a `raise SystemExit(...)`, or a
          non-zero exit path - i.e. some way to FAIL; or
      (b) be listed in STUDIES below, which declares in writing that it is a research study
          whose name is historical and whose output is a report, not a verdict.

    Renaming them all to `study_*.py` would be cleaner. It is also a wide rename across files
    owned by other work in flight, so the honest intermediate is an explicit, reviewed list
    that cannot grow silently: a NEW zero-assertion test file fails this check.

    python backend/tests/test_naming_honesty.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("backend", "research", "tests", "microstructure")

# Declared research studies: named test_* for historical reasons, verify nothing by design.
# Each writes a report to docs/ or data/research/ and is read by a human, not by a gate.
STUDIES = {
    "backend/decision/phold_keeper_test.py",
    "backend/research/test_120d_conditional_ev_pipeline.py",
    "backend/research/test_120d_trade_policy_heads.py",
    "backend/research/test_180d_decision_heads.py",
    "backend/research/test_180d_economic_policy_campaign.py",
    "backend/research/test_180d_path_dynamics.py",
    "backend/research/test_180d_round_state_and_stopping.py",
    "backend/research/test_complement_and_opening_drift.py",
    "backend/research/test_directional_bigmove.py",
    "backend/research/test_head_calibration.py",
    "backend/research/test_oracle_capacity.py",
    "backend/research/test_stopping_baselines.py",
    "backend/research/test_virtue_complexity_late_leader.py",
    "backend/tests/test_5m_15m_30d.py",
    "backend/tests/test_deadfeatures_30d.py",
}

# Research studies under research/ are covered by run_all_sequence.py and are not test files.
SKIP_PREFIXES = ("research/v", "research/_original/")


def can_fail(path: Path) -> bool:
    """True if the module contains any construct capable of failing."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return True                      # a syntax error fails loudly on its own
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name and (name.startswith("assert") or name in {"fail", "exit"}):
                return True
    return False


def offenders() -> list[str]:
    found = []
    for directory in SOURCE_DIRS:
        base = REPO / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith(SKIP_PREFIXES):
                continue
            name = path.name
            if not (name.startswith("test_") or name.endswith("_test.py")):
                continue
            if rel in STUDIES:
                continue
            if not can_fail(path):
                found.append(rel)
    return found


def selftest() -> None:
    """The check must actually detect an offender, not merely return an empty list."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "test_probe.py"
        probe.write_text("def test_nothing():\n    x = 1 + 1\n", encoding="utf-8")
        assert not can_fail(probe), "a file with no assertion must be reported as unable to fail"
        probe.write_text("def test_something():\n    assert 1 == 1\n", encoding="utf-8")
        assert can_fail(probe), "a file with an assertion must be reported as able to fail"


def main() -> int:
    selftest()
    stale = sorted(name for name in STUDIES if not (REPO / name).is_file())
    bad = offenders()

    print("=" * 88)
    print("TEST NAMING HONESTY - a test_*.py must be able to fail, or be declared a study")
    print("=" * 88)
    print(f"  declared research studies : {len(STUDIES)}")
    print(f"  undeclared and unable to fail : {len(bad)}")

    if stale:
        print("\n  STALE DECLARATIONS - listed as studies but no longer on disk:")
        for name in stale:
            print(f"    {name}")
        print("  Remove them from STUDIES; a list that outlives its files stops being read.")
        return 1

    if bad:
        print("\n  These contain no assertion, no raise and no failing exit path, so they")
        print("  cannot fail. A runner will record OK for them regardless of what they do:")
        for name in bad:
            print(f"    {name}")
        print("\n  Either give the file a real check, or rename it (study_*.py / analyse_*.py),")
        print("  or add it to STUDIES with the understanding that it verifies nothing.")
        return 1

    print("\n  PASS - every test_*.py either can fail or is a declared study.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
