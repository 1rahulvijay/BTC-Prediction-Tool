"""
audit_strategy_registry.py — is every paper strategy consistently registered end to end?
=========================================================================================
A strategy is only trustworthy if it is wired the same way in all three places:

    1. backend/price_to_beat.py   logs it              (it actually trades)
    2. backend/server.py          exposes it           (its live EV reaches the API)
    3. src/main.js                names it             (a human can read the blotter)

A rule missing from (2) or (3) still trades but becomes invisible - the worst failure mode,
because it accrues evidence nobody reads.

WHY THIS FILE EXISTS (2026-07-25): an ad-hoc regex audit reported LATE_LEADER_15S_V1 and
LATE_LEADER_60S_V1 as "registered but never logged". That was a FALSE POSITIVE - those two are
logged through a loop variable:

    for _lkey, _lrule, _lo, _hi in (("ll60", "LATE_LEADER_60S_V1", 50, 65), ...):
        database.log_rule_paper_trade(rnd["id"], _lrule, ...)

...so the rule name never appears as a literal argument to the call. A regex over call sites
cannot see it. This module parses the AST instead and collects EVERY string constant in the
file that looks like a rule id, which is robust to loop tuples, dicts, constants and direct
arguments alike. A false alarm here is expensive: it invites "fixing" something that is not
broken.

Usage:
    python backend/research/audit_strategy_registry.py            # audit
    python backend/research/audit_strategy_registry.py --selftest # prove the detector works
Exit code 0 = consistent, 1 = real inconsistency found.
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PTB = os.path.join(ROOT, "backend", "price_to_beat.py")
POLYMARKET_RULE_MODULES = (
    os.path.join(ROOT, "backend", "polymarket", "model_dynamic_paper.py"),
    # The Polymarket fair-value benchmark owns its own id (STRATEGY_ID) and price_to_beat
    # imports it rather than restating the string, so that the ledger row and the paper row
    # cannot drift apart. That is the right pattern and it makes the literal invisible at the
    # call site - the same blind spot the loop-variable case in the docstring describes. The
    # module that OWNS the id is the place to read it from.
    os.path.join(ROOT, "backend", "polymarket_paper", "calibrated_fair_value.py"),
)
SRV = os.path.join(ROOT, "backend", "server.py")
JS = os.path.join(ROOT, "src", "main.js")

RULE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_V\d+$")
# The frozen rule has its own dedicated status tile, so it is intentionally NOT in the
# server's `shadows` dict. Anything else missing from there is a real defect.
TILE_ONLY = {"LATE_LEADER_30S_V1"}


def rule_strings_in_python(path: str) -> set[str]:
    """Every string constant in the file that looks like a rule id (AST, not regex)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and RULE_RE.match(n.value)}


def rule_strings_in_text(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        return {m for m in re.findall(r"[A-Z][A-Z0-9_]*_V\d+", f.read()) if RULE_RE.match(m)}


def logged_rules() -> set[str]:
    """Rules the tracker can trade, including IDs owned by imported strategy modules."""
    rules = rule_strings_in_python(PTB)
    for path in POLYMARKET_RULE_MODULES:
        rules.update(rule_strings_in_python(path))
    return rules


def audit() -> int:
    logged = logged_rules()
    exposed = rule_strings_in_python(SRV)
    named = rule_strings_in_text(JS)

    missing_srv = sorted(r for r in logged if r not in exposed and r not in TILE_ONLY)
    missing_js = sorted(r for r in logged if r not in named)
    ghost_srv = sorted(r for r in exposed if r not in logged)
    ghost_js = sorted(r for r in named if r not in logged)

    print(f"strategies logged in price_to_beat : {len(logged)}")
    for r in sorted(logged):
        flags = []
        if r in TILE_ONLY:
            flags.append("own tile")
        if r in exposed:
            flags.append("server")
        if r in named:
            flags.append("ui")
        print(f"   {r:32} [{', '.join(flags) or 'UNREGISTERED'}]")

    problems = []
    if missing_srv:
        problems.append(f"logged but NOT exposed by server: {missing_srv}")
    if missing_js:
        problems.append(f"logged but NOT named in the UI: {missing_js}")
    if ghost_srv:
        problems.append(f"exposed by server but never logged: {ghost_srv}")
    if ghost_js:
        problems.append(f"named in UI but never logged: {ghost_js}")

    print()
    if problems:
        print("INCONSISTENT:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"CONSISTENT: all {len(logged)} strategies are logged, exposed and named.")
    return 0


def selftest() -> int:
    """Prove the detector catches what the old regex missed, and does not invent problems."""
    import tempfile
    src = '''
RULES = (("a", "LOOP_TUPLE_V1", 1), ("b", "SECOND_LOOP_V1", 2))
def f(db, rnd):
    for _k, _rule, _n in RULES:
        db.log_rule_paper_trade(rnd, _rule, 1)          # via loop variable
    db.log_rule_paper_trade(rnd, "DIRECT_LITERAL_V1", 1)  # direct literal
    db.close_rule_paper_trade(rnd, "DIRECT_LITERAL_V1")
    x = {"KEY_IN_DICT_V1": 1}
    return x, "not_a_rule", "lowercase_v1"
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(src)
        p = fh.name
    try:
        found = rule_strings_in_python(p)
        expect = {"LOOP_TUPLE_V1", "SECOND_LOOP_V1", "DIRECT_LITERAL_V1", "KEY_IN_DICT_V1"}
        ok = found == expect
        print(f"  detector found : {sorted(found)}")
        print(f"  expected       : {sorted(expect)}")
        print(f"  loop-variable rules detected (the 2026-07-25 false positive): "
              f"{'YES' if {'LOOP_TUPLE_V1', 'SECOND_LOOP_V1'} <= found else 'NO - REGRESSION'}")
        print(f"  non-rule strings correctly ignored: "
              f"{'YES' if not ({'not_a_rule', 'lowercase_v1'} & found) else 'NO'}")
        print("\nSELFTEST", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        os.unlink(p)


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else audit())
