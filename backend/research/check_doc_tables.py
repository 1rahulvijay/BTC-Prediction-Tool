"""
check_doc_tables.py — verify every markdown table in docs/active/ is well formed.

A table row with a different cell count than its header renders wrong (cells dropped or an
extra empty column). This is easy to introduce when a row is assembled by string manipulation
rather than built directly - which is exactly how the archetype table in HF_PATHSTATE_TESTS
ended up with 4 cells under a 3-cell header on 2026-07-25.

Two traps this checker handles, because a naive version got both wrong:
  * escaped pipes and pipes inside `inline code` are NOT cell separators;
  * Windows console is cp1252 - printing a doc line verbatim can crash the checker itself.

Usage: python backend/research/check_doc_tables.py [--selftest]
Exit 0 = clean, 1 = malformed rows found.
"""
from __future__ import annotations

import glob
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOCS = os.path.join(ROOT, "docs", "active", "*.md")


def cell_count(line: str) -> int:
    """Number of cells in a markdown table row, ignoring code spans and escaped pipes."""
    s = re.sub(r"`[^`]*`", "X", line)     # inline code may legally contain '|'
    s = s.replace(r"\|", "")              # escaped pipe is a literal, not a separator
    return s.count("|") - 1


def scan(pattern: str = DOCS) -> int:
    bad = 0
    for path in sorted(glob.glob(pattern)):
        lines = open(path, encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines):
            if not line.startswith("|---"):
                continue
            width = cell_count(line)
            j = i + 1
            while j < len(lines) and lines[j].startswith("|"):
                got = cell_count(lines[j])
                if got != width:
                    name = os.path.basename(path)
                    safe = lines[j][:110].encode("ascii", "replace").decode()
                    print(f"{name}  line {j+1}: header {width} cells, row {got}")
                    print(f"    {safe}")
                    bad += 1
                j += 1
    print(f"\nmalformed rows: {bad}" if bad else "\nall tables well-formed")
    return 1 if bad else 0


def selftest() -> int:
    cases = [
        ("| a | b | c |", 3, "plain row"),
        ("| a | `x|y` | c |", 3, "pipe inside code span"),
        (r"| a | x\|y | c |", 3, "escaped pipe"),
        ("| a | b |", 2, "two cells"),
    ]
    ok = True
    for line, want, label in cases:
        got = cell_count(line)
        good = got == want
        ok &= good
        print(f"  {'OK  ' if good else 'FAIL'} {label:24} got {got}, want {want}")
    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else scan())
