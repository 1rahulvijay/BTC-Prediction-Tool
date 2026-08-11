"""
`lean_hit` - the column the schema names as THE betting-accuracy metric - was not measuring
the contract.

    lean_hit = (_raw_lean == "UP") == (actual_move_usd > 0)

`actual_move_usd` is `resolution_price - entry`. Under first touch `resolution_price` is the
BARRIER on a touching row, so that comparison is right there by accident. On a TIMEOUT row
it is the last bar's CLOSE, so the rule credited a lean whenever a small residual drift
happened to agree with it - on a row the contract graded NEUTRAL, where no barrier was
reached and the bet did not win.

It was therefore not the endpoint question and not the first-touch question, but a mixture:
barrier sign on some rows, residual drift on others.

NEUTRAL is 46.6% of 5m rows, so this was nearly half the metric.

Correcting it moves the number a long way, which is why the threshold namespaces matter as
much as the fix: `CASCADE_MIN_ACCURACY = 0.62`, `bias_strength = (acc - 0.5) * 0.6` and the
`< 0.45` retrain trigger all take 0.5 as no-skill.

Run directly:  python backend/tests/test_lean_hit_contract_truth.py
"""

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def body_of(path: Path, name: str) -> str:
    """The function's CODE, with no docstring and no comments, via `ast.unparse`.

    Two weaker approaches failed here first, both the same mistake - the test matching the
    fix's own description of the code it removed:

      - subtracting `ast.get_docstring` from raw source removes NOTHING, because that call
        returns the cleaned text rather than the literal span;
      - `ast.get_source_segment` per statement still carries every comment nested inside a
        compound statement, and the explanatory comment above a fix always quotes the line
        it replaced.

    `ast.unparse` emits code only, so an assertion here can only be satisfied by code.
    Formatting is normalised, so match on normalised forms.
    """
    src = path.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    return "\n".join(ast.unparse(s) for s in stmts)


def _verifier_with(decided_correct, decided_wrong, timeouts):
    from prediction_verifier import PredictionVerifier
    v = PredictionVerifier()
    rows = ([("UP", True)] * decided_correct + [("DOWN", False)] * decided_wrong
            + [("NEUTRAL", False)] * timeouts)
    for actual, ok in rows:
        v.verified_by_horizon[5].append({
            "horizon": 5, "direction": "UP", "raw_direction": "UP", "confidence": 0.6,
            "actual_direction": actual, "lean_hit": ok, "hit": ok,
            "actual_move_usd": 10.0, "regime": "RANGE"})
    v._update_accuracy_cache()
    return v


def test_lean_hit_is_contract_graded():
    print("\nlean_hit is decided by the CONTRACT, not by the move sign")
    src = body_of(BACKEND / "prediction_verifier.py", "check_and_verify")
    chk("lean_hit = _raw_lean == actual_direction" in src,
        "the graded outcome decides it - it is produced by the same grade() call as the "
        "price and was sitting in scope the whole time")
    chk("actual_move_usd > 0" not in src,
        "and no comparison against the move sign survives anywhere in the grading path")
    chk("'NEUTRAL'" in src.split("lean_hit = _raw_lean")[0].rsplit("_raw_lean =", 1)[-1],
        "a NEUTRAL outcome is admitted to the comparison rather than skipped - dropping "
        "those rows would leave `lean_accuracy` silently equal to the decisive rate and the "
        "46.6% of unwinnable rows invisible again")

    print("\n     ... and a row with no stored lean_hit is graded the same way")
    from prediction_verifier import PredictionVerifier
    v0 = PredictionVerifier()
    for actual in ["UP"] * 10 + ["NEUTRAL"] * 10:
        v0.verified_by_horizon[5].append({
            "horizon": 5, "direction": "UP", "raw_direction": "UP", "confidence": 0.6,
            "actual_direction": actual, "hit": actual == "UP",
            "actual_move_usd": 10.0, "regime": "RANGE"})     # NOTE: no lean_hit
    v0._update_accuracy_cache()
    a0 = v0.get_accuracy_summary()[5]
    chk(a0["lean_total"] == 20 and a0["lean_accuracy"] == 0.5,
        f"the in-memory fallback counts all 20 leans and scores {a0['lean_accuracy']} - the "
        f"10 NEUTRAL rows are misses, not exclusions, even though `actual_move_usd` is "
        f"POSITIVE on every one of them and the old rule would have called them all wins")

    print("\n     ... a NEUTRAL outcome is a MISS for a directional lean")
    v = _verifier_with(decided_correct=20, decided_wrong=10, timeouts=30)
    a = v.get_accuracy_summary()[5]
    chk(a["lean_accuracy"] == round(20 / 60, 4) and a["lean_total"] == 60,
        f"20 correct of 60 leans -> {a['lean_accuracy']} - no barrier was reached on the 30 "
        f"timeouts, so those bets did not win")
    chk(a["lean_decisive_accuracy"] == round(20 / 30, 4)
        and a["lean_decisive_total"] == 30,
        f"and over the rows the contract DECIDED it is {a['lean_decisive_accuracy']} - a "
        f"different question, reported separately rather than blended")

    print("\n     ... and the inflation is measured, not asserted")
    random.seed(7)
    p_neutral = 40206 / (23009 + 40206 + 23110)          # measured 5m label distribution
    n, old, new = 200_000, 0, 0
    for _ in range(n):
        lean = random.choice(("UP", "DOWN"))
        if random.random() < p_neutral:
            outcome, move_agrees = "NEUTRAL", random.random() < 0.5
        else:
            outcome = random.choice(("UP", "DOWN"))
            move_agrees = (outcome == lean)
        old += move_agrees
        new += (lean == outcome)
    chk(abs(old / n - 0.50) < 0.01,
        f"on a ZERO-SKILL model the old rule reported {old / n:.3f} - a coin flip, because "
        f"the timeout rows contributed a coin flip on residual drift")
    chk(abs(new / n - 0.27) < 0.01,
        f"the contract says {new / n:.3f}. The gap is {(old - new) / n:+.3f} of all rows, "
        f"and it is entirely the {p_neutral:.1%} of rows that never reached a barrier")


def test_threshold_namespaces():
    print("\nthresholds chosen against a coin flip read the coin-flip metric")
    mdl = body_of(BACKEND / "model.py", "generate_ensemble_prediction")
    chk("lower_acc_stats.get('lean_decisive_accuracy')" in mdl,
        "the cascade gate reads the DECISIVE rate - `CASCADE_MIN_ACCURACY = 0.62` and "
        "`bias_strength = (recent_accuracy - 0.5) * 0.6` both take 0.5 as no-skill, and the "
        "all-rows rate cannot exceed ~0.534 at 5m, so feeding it there would have made this "
        "cascade permanently inert while still looking like a measurement")
    chk("lower_acc_stats.get('lean_decisive_total')" in mdl,
        "and its sample floor counts the same rows the rate is computed over")

    pv = BACKEND / "prediction_verifier.py"
    fb = body_of(pv, "get_learning_feedback")
    chk("acc['lean_decisive_accuracy'] < 0.45" in fb,
        "the retrain trigger reads it too - at 0.27 for a zero-skill model the all-rows rate "
        "would have latched `needs_retrain` on permanently")
    chk("acc.get('lean_decisive_accuracy') is not None" in fb,
        "and an unmeasured rate does not trigger a retrain: absent is not 'below the bar'")
    chk("x.get('lean_decisive_accuracy')" in fb,
        "the TREND is computed on the same series the 0.45 bar applies to - a trend measured "
        "on one metric and compared against a threshold chosen for another is the same "
        "mistake one level up")

    print("\n     ... and an unmeasured lean rate never triggers a retrain")
    v = _verifier_with(decided_correct=0, decided_wrong=0, timeouts=40)
    a = v.get_accuracy_summary()[5]
    chk(a["lean_decisive_total"] == 0 and a["lean_decisive_accuracy"] is None,
        "with no decided rows there is no decisive rate to report")
    fbk = v.get_learning_feedback().get(5, {})
    chk(fbk.get("needs_retrain") is not True,
        "so nothing fires on it - 40 timeouts is not evidence a model is broken")


def test_stored_rows_agree():
    print("\nthe stored column and the restore path use the same rule")
    db = (BACKEND / "database.py").read_text(encoding="utf-8")
    chk("SET lean_hit = (raw_direction = actual_direction)" in db,
        "the backfill grades legacy rows by the contract's recorded outcome")
    chk("SET lean_hit = ((raw_direction = 'UP'   AND actual_move > 0)" not in db,
        "and the endpoint-sign backfill is gone")
    chk("AND actual_direction IS NOT NULL AND actual_direction <> ''" in db,
        "a row that cannot say what the contract decided is left NULL - unknown and excluded "
        "from the metric - rather than assigned an answer by a rule the contract does not use")

    pv = body_of(BACKEND / "prediction_verifier.py", "_update_accuracy_cache")
    chk("return rd == ad" in pv,
        "and the in-memory fallback for older rows grades the same way")
    chk("(mv > 0)" not in pv and "mv > 0" not in pv,
        "rather than falling back to the sign rule it was written to replace")


def main():
    print("=" * 78)
    print("LEAN_HIT CONTRACT TRUTH")
    print("=" * 78)
    test_lean_hit_is_contract_graded()
    test_threshold_namespaces()
    test_stored_rows_agree()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"LEAN_HIT CONTRACT TRUTH: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("LEAN_HIT CONTRACT TRUTH: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
