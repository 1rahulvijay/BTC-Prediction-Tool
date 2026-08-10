"""No writer may reset a TERMINAL OUTCOME column via INSERT OR REPLACE.

THE BUG CLASS
    `log_price_to_beat` used `INSERT OR REPLACE INTO price_to_beat (... resolved) VALUES
    (..., FALSE)`, naming 20 of 32 columns. OR REPLACE rewrites the whole row, so the twelve
    omitted ones - actual_price, actual_direction, hit, move, settlement_source among them -
    reverted to their defaults, and `resolved` was driven back to FALSE. Re-logging a settled
    round erased its outcome with no error and no log line. The live store holds 14,372
    rounds, 14,368 of them resolved: the labels a retrain learns from.

    That statement is fixed. This test exists because it was never the only one - a repo scan
    found six more with the identical shape, on kronos_predictions, model_predictions (twice),
    forward_ev_ledger, fsr_ppo_decisions and ab_results.

    So the rule is enforced structurally rather than one line at a time: a statement that
    REPLACES a row may not carry a terminal-outcome column that it hard-codes to FALSE or
    NULL. New violations fail immediately. The ones that already exist are listed below and
    must be worked down - the list may shrink, never grow.

    python backend/test_terminal_outcomes_not_replaceable.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

#: Columns whose value is a SETTLED FACT. Once written they are evidence, and a writer that
#: can silently revert them can destroy training labels and health populations alike.
TERMINAL_COLUMNS = (
    "resolved", "actual_direction", "actual_price", "hit", "move", "settlement_source",
    "outcome_status",
)

#: KNOWN, UNFIXED violations - each is `INSERT OR REPLACE ... resolved ... FALSE`, the same
#: defect repaired in log_price_to_beat. Listed rather than silently tolerated so they cannot
#: be forgotten, and so a NEW one fails this test on the day it is written.
#:
#: THIS LIST MAY SHRINK, NEVER GROW. Remove an entry when its statement is converted to
#: `INSERT ... ON CONFLICT (pk) DO UPDATE SET <non-terminal columns> WHERE <table>.resolved
#: = FALSE`, which is what price_to_beat now does.
KNOWN_UNFIXED = {
    ("backend/database.py", "kronos_predictions"),
    ("backend/database.py", "model_predictions"),
    ("backend/database.py", "forward_ev_ledger"),
    ("backend/database.py", "fsr_ppo_decisions"),
    ("backend/database.py", "ab_results"),
}

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def violations() -> set:
    """(file, table) for every REPLACE statement that resets a terminal column."""
    found = set()
    for path in sorted(BACKEND.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            sql = node.value
            m = re.search(r"(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+([A-Za-z_]\w*)",
                          sql, re.I)
            if not m:
                continue
            table = m.group(1)
            cols = re.search(r"\(([^)]*)\)\s*VALUES", sql, re.S)
            vals = re.search(r"VALUES\s*\((.*?)\)", sql, re.S)
            if not (cols and vals):
                continue
            named = {c.strip().lower() for c in cols.group(1).replace("\n", " ").split(",")}
            terminal = named & set(TERMINAL_COLUMNS)
            resets = re.search(r"\b(FALSE|NULL)\b", vals.group(1), re.I)
            if terminal and resets:
                rel = str(path.relative_to(BACKEND.parent)).replace("\\", "/")
                found.add((rel, table))
    return found


def main() -> int:
    found = violations()

    new = found - KNOWN_UNFIXED
    check(not new,
          f"no NEW writer resets a terminal outcome column through REPLACE "
          f"(new violations: {sorted(new) or 'none'}) - this is the defect that could erase "
          f"a settled round's outcome with no error")

    fixed = KNOWN_UNFIXED - found
    check(not fixed,
          f"the known-unfixed list contains no stale entries (already repaired: "
          f"{sorted(fixed) or 'none'}) - remove them from KNOWN_UNFIXED so the list keeps "
          f"meaning what it says")

    check(("backend/database.py", "price_to_beat") not in found,
          "price_to_beat - the round table holding 14,368 settled outcomes - is NOT among "
          "them; it is insert-only for resolved rows")

    check(len(KNOWN_UNFIXED) <= 5,
          f"the remaining debt is {len(KNOWN_UNFIXED)} statements and bounded - this list is "
          f"allowed to shrink, never grow")

    print(f"\nTERMINAL OUTCOMES NOT REPLACEABLE: PASS ({CHECKS} checks, "
          f"{len(KNOWN_UNFIXED)} known unfixed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
