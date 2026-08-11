"""
The directional dead zone is applied at EVERY site that turns two probabilities into a side.

`model.generate_ensemble_prediction` refuses to commit when `prob_up` and `prob_down` are
within `BTC_DIR_MARGIN` of each other, so that a bare `prob_up > prob_down` cannot turn a
systematic tilt into a directional call. `price_to_beat._bet_lean` then performed exactly
that bare comparison, on precisely the rows the head had just sent to NEUTRAL.

Also pins the measurement that refutes the margin as a REMEDY, so nobody sets it: the tilt
is a whole-distribution shift, and widening a dead zone selects more of the biased side.

Run directly:  python backend/tests/test_directional_tilt_contract.py
"""

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import os
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


def code_of(path: Path, name: str) -> str:
    """Function CODE only - no docstring, no comments - via `ast.unparse`."""
    src = path.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    return "\n".join(ast.unparse(s) for s in stmts)


def test_margin_is_not_bypassed():
    print("\nThe dead zone applies wherever two probabilities become a side")
    from price_to_beat import PriceToBeatTracker

    inside = {"rawDirection": "NEUTRAL", "probUp": 0.305, "probDown": 0.300}
    clear = {"rawDirection": "NEUTRAL", "probUp": 0.400, "probDown": 0.280}

    prev = os.environ.pop("BTC_DIR_MARGIN_5", None)
    prev_g = os.environ.pop("BTC_DIR_MARGIN", None)
    try:
        chk(PriceToBeatTracker._bet_lean(inside, 5) == "UP",
            "at the shipped default of 0.0 nothing changes - this is a consistency fix, "
            "not a behaviour change")

        os.environ["BTC_DIR_MARGIN_5"] = "0.015"
        chk(PriceToBeatTracker._bet_lean(inside, 5) == "NEUTRAL",
            "with a margin set, a 0.005 gap is inside the noise floor and returns NEUTRAL - "
            "which is what this function's own last line already calls 'no probability "
            "signal at all'")
        chk(PriceToBeatTracker._bet_lean(clear, 5) == "UP",
            "a real gap still produces a side")
        chk(PriceToBeatTracker._bet_lean({"rawDirection": "DOWN", "probUp": 0.9,
                                          "probDown": 0.0}, 5) == "DOWN",
            "and a COMMITTED lean is never second-guessed by the margin - the head already "
            "applied it")

        os.environ.pop("BTC_DIR_MARGIN_5")
        os.environ["BTC_DIR_MARGIN"] = "0.015"
        chk(PriceToBeatTracker._bet_lean(inside, 5) == "NEUTRAL",
            "the global knob is honoured when the per-horizon one is absent, matching the "
            "head's own precedence")
    finally:
        os.environ.pop("BTC_DIR_MARGIN_5", None)
        os.environ.pop("BTC_DIR_MARGIN", None)
        if prev is not None:
            os.environ["BTC_DIR_MARGIN_5"] = prev
        if prev_g is not None:
            os.environ["BTC_DIR_MARGIN"] = prev_g

    src = code_of(BACKEND / "price_to_beat.py", "_bet_lean")
    chk("BTC_DIR_MARGIN" in src,
        "the margin is read here, not only in the head - applied at one site and bypassed at "
        "the next is not a margin")

    print("\n     ... and NEUTRAL still means neutral, not a defaulted side")
    chk(PriceToBeatTracker._bet_lean({"rawDirection": "NEUTRAL", "probUp": 0.0,
                                      "probDown": 0.0}, 5) == "NEUTRAL",
        "no probability signal at all is still NEUTRAL")


def test_margin_is_not_a_remedy():
    print("\nThe margin is NOT a remedy for the tilt, and the measurement says so")
    mdl = (BACKEND / "model.py").read_text(encoding="utf-8")

    chk("MEASURE-BEFORE-GATE COMPLETED" in mdl,
        "the knob's own comment demanded a measurement before use, and carries the result")
    chk("mean prob_up 0.3430" in mdl and "mean prob_down 0.2920" in mdl,
        "the tilt is a WHOLE-DISTRIBUTION shift of +5.1pp, not an offset near the boundary")

    # The arithmetic that makes the remedy counterproductive, reproduced so the claim in
    # that comment cannot rot into folklore: under a uniform positive shift, filtering to
    # larger absolute gaps RAISES the UP share.
    import random
    rng = random.Random(11)
    shift = 0.051
    sample = [rng.gauss(shift, 0.12) for _ in range(200_000)]
    shares = []
    for margin in (0.0, 0.015, 0.05, 0.10):
        keep = [x for x in sample if abs(x) >= margin]
        shares.append(sum(1 for x in keep if x > 0) / len(keep))
    print("       simulated UP share by margin: "
          + ", ".join(f"{m}->{s:.3f}" for m, s in zip((0.0, 0.015, 0.05, 0.10), shares)))
    chk(all(b >= a for a, b in zip(shares, shares[1:])),
        "a wider dead zone selects MORE of the biased side under a uniform shift - which is "
        "why setting BTC_DIR_MARGIN would make the skew worse while shrinking the sample")

    chk(float(os.environ.get("BTC_DIR_MARGIN", "0.0") or 0.0) == 0.0,
        "so the shipped default stays 0.0")


def main():
    print("=" * 78)
    print("DIRECTIONAL TILT CONTRACT")
    print("=" * 78)
    test_margin_is_not_bypassed()
    test_margin_is_not_a_remedy()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"DIRECTIONAL TILT CONTRACT: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("DIRECTIONAL TILT CONTRACT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
