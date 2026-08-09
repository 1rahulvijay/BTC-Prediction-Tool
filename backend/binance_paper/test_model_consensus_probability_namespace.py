"""Exit thresholds must be compared in the SAME probability namespace entry admits on.

THE DEFECT
    Entry admits on `calibratedConfidence` against `minimum_calibrated_probability`. The exit
    path read RAW `probUp`/`probDown` and compared them to that same calibrated threshold -
    two different quantities against one number.

    Calibration exists precisely because raw and calibrated disagree, so:

        opened at calibrated .61   ->   closed on raw .54 that calibrates to .60   (too early)
        held on raw .59            ->   which calibrates to .52                    (too late)

    Neither direction is intended, and both look reasonable in a log.

    python -m backend.binance_paper.test_model_consensus_probability_namespace
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0
SRC = Path(__file__).resolve().parent.parent / "binance_paper" / "strategies" / "model_consensus.py"


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and "minimum_calibrated_probability" in ast.unparse(n)
               and "probUp" in ast.unparse(n)), None)
    check(fn is not None, "the exit path that uses the calibrated threshold is present")
    body = ast.unparse(fn)

    # Find what is actually compared against the calibrated bound.
    compared = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node)
        if "minimum_calibrated_probability" in rendered:
            compared.append(ast.unparse(node.left))
    check(compared, "a comparison against minimum_calibrated_probability exists")
    check(all("raw" not in c.lower() for c in compared),
          f"and its left-hand side is not a RAW probability ({compared}) - it previously read "
          f"probUp/probDown straight into a calibrated bound")

    # The assigned source of that value must be the calibrated field.
    assigns = {t.id: ast.unparse(n.value)
               for n in ast.walk(fn) if isinstance(n, ast.Assign)
               for t in n.targets if isinstance(t, ast.Name)}
    src_expr = assigns.get(compared[0], "")
    check("calibratedConfidence" in src_expr,
          f"the compared value comes from calibratedConfidence ({src_expr[:60]}) - the same "
          f"field the ENTRY gate admits on, so both ends of a trade speak one namespace")

    # The collapse floor is a bound too, and must not be left in the other namespace.
    floors = [ast.unparse(n.left) for n in ast.walk(fn)
              if isinstance(n, ast.Compare) and "0.45" in ast.unparse(n)]
    check(floors and all("raw" not in f.lower() for f in floors),
          f"the 0.45 collapse floor is also compared in the calibrated namespace ({floors}) - "
          f"fixing only one of the two comparisons would leave the same defect, halved")

    # Absence must fail closed, matching entry, which refuses to open without a calibration.
    check("MODEL_CALIBRATION_UNAVAILABLE" in body,
          "a missing calibration returns an explicit reason rather than falling through - "
          "entry refuses to OPEN without one, so exiting on an uncalibrated number would hold "
          "capital already at risk to a weaker standard than capital not yet committed")

    print("")
    print(f"PROBABILITY NAMESPACE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
