"""P1-3: the per-model panel must grade the contract its models were TRAINED on.

    python backend/test_per_model_grading_contract.py

THE DEFECT
    PerModelVerifier.check resolved every vote with one line:

        actual_dir = "UP" if current_price >= p["ref_price"] else "DOWN"

    `current_price` is the MAIN LOOP's price, so a loop delayed by training, CPU contention or
    a feed stall graded against a moment well past the horizon. The rule is an ENDPOINT sign,
    while these base models are trained on TRAINING_CONTRACT (first touch). And `>=` admits no
    NEUTRAL, so a flat bar was always scored UP and any model voting DOWN on it always missed.

    The main verifier had already been fixed. This panel had not, so the two described the same
    vote with two different random variables and both were labelled "accuracy" - and the
    per-model numbers are what model seats are selected on.

WHY THE PATH BELOW
    Price dips through the LOWER barrier first, then rallies far above entry. First touch says
    DOWN. Loop-time price says UP. A test on a path where the two rules agree would pass
    against the broken code, which is the only reason this fixture is shaped this way.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database                                             # noqa: E402
import target_contract as tc                                # noqa: E402
from model_verifier import PerModelVerifier                 # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


BASE_MS = 1_785_000_000_000
ENTRY = 100.0
BAND = 0.0008                       # +/- 0.08%, the verifier's default neutral band


def _code_only(src: str) -> str:
    """Source with DOCSTRINGS and '#' comments removed.

    check()'s docstring quotes the old loop-time rule verbatim in order to record what was
    wrong with it, so a raw text search fails on the very sentence documenting the fix. Only an
    ast pass distinguishes "this line runs" from "this line is prose about a line that used
    to run"."""
    import ast

    doc_lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                doc_lines.update(
                    range(value.lineno, (value.end_lineno or value.lineno) + 1))
    return chr(10).join(
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in doc_lines and not ln.strip().startswith("#"))


def _bars(specs, start_ms=BASE_MS + 60_000):
    """Production-shaped klines: SECONDS, like data_ingestion writes them."""
    out = []
    for i, (high, low, close) in enumerate(specs):
        out.append({
            "time": (start_ms + i * 60_000) // 1000,        # SECONDS on purpose
            "high": high, "low": low, "close": close, "is_closed": True,
        })
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        database.close_db()
        original = database.DB_PATH
        database.DB_PATH = os.path.join(tmp, "analytics.duckdb")
        try:
            database.init_db()
            run_checks()
        finally:
            database.close_db()
            database.DB_PATH = original
    print("\nPER-MODEL GRADING CONTRACT:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


def run_checks() -> None:
    # The two rules genuinely disagree on this path.
    down_first = _bars([
        (100.05, 99.80, 99.85),      # low 99.80 < 99.92 lower barrier -> DOWN touched first
        (104.00, 99.90, 103.50),     # then a big rally
        (106.00, 103.00, 105.00),
    ])
    verify_at = BASE_MS + 300_000

    print("the fixture actually disagrees")
    first_touch = tc.label_first_touch(
        ENTRY, [b["high"] for b in down_first], [b["low"] for b in down_first], BAND)
    endpoint_at_loop = "UP" if 105.00 >= ENTRY else "DOWN"
    chk(first_touch == tc.DOWN and endpoint_at_loop == "UP",
        f"first touch says {first_touch}, loop-time sign says {endpoint_at_loop} - a path where "
        f"they agreed could not distinguish the two rules")

    print("a committed vote is graded by the TRAINED contract")
    v = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v.record({"xgb": 0}, 5, ENTRY, BASE_MS, prediction_id="p1")     # 0 = DOWN
    chk(len(v.pending) == 1, "the vote is pending before its horizon elapses")
    chk(v.pending[0].get("target_contract") == tc.TRAINING_CONTRACT,
        "the vote carries the contract it was recorded under")
    v.check(current_price=105.00, now_ms=verify_at + 1000, klines=down_first)
    hist = list(v.history["xgb"][5])
    chk(hist == [1],
        "a DOWN vote on a path that touched DOWN first is a HIT - under the old loop-time "
        "rule the 105.00 price made this an obvious miss")

    print("and the loop-time price does not decide the grade")
    v2 = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v2.record({"xgb": 0}, 5, ENTRY, BASE_MS, prediction_id="p2")
    v2.check(current_price=1_000_000.0, now_ms=verify_at + 1000, klines=down_first)
    chk(list(v2.history["xgb"][5]) == [1],
        "an absurd loop-time price changes nothing - the grade comes from the path")

    print("NEUTRAL outcomes exist again")
    flat = _bars([(100.02, 99.98, 100.0)] * 3)
    v3 = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v3.record({"xgb": 0}, 5, ENTRY, BASE_MS, prediction_id="p3")     # DOWN vote
    v3.check(current_price=100.0, now_ms=verify_at + 1000, klines=flat)
    chk(list(v3.history["xgb"][5]) == [0],
        "a bar that touches neither barrier resolves NEUTRAL, so a DOWN vote MISSES - the "
        "old `>=` rule had no NEUTRAL and would have scored this UP")

    print("an ungradeable vote stays pending rather than being invented")
    v4 = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v4.record({"xgb": 0}, 5, ENTRY, BASE_MS, prediction_id="p4")
    v4.check(current_price=105.0, now_ms=verify_at + 1000, klines=[])
    chk(len(v4.pending) == 1 and not list(v4.history["xgb"][5]),
        "with no intrabar path the vote is NOT graded and NOT dropped")
    chk(v4.ungraded == 1, "and the refusal is counted, not silent")

    print("a late resolution is refused, not graded on the wrong moment")
    v5 = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v5.record({"xgb": 0}, 5, ENTRY, BASE_MS, prediction_id="p5")
    late = verify_at + tc.MAX_RESOLUTION_LATENESS_MS + 1
    v5.check(current_price=105.0, now_ms=late, klines=down_first)
    chk(not v5.pending and not list(v5.history["xgb"][5]),
        "beyond the lateness bound the row is dropped, not graded")
    chk(v5.invalid_late == 1, "and counted as INVALID_LATE")
    chk(tc.MAX_RESOLUTION_LATENESS_MS == 30_000,
        "the bound is the SHARED one, so this panel and the main verifier cannot drift")

    print("NEUTRAL votes stay out of the accuracy denominator")
    v6 = PerModelVerifier(horizons=(5,), neutral_band=BAND)
    v6.record({"xgb": 1}, 5, ENTRY, BASE_MS, prediction_id="p6")     # 1 = NEUTRAL vote
    v6.check(current_price=105.0, now_ms=verify_at + 1000, klines=down_first)
    chk(not list(v6.history["xgb"][5]),
        "an abstention is resolved but never counted as a hit or a miss")
    chk(v6.accuracy()["xgb"][5]["latest_vote"] == "NEUTRAL",
        "while the raw argmax is still reported")

    print("both verifiers resolve through ONE function")
    src = (Path(__file__).resolve().parent / "model_verifier.py").read_text(encoding="utf-8")
    code = _code_only(src)
    chk("tc.grade(" in code, "the per-model panel calls target_contract.grade")
    chk('"UP" if current_price' not in code,
        "and the loop-time sign rule is gone from the code, not merely bypassed")
    main_src = (Path(__file__).resolve().parent
                / "prediction_verifier.py").read_text(encoding="utf-8")
    chk("_tc.grade(" in main_src, "the main verifier calls the same function")


if __name__ == "__main__":
    raise SystemExit(main())
