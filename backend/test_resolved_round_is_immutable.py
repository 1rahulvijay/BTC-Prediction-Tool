"""A settled round's outcome must survive a re-log of its prediction, byte for byte.

WHAT WAS WRONG
    log_price_to_beat used `INSERT OR REPLACE`, listing 20 of price_to_beat's 32 columns and
    hard-coding `resolved = FALSE`. OR REPLACE rewrites the entire row, so every omitted
    column reverted to its default:

        actual_price -> NULL     actual_direction -> NULL     hit -> NULL
        move -> NULL             settlement_source -> NULL    resolved -> FALSE

    Re-logging an id that had already settled erased that round's outcome silently: no error,
    no constraint violation, nothing in any log. The live store holds 14,372 rounds of which
    14,368 are resolved - the labels the retrain learns from and the population head-health
    measures against. A prediction writer must not be able to destroy a settlement record.

    This test uses a REAL DuckDB through the real function. A mock would prove nothing here,
    because the defect lives in what SQL the database executes.

    python backend/test_resolved_round_is_immutable.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _entry(rid, **kw):
    base = {
        "id": rid, "timestamp": 1_700_000_000_000, "horizon": 5,
        "price_to_beat": 100_000.0, "our_direction": "UP", "signal": "LEAN",
        "conviction": 0.6, "actionable": True, "kronos_direction": "UP",
        "target_price": 100_050.0, "verify_at": 1_700_000_300_000,
        "lean_source": "test", "regime": "NORMAL", "source": "pyth",
        "pred_contract": "C1", "grading_contract": "G1",
        "horizon_overlap": 0, "grade_usable": True,
    }
    base.update(kw)
    return base


def main() -> int:
    tmp = tempfile.mkdtemp()
    os.environ["BTC_DATA_DIR"] = tmp          # isolate: never touch the real store
    os.environ.pop("BTC_DB_PATH", None)
    sys.path.insert(0, str(BACKEND))
    import database as db
    db.init_db()

    RID = "round-immutable-1"
    db.log_price_to_beat(_entry(RID))

    conn = db._connect()
    try:
        # Settle it, exactly as the resolver would.
        conn.execute("""
            UPDATE price_to_beat
               SET actual_price = 100_123.0, actual_direction = 'UP', hit = TRUE,
                   move = 123.0, settlement_source = 'official:chainlink', resolved = TRUE
             WHERE id = ?""", (RID,))
        settled = conn.execute(
            "SELECT actual_price, actual_direction, hit, move, settlement_source, resolved "
            "FROM price_to_beat WHERE id = ?", (RID,)).fetchone()
    finally:
        conn.close()
    check(settled[5] is True and settled[1] == "UP",
          f"the fixture round is genuinely settled before the re-log ({settled[1]}, "
          f"resolved={settled[5]}) - otherwise this test proves nothing")

    # THE DANGEROUS CALL: the same id logged again, with different prediction values.
    db.log_price_to_beat(_entry(RID, our_direction="DOWN", conviction=0.1,
                                price_to_beat=999.0, signal="WAIT"))

    conn = db._connect()
    try:
        after = conn.execute(
            "SELECT actual_price, actual_direction, hit, move, settlement_source, resolved "
            "FROM price_to_beat WHERE id = ?", (RID,)).fetchone()
        pred = conn.execute(
            "SELECT our_direction, conviction, price_to_beat FROM price_to_beat WHERE id = ?",
            (RID,)).fetchone()
        n_rows = conn.execute("SELECT count(*) FROM price_to_beat WHERE id = ?",
                              (RID,)).fetchone()[0]
    finally:
        conn.close()

    check(after == settled,
          "EVERY resolution field is byte-identical after re-logging the same id - outcome, "
          "price, hit, move, settlement source and the resolved flag all survive")
    check(after[5] is True,
          "specifically, resolved stays TRUE - the old statement hard-coded it back to FALSE, "
          "so a settled round silently became an open one")
    check(pred == ("UP", 0.6, 100_000.0),
          f"and the PREDICTION fields are not rewritten either on a settled round {pred} - "
          f"the conflict update is skipped whole, never applied in part")
    check(n_rows == 1,
          "the re-log did not create a duplicate row - it is one round, still one record")

    # An UNRESOLVED round must still be refinable, or this fix would break live use.
    OPEN = "round-still-open"
    db.log_price_to_beat(_entry(OPEN))
    db.log_price_to_beat(_entry(OPEN, our_direction="DOWN", conviction=0.9))
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT our_direction, conviction, resolved FROM price_to_beat WHERE id = ?",
            (OPEN,)).fetchone()
    finally:
        conn.close()
    check(row == ("DOWN", 0.9, False),
          "an UNRESOLVED round is still updated in place - the fix protects settled evidence "
          "without freezing rounds that are mid-flight")

    # The statement itself must not be able to write outcome columns at all.
    #
    # Read the SQL LITERAL via AST, not the function text: the docstring quotes the old
    # `INSERT OR REPLACE` in order to explain it, so a substring scan of the source fails on
    # the explanation while the code is correct. The same trap has now caught me twice.
    import ast
    src = (BACKEND / "database.py").read_text(encoding="utf-8", errors="replace")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "log_price_to_beat")
    sql = [c.value for c in ast.walk(fn)
           if isinstance(c, ast.Constant) and isinstance(c.value, str)
           and "price_to_beat" in c.value and "INSERT" in c.value.upper()]
    check(len(sql) == 1, f"the function issues exactly one INSERT statement ({len(sql)} found)")
    stmt = sql[0]
    check("OR REPLACE" not in stmt.upper(),
          "log_price_to_beat no longer uses OR REPLACE, which rewrites omitted columns to "
          "their defaults")
    # WHITELIST the SET clause rather than blacklisting a few names.
    #
    # A blacklist of `col = excluded` misses `actual_direction = NULL` and `resolved = FALSE`,
    # and the WHERE guard hides both from behavioural tests because the update never fires on
    # a settled row. Mutation testing surfaced exactly those two survivors. What must hold is
    # stronger and simpler: the conflict update may touch PREDICTION columns and nothing else.
    import re
    body = stmt[stmt.upper().index("DO UPDATE SET") + len("DO UPDATE SET"):]
    body = body[:body.upper().index("WHERE")]
    assigned = {m.group(1) for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", body)}
    PREDICTION_COLUMNS = {
        "timestamp", "horizon", "price_to_beat", "our_direction", "signal", "conviction",
        "actionable", "kronos_direction", "target_price", "verify_at", "lean_source",
        "confluence_grade", "regime", "source", "pred_contract", "grading_contract",
        "horizon_overlap", "grade_usable",
    }
    stray = assigned - PREDICTION_COLUMNS
    check(not stray,
          f"the conflict update assigns ONLY prediction columns (stray: {sorted(stray) or 'none'}) "
          f"- outcome fields and the resolved flag are unreachable from the prediction writer, "
          f"whatever the WHERE guard does")
    check("resolved" not in assigned,
          "and 'resolved' specifically is never assigned on conflict - the old statement "
          "hard-coded it FALSE, which is how a settled round became open again")

    print(f"\nRESOLVED ROUND IS IMMUTABLE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
