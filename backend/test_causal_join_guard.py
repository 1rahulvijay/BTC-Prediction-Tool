"""An economic study may not join a market STATE to an executable QUOTE without a causal rule.

WHY THIS GATE EXISTS
    Five studies in research/ produced economic numbers from a join that selected the state and
    the quote independently:

        state:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) = 1
        quote:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts)           = 1
        join:   ON round_id AND side

    Nothing required state.ts <= quote.ts. Measured on the live sample, the state was observed
    AFTER the decision in 93.5% of rows, median +8.1s, max +17.8s - a quarter of the remaining
    time in a window with 20-32 seconds left. One of those studies produced the only positive
    candidate this repository has ever had. It was hindsight.

    Every one of those scripts passed CI, passed its own preregistered gates, carried matched
    controls and day-block lower bounds, and was wrong anyway - because no check asked whether
    the inputs existed when the decision was made.

THE RULE
    A python file under research/ or backend/ that joins `rule_paper_trades` (executable quotes)
    to `round_state_snapshots` (model state) must contain an explicit causal timestamp
    constraint, or be registered as RETRACTED in research/research_status.py.

    Accepted evidence of causality (any one):
        *  s.ts <= q.decision_ts        - or the same comparison with either alias
        *  merge_asof(..., direction="backward")
        *  a documented import of the prejoined causal construction

    This is a text-level check on purpose. It cannot verify semantics, and it does not try:
    its job is to make the ABSENCE of a causal rule impossible to introduce silently. A file
    that games it by writing the token without meaning it has at least had to type the words.

    python backend/test_causal_join_guard.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("research", "backend")

QUOTE_TABLE = "rule_paper_trades"
STATE_TABLE = "round_state_snapshots"

CAUSAL_TOKENS = (
    r"\.ts\s*<=\s*\w*\.?decision_ts",
    r"s\.ts\s*<=\s*q\.\w+",
    r"state_ts\s*<=\s*decision_ts",
    r'merge_asof\([^)]*direction\s*=\s*"backward"',
    r"merge_asof\([^)]*direction\s*=\s*'backward'",
    r"from\s+causal_decision_join\s+import",
    r"causal_decision_join",
)


def _actually_joins(text: str) -> bool:
    """Both tables must appear near the SAME `JOIN`, not merely somewhere in the file.

    A plain "does the file mention both tables" test flags backend/database.py, which DEFINES
    both with CREATE TABLE and joins nothing. A gate that fires on the schema module trains
    people to disable the gate, so proximity to a JOIN keyword is the discriminator.
    """
    upper = text.upper()
    for match in re.finditer(r"\bJOIN\b", upper):
        window = text[max(0, match.start() - 600): match.start() + 600]
        if QUOTE_TABLE in window and STATE_TABLE in window:
            return True
    return False


def _retracted() -> set[str]:
    sys.path.insert(0, str(REPO / "research"))
    try:
        from research_status import REGISTRY, RETRACTED
    except ImportError:
        return set()
    return {name for name, entry in REGISTRY.items() if entry["status"] == RETRACTED}


def offenders() -> list[tuple[str, str]]:
    retracted = _retracted()
    found: list[tuple[str, str]] = []
    for directory in SCAN_DIRS:
        base = REPO / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name == Path(__file__).name:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not _actually_joins(text):
                continue                        # not an economic state/quote join
            if path.name in retracted:
                continue                        # declared invalid, deliberately kept
            if any(re.search(token, text) for token in CAUSAL_TOKENS):
                continue                        # declares a causal rule
            found.append((path.relative_to(REPO).as_posix(),
                          "joins quotes to state with no causal timestamp rule"))
    return found


def selftest() -> None:
    """The check must detect a real offender, not merely return an empty list."""
    import tempfile
    noncausal = (f"q = 'SELECT * FROM {QUOTE_TABLE}'\n"
                 f"s = 'SELECT * FROM {STATE_TABLE}'\n"
                 "sql = q + ' JOIN ' + s + ' ON round_id'\n")
    causal = noncausal + "sql += ' AND s.ts <= q.decision_ts'\n"
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "probe.py"
        bad.write_text(noncausal, encoding="utf-8")
        text = bad.read_text(encoding="utf-8")
        assert QUOTE_TABLE in text and STATE_TABLE in text
        assert not any(re.search(t, text) for t in CAUSAL_TOKENS), \
            "a join with no causal rule must be detected"
        # backend/database.py DEFINES both tables and joins nothing. A gate that fires on the
        # schema module trains people to switch the gate off.
        schema = Path(tmp) / "schema_like.py"
        schema.write_text(
            f"CREATE_A = 'CREATE TABLE {QUOTE_TABLE} (x INT)'\n"
            f"CREATE_B = 'CREATE TABLE {STATE_TABLE} (y INT)'\n",
            encoding="utf-8")
        assert not _actually_joins(schema.read_text(encoding="utf-8")), \
            "a module that merely DEFINES both tables must not be flagged"

        good = Path(tmp) / "probe_ok.py"
        good.write_text(causal, encoding="utf-8")
        assert any(re.search(t, good.read_text(encoding="utf-8")) for t in CAUSAL_TOKENS), \
            "a declared causal constraint must satisfy the check"


def main() -> int:
    selftest()
    retracted = _retracted()
    bad = offenders()

    print("=" * 96)
    print("CAUSAL JOIN GUARD - quotes may not be joined to state without a causal rule")
    print("=" * 96)
    print(f"  registered as RETRACTED (exempt, deliberately kept) : {len(retracted)}")
    for name in sorted(retracted):
        print(f"      {name}")
    print(f"  undeclared non-causal economic joins                : {len(bad)}")

    if bad:
        print()
        for name, reason in bad:
            print(f"    {name}: {reason}")
        print()
        print("  Require the state to exist at the decision instant, e.g.")
        print("      AND s.ts <= q.decision_ts")
        print("      AND s.ts >= q.decision_ts - <max age>")
        print("  or consume research/causal_decision_join.py, which already does it.")
        print()
        print("  93.5% of rows in the previous construction used a state from AFTER the")
        print("  decision. It produced this repository's only positive candidate, and that")
        print("  candidate was hindsight.")
        return 1

    print("\n  PASS - every economic state/quote join declares a causal timestamp rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
