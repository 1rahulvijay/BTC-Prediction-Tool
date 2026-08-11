"""No writer may reset a TERMINAL OUTCOME column via INSERT OR REPLACE.

THE BUG CLASS
    `log_price_to_beat` used `INSERT OR REPLACE INTO price_to_beat (... resolved) VALUES
    (..., FALSE)`, naming 20 of 32 columns. On DuckDB, omitted columns in this form are
    preserved, but the named `resolved` column was driven back to FALSE. Re-logging a settled
    round therefore hid an existing outcome from resolved-only readers with no error or log
    line. A no-column-list REPLACE is worse because it can rewrite every field.

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

# Any REPLACE against these tables is forbidden, regardless of whether it uses literals,
# bound parameters, a dynamic f-string, or no column list. They contain immutable evidence.
TERMINAL_TABLES = {
    "price_to_beat", "kronos_predictions", "model_predictions", "forward_ev_ledger",
    "fsr_ppo_decisions", "ab_results", "shadow_signals", "round_settlement_truth",
    "settlement_checkpoint", "pm_round_settlements", "complete_trade_forecasts_v2",
    "model_revision_outcomes", "open_position_action_outcomes", "shadow_orders",
}

# Horizon prediction tables are created from an f-string (predictions_{tf}m), so they cannot
# be represented by a single literal table name. Any concrete or schema-template form is
# protected. This also covers a newly added horizon without a hand-maintained list update.
TERMINAL_TABLE_PATTERNS = (
    re.compile(r"^predictions_(?:\d+|\{expr\})m$", re.I),
)

# A schema-derived candidate is exempt only when the terminal-looking column is not mutable
# outcome state. Keeping the reason beside the exception makes additions reviewable.
TERMINAL_SCHEMA_EXEMPTIONS = {
    "hf_crossing_events": (
        "move is the immutable price move measured at event creation; labels live in "
        "hf_crossing_labels"
    ),
    "historical_replay_predictions": (
        "offline replay rows include actual_price at creation and are intentionally idempotent"
    ),
}

#: KNOWN, UNFIXED violations - each is `INSERT OR REPLACE ... resolved ... FALSE`, the same
#: defect repaired in log_price_to_beat. Listed rather than silently tolerated so they cannot
#: be forgotten, and so a NEW one fails this test on the day it is written.
#:
#: THIS LIST MAY SHRINK, NEVER GROW. Remove an entry when its statement is converted to
#: `INSERT ... ON CONFLICT (pk) DO UPDATE SET <non-terminal columns> WHERE <table>.resolved
#: = FALSE`, which is what price_to_beat now does.
KNOWN_UNFIXED = set()

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _sql_text(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            parts.append(value.value if isinstance(value, ast.Constant) else "{expr}")
        return "".join(str(part) for part in parts)
    return None


def _replace_table(sql: str) -> str | None:
    match = re.search(
        r"(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+([A-Za-z_]\w*)", sql, re.I)
    return match.group(1).lower() if match else None


def _create_table(sql: str) -> str | None:
    match = re.search(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_]\w*(?:\{expr\}\w*)?)",
        sql,
        re.I,
    )
    return match.group(1).lower() if match else None


def _is_terminal_table(table: str) -> bool:
    return table in TERMINAL_TABLES or any(
        pattern.fullmatch(table) for pattern in TERMINAL_TABLE_PATTERNS
    )


def terminal_schema_candidates() -> set[str]:
    """Production tables declaring a terminal-looking column.

    This binds TERMINAL_TABLES to executable schemas. Adding a new evidence table without
    protection or a documented exemption therefore fails this test immediately.
    """
    candidates: set[str] = set()
    terminal_column = re.compile(
        r"\b(?:" + "|".join(re.escape(column) for column in TERMINAL_COLUMNS) + r")\b",
        re.I,
    )
    for path in sorted(BACKEND.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            sql = _sql_text(node)
            if sql is None or not terminal_column.search(sql):
                continue
            if table := _create_table(sql):
                candidates.add(table)
    return candidates


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
            sql = _sql_text(node)
            if sql is None:
                continue
            table = _replace_table(sql)
            if table is None:
                continue
            if _is_terminal_table(table):
                rel = str(path.relative_to(BACKEND.parent)).replace("\\", "/")
                found.add((rel, table))
                continue
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
    probe_sources = (
        "x = 'INSERT OR REPLACE INTO price_to_beat VALUES (?,?,?)'",
        "x = 'INSERT OR REPLACE INTO price_to_beat (id,resolved) VALUES (?,?)'",
        "x = 'REPLACE INTO price_to_beat (id,resolved) VALUES (?,FALSE)'",
        "x = f'INSERT OR REPLACE INTO shadow_signals ({cols}) VALUES ({vals})'",
    )
    for source in probe_sources:
        tree = ast.parse(source)
        tables = {_replace_table(text) for node in ast.walk(tree)
                  if (text := _sql_text(node)) is not None}
        check(any(table and _is_terminal_table(table) for table in tables),
              "terminal-table rule catches bound, literal, f-string and no-column REPLACE")

    candidates = terminal_schema_candidates()
    uncovered = {
        table for table in candidates
        if not _is_terminal_table(table) and table not in TERMINAL_SCHEMA_EXEMPTIONS
    }
    stale_exemptions = set(TERMINAL_SCHEMA_EXEMPTIONS) - candidates
    check(not uncovered,
          f"every production schema with a terminal-outcome column is protected or explicitly "
          f"exempt (uncovered: {sorted(uncovered) or 'none'})")
    check(not stale_exemptions,
          f"terminal schema exemptions are still backed by executable schemas "
          f"(stale: {sorted(stale_exemptions) or 'none'})")

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

    check(len(KNOWN_UNFIXED) == 0,
          f"the remaining debt is {len(KNOWN_UNFIXED)} statements and bounded - this list is "
          f"now zero and may never grow")

    print(f"\nTERMINAL OUTCOMES NOT REPLACEABLE: PASS ({CHECKS} checks, "
          f"{len(KNOWN_UNFIXED)} known unfixed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
