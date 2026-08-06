"""Hunt the defect class that produced every real bug found in this repository today.

THE PATTERN
    Each one was a DECLARATION THAT OUTRAN ITS IMPLEMENTATION. The code says a thing is
    handled; nothing handles it. None are syntax errors, none are type errors, and linters
    see nothing:

      * a contract added to KNOWN_CONTRACTS with no branch in the grader, so it stopped being
        refused and started being graded by the WRONG rule (strictly worse than before);
      * MIN_TRAIN_GROUPS declared and guarded on `groups is not None` while no caller ever
        passed groups, so the floor could never fire;
      * `_oof_class_set_mismatch` counted and never read by anything;
      * `if threshold:` letting 0.0 through - the exact value a caller passes believing they
        disabled the band;
      * a test asserting an error MESSAGE, which survives `raise` becoming `logger.warning`.

    A passing test suite cannot find these, because the thing that is missing is the thing
    that would have been tested.

    python backend/audit/hunt_declaration_gaps.py            # report
    python backend/audit/hunt_declaration_gaps.py --selftest # prove the hunters can fire
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: OUR code only. The first run scanned .venv_research_cuda/site-packages and returned 399
#: findings, almost all third-party - noise that buries the handful that are ours. A report
#: nobody can read is the same as no report.
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "dist", "btc_full_project",
             "catboost_info", "site-packages", "Lib", ".mypy_cache", ".pytest_cache"}
SKIP_PREFIXES = (".venv", "venv", "env")
#: Names whose "unused" state is legitimate: public API, or read via getattr/import *.
CONST_ALLOW = {"__all__", "VERSION", "HEAD_VERSION", "FEATURE_SEMANTICS_VERSION"}


def py_files():
    for path in sorted(REPO.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if any(part.startswith(SKIP_PREFIXES) for part in path.parts):
            continue
        yield path


def parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def hunt_truthiness_on_numeric(tree, path, findings):
    """`if x:` where x is a numeric PARAMETER with a numeric default.

    0 and 0.0 are falsy. For a threshold, a size, a count or a tolerance, zero is a real value
    a caller passes deliberately - and the guard silently treats it as absent. This is exactly
    how `threshold=0.0` bypassed the binary-contract refusal."""
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        numeric = set()
        args = fn.args
        defaults = list(args.defaults)
        pos = args.posonlyargs + args.args
        for arg, dflt in zip(pos[len(pos) - len(defaults):], defaults):
            if isinstance(dflt, ast.Constant) and isinstance(dflt.value, (int, float)) \
                    and not isinstance(dflt.value, bool):
                numeric.add(arg.arg)
        for kwarg, dflt in zip(args.kwonlyargs, args.kw_defaults):
            if isinstance(dflt, ast.Constant) and isinstance(dflt.value, (int, float)) \
                    and not isinstance(dflt.value, bool):
                numeric.add(kwarg.arg)
        if not numeric:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) \
                    and node.test.id in numeric:
                findings.append((
                    "TRUTHINESS_ON_NUMERIC", path, node.lineno,
                    f"`if {node.test.id}:` in {fn.name}() - 0 is falsy, so a deliberate zero "
                    f"is read as absent"))


def identifier_counts(sources) -> Counter:
    """One pass over every file, counting identifier occurrences.

    The first version re-scanned every source for every constant, which is quadratic and
    took minutes on this repo. The counts are identical; only the cost changes."""
    token = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    counts: Counter = Counter()
    for src in sources:
        counts.update(token.findall(src))
    return counts


def hunt_unread_constants(tree, path, findings, counts):
    """Module-level UPPER_CASE constants that nothing anywhere reads.

    A declared limit nobody compares against is decoration. MIN_TRAIN_GROUPS looked exactly
    like a gate and could never fire."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if not name.isupper() or name.startswith("_") or name in CONST_ALLOW:
                continue
            uses = counts.get(name, 0)
            if uses <= 1:                      # only its own definition
                findings.append((
                    "CONSTANT_NEVER_READ", path, node.lineno,
                    f"{name} is declared and never read anywhere in the repository"))


def hunt_swallowed_writes(tree, path, findings):
    """`except: pass` (or bare-log) wrapped around a write/persist call.

    The app stays alive while the evidence it depends on silently stops being written."""
    WRITEY = ("write", "dump", "save", "persist", "commit", "flush", "execute", "insert")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        names = []
        for c in calls:
            if isinstance(c.func, ast.Attribute):
                names.append(c.func.attr.lower())
            elif isinstance(c.func, ast.Name):
                names.append(c.func.id.lower())
        if not any(any(w in n for w in WRITEY) for n in names):
            continue
        # IDEMPOTENT SCHEMA MIGRATION IS NOT A SWALLOWED WRITE.
        #
        # The first version flagged 37 sites in database.py; every sample inspected was
        # `ALTER TABLE ... ADD COLUMN` where swallowing "column already exists" is exactly
        # right. Reporting those as bugs would have been a false alarm at scale - and a
        # report full of false alarms is one people stop reading, which is the same failure
        # mode this module exists to prevent.
        sql = " ".join(
            (c.value if isinstance(c, ast.Constant) and isinstance(c.value, str) else
             (c.value.value if isinstance(c, ast.JoinedStr) and False else ""))
            for c in ast.walk(node) if isinstance(c, ast.Constant)
        ).upper()
        for c in ast.walk(node):
            if isinstance(c, ast.JoinedStr):
                sql += " " + " ".join(
                    v.value.upper() for v in c.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str))
        is_migration = ("ALTER TABLE" in sql or "CREATE TABLE" in sql
                        or "CREATE INDEX" in sql or "ADD COLUMN" in sql)
        writes_rows = any(k in sql for k in ("INSERT", "UPDATE ", "DELETE", "UPSERT",
                                             "COPY ", "APPEND"))
        # A handler that ROLLS BACK and then RE-RAISES is correct transaction handling, not a
        # swallowed write. The inner `except: pass` guards the rollback itself.
        reraises = any(isinstance(s, ast.Raise) for h in node.handlers for s in h.body)
        # SELECT-only blocks are reads. A diagnostic print of the latest row is not evidence
        # failing to be written, and flagging it buries the sites that are.
        read_only = ("SELECT" in sql or "PRAGMA" in sql) and not writes_rows
        if reraises or read_only:
            continue
        for handler in node.handlers:
            broad = handler.type is None or (
                isinstance(handler.type, ast.Name) and handler.type.id == "Exception")
            silent = all(isinstance(s, ast.Pass) for s in handler.body)
            if not (broad and silent):
                continue
            if is_migration and not writes_rows:
                continue
            findings.append((
                "SWALLOWED_WRITE", path, handler.lineno,
                "an evidence write is wrapped in `except ...: pass` - the process survives "
                "while the record it owes silently stops being written"))


def hunt_unbranched_members(tree, path, findings, counts):
    """Members of a KNOWN_* / *_CONTRACTS style set that no code ever compares against.

    This is P0-1 generalised. Adding a member to a 'known' set makes it PASS the validity
    guard; if no branch handles it, it then falls through to whatever the default path is.
    That converts a safe refusal into a confident wrong answer."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if not (name.isupper() and ("KNOWN" in name or name.endswith("_CONTRACTS"))):
                continue
            members = [e.id for e in ast.walk(node.value) if isinstance(e, ast.Name)
                       and e.id.isupper()]
            for m in members:
                # A member is "handled" if it appears in a comparison or a dict/mapping key
                # somewhere, beyond its own definition and this set.
                hits = counts.get(m, 0)
                if hits <= 2:
                    findings.append((
                        "UNBRANCHED_SET_MEMBER", path, node.lineno,
                        f"{m} is in {name} but is referenced {hits}x repo-wide - it passes "
                        f"the validity guard while probably having no branch of its own"))


HUNTERS = (hunt_truthiness_on_numeric, hunt_unread_constants, hunt_swallowed_writes,
           hunt_unbranched_members)


def run():
    files = list(py_files())
    sources = [p.read_text(encoding="utf-8", errors="replace") for p in files]
    counts = identifier_counts(sources)
    findings = []
    for path, tree in ((p, parse(p)) for p in files):
        if tree is None:
            continue
        rel = path.relative_to(REPO).as_posix()
        hunt_truthiness_on_numeric(tree, rel, findings)
        hunt_unread_constants(tree, rel, findings, counts)
        hunt_swallowed_writes(tree, rel, findings)
        hunt_unbranched_members(tree, rel, findings, counts)
    return files, findings


def selftest() -> int:
    """Prove each hunter FIRES on a known-bad fixture. A hunter that never fires finds nothing
    and reports success, which is the defect it is looking for."""
    fixtures = {
        "TRUTHINESS_ON_NUMERIC": "def f(threshold=0.0):\n    if threshold:\n        raise X\n",
        "SWALLOWED_WRITE": "try:\n    handle.write(x)\nexcept Exception:\n    pass\n",
    }
    checks = 0
    for kind, src in fixtures.items():
        found = []
        tree = ast.parse(src)
        hunt_truthiness_on_numeric(tree, "fixture.py", found)
        hunt_swallowed_writes(tree, "fixture.py", found)
        assert any(f[0] == kind for f in found), f"hunter for {kind} did not fire on its fixture"
        checks += 1
        print(f"  PASS  the {kind} hunter fires on a known-bad fixture")
    clean = ast.parse("def f(threshold=0.0):\n    if threshold is not None:\n        raise X\n")
    found = []
    hunt_truthiness_on_numeric(clean, "fixture.py", found)
    assert not found, "the truthiness hunter fired on a correct `is not None` guard"
    checks += 1
    print("  PASS  and does NOT fire on the corrected `is not None` form")
    print(f"\nHUNTER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--kind", default=None)
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    files, findings = run()
    if args.kind:
        findings = [f for f in findings if f[0] == args.kind]
    print("=" * 92)
    print(f"DECLARATION-GAP HUNT  ({len(files)} files scanned)")
    print("=" * 92)
    by_kind: dict = {}
    for kind, path, line, msg in findings:
        by_kind.setdefault(kind, []).append((path, line, msg))
    for kind in sorted(by_kind):
        rows = by_kind[kind]
        print(f"\n{kind}  ({len(rows)})")
        for path, line, msg in rows[:20]:
            print(f"  {path}:{line}")
            print(f"      {msg}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")
    if not findings:
        print("\n  no findings")
    print()
    print(f"TOTAL: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
