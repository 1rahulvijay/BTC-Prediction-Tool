"""
The calibration map now knows which question it answers (the P0-14 consumer half).

`PrecisionEngine.fit_from_db` pooled every resolved lean into one isotonic map and graded
correctness by ENDPOINT SIGN (`actual_move > 0`) while the ensemble trains on
`first_touch_triple_barrier_v1`. It could not do otherwise: `predictions_{h}m` had no
`target_contract` column, so the rows could not be separated. That column now exists and
`log_prediction` requires it.

Two things follow, and the second is the one that matters:

  - the fit filters on the contract and grades with the CONTRACT'S own outcome;
  - provenance is EARNED by a fit that happened, so a run that finds no admissible rows
    still refuses.

Run directly:  python backend/test_calibration_contract_filter.py
"""

import ast
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _seed(conn, horizon, rows):
    conn.execute(f"""
        CREATE TABLE predictions_{horizon}m (
            id VARCHAR, timestamp BIGINT, resolved BOOLEAN, raw_direction VARCHAR,
            actual_direction VARCHAR, actual_move DOUBLE, confidence DOUBLE,
            regime VARCHAR, conviction DOUBLE, model_version VARCHAR,
            target_contract VARCHAR)
    """)
    for i, (raw, actual, move, conf, contract) in enumerate(rows):
        conn.execute(
            f"INSERT INTO predictions_{horizon}m VALUES "
            f"(?, ?, TRUE, ?, ?, ?, ?, 'RANGE', 0.5, 'v1', ?)",
            (f"p{i}", 1_800_000_000_000 + i, raw, actual, move, conf, contract))


def main():
    print("=" * 78)
    print("CALIBRATION CONTRACT FILTER")
    print("=" * 78)

    import duckdb
    import calibration
    import database
    import target_contract as tc

    FT = tc.FIRST_TOUCH_TRIPLE_BARRIER_V1
    EP = tc.ENDPOINT_SETTLEMENT_V1

    print("\nthe fit selects ONE contract and grades by that contract's outcome")
    tmp = Path(tempfile.mkdtemp(prefix="calib_")) / f"{uuid.uuid4().hex}.duckdb"
    conn = duckdb.connect(str(tmp))
    rows = []
    # 120 first-touch rows. Correct by the CONTRACT (raw == actual) on 90 of them, while
    # `actual_move` disagrees on every single one - a lean that touched the upper barrier
    # first and then closed lower is a contract HIT and an endpoint MISS.
    for i in range(120):
        contract_correct = i < 90
        rows.append(("UP", "UP" if contract_correct else "DOWN", -5.0, 0.60, FT))
    # 200 endpoint rows that must not leak into a first-touch fit.
    for i in range(200):
        rows.append(("DOWN", "DOWN", -9.0, 0.90, EP))
    # 200 legacy rows that cannot say which question they answered.
    for i in range(200):
        rows.append(("UP", "UP", 9.0, 0.95, "UNKNOWN_LEGACY"))
    _seed(conn, 5, rows)
    _seed(conn, 15, rows)
    conn.close()

    real_connect, real_min = database._connect, calibration.MIN_CALIB_SAMPLES
    database._connect = lambda *a, **k: duckdb.connect(str(tmp))
    calibration.MIN_CALIB_SAMPLES = 50
    try:
        eng = calibration.PrecisionEngine()
        eng.fit_from_db(FT)
        chk(eng.contract_provenance == "RECORDED" and eng.fitted_under_contract == FT,
            "a successful fit RECORDS the contract it was fitted under")
        chk(eng.calib_n.get(5) == 120,
            f"and it used only the 120 first-touch rows, not the 520 present "
            f"(got {eng.calib_n.get(5)}) - pooling contracts produced a map that answered "
            f"neither, and both are floats in [0, 1] so the value never revealed the mixture")
        chk(abs(eng.global_rate.get(5, 0.0) - 0.75) < 1e-6,
            f"the base rate is {eng.global_rate.get(5):.3f} - the CONTRACT's 90/120. Under "
            f"the old `actual_move > 0` rule every one of these rows was a miss, so the same "
            f"evidence would have taught a rate of 0.000")
        chk(eng.is_admissible_for(FT) and not eng.is_admissible_for(EP),
            "and the map is admissible for the contract it was fitted under, and only that one")

        print("\n     ... and legacy rows are excluded rather than pooled in")
        eng2 = calibration.PrecisionEngine()
        eng2.fit_from_db(EP)
        chk(eng2.calib_n.get(5) == 200,
            f"an endpoint fit sees only its own 200 rows (got {eng2.calib_n.get(5)}) - the "
            f"200 UNKNOWN_LEGACY rows join neither fit, because a row that cannot say which "
            f"question it answers cannot calibrate an answer to a specific one")

        print("\n     ... and a fit that finds nothing admissible still REFUSES")
        eng3 = calibration.PrecisionEngine()
        eng3.fit_from_db("some_contract_with_no_rows_v1")
        chk(eng3.contract_provenance == "UNRECORDED",
            "provenance is EARNED by a fit that happened, not asserted by the code path "
            "that intended one")
        chk(eng3.calibrated(5, 0.62, required_contract=FT) is None,
            "so the consumer still gets None and falls back to raw confidence - 'we do not "
            "know' must not read as 'yes', which is the whole lesson of the contract layer")
        print("\n     ... and a release swap clears the provenance with the maps")
        eng4 = calibration.PrecisionEngine()
        eng4.fit_from_db(FT)
        assert eng4.contract_provenance == "RECORDED"
        report = eng4.bind_release("new_bundle", target_contract=FT)
        chk(eng4.contract_provenance == "UNRECORDED" and not eng4.calibrators,
            "a cleared map does not keep the provenance it earned before the swap - the "
            "report already says available=False and the flag must not contradict it")
        chk(report.get("available") is False and not eng4.is_admissible_for(FT),
            "so the engine refuses again until it is refitted under the new release")
    finally:
        database._connect = real_connect
        calibration.MIN_CALIB_SAMPLES = real_min

    print("\nthe stale blocker is gone from the source, not just from the behaviour")
    src = (BACKEND / "calibration.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fit_from_db")
    # Strip the docstring by NODE. `ast.get_docstring` returns the CLEANED text - dedented
    # and whitespace-normalised - so subtracting it from raw source silently removes
    # nothing, and the assertion below then matches the fix's own description of the code
    # it removed. That trap has now fired six times in this repository.
    statements = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                                 and isinstance(fn.body[0].value, ast.Constant)
                                 and isinstance(fn.body[0].value.value, str)) else fn.body
    body = "\n".join(ast.get_source_segment(src, st) or "" for st in statements)
    chk("COALESCE(target_contract, ?) = ?" in body,
        "the query filters on the stored contract")
    chk("raw_direction = actual_direction" in body,
        "and grades with the contract's own recorded outcome")
    chk("actual_move > 0" not in body,
        "the endpoint-sign rule is gone from the fit - it graded a first-touch model by a "
        "rule that disagrees with its contract on roughly a quarter of paths")
    chk("has NO target_contract" not in src,
        "and the comment asserting the column does not exist is gone: it was still there "
        "after the column shipped, which is how a solved blocker keeps closing a gate")

    print("\n" + "=" * 78)
    if FAILURES:
        print(f"CALIBRATION CONTRACT FILTER: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("CALIBRATION CONTRACT FILTER: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
