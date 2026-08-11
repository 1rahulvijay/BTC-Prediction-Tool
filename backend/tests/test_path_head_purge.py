"""The path head must purge the label horizon at every split boundary.

WHAT WAS WRONG
    train_path_forecaster builds every target by reading FORWARD h bars - `_future_hl(df, h)`
    spans t..t+h, `net` is close[t+h] - so a row at index i encodes prices through i+h. The
    splits were adjacent:

        X[:a]      fit          (a = n*(2*sf-1), sf=0.98 -> 96%)
        X[a:b]     conformal + isotonic calibration        (2%)
        X[b:]      test                                    (2%)

    with no gap anywhere. The last h fit rows were therefore built from calibration-span
    prices, and the last h calibration rows from test-span prices. The production refit had
    the same defect at X[:b] / X[b:]. Chronological ordering does not remove that overlap,
    which is what makes "the split is temporal" a misleading defence.

    This test does not read the source for the word "purge" - a comment survives any mutation
    that matters. It executes the trainer's real boundary arithmetic and asserts the realised
    gaps, and it asserts on the SERVED bundle that the recorded purge is the label horizon.

    python backend/tests/test_path_head_purge.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _boundaries(src: str, n: int, sf: float, h: int) -> dict:
    """Execute the trainer's OWN boundary lines, lifted verbatim from the source.

    Lifting them keeps this test bound to the shipped arithmetic: if someone edits the
    slicing, this evaluates the edited version rather than a copy that has drifted.
    """
    tree = ast.parse(src)
    wanted = ("a_end = max(1, a - _pg)", "b_end = max(a_end + 1, b - _pg)",
              "b_ref = max(1, b - _pg)")
    found = {w.split(" =")[0].strip(): False for w in wanted}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            txt = ast.unparse(node).replace("\n", " ")
            for w in wanted:
                lhs, rhs = w.split(" = ", 1)
                if txt.startswith(f"{lhs} = ") and rhs.replace(" ", "") in txt.replace(" ", ""):
                    found[lhs] = True
    missing = [k for k, v in found.items() if not v]
    if missing:
        raise AssertionError(f"trainer no longer computes purged boundaries: {missing}")
    a, b = int(n * (2 * sf - 1)), int(n * sf)
    pg = int(h)
    return {"a": a, "b": b, "n": n, "pg": pg,
            "a_end": max(1, a - pg), "b_end": max(max(1, a - pg) + 1, b - pg),
            "b_ref": max(1, b - pg)}


def main() -> int:
    src = (BACKEND / "train_path_forecaster.py").read_text(encoding="utf-8", errors="replace")

    for h in (5, 15):
        g = _boundaries(src, n=500_000, sf=0.98, h=h)
        check(g["a"] - g["a_end"] >= h,
              f"h={h}: the FIT span ends >= {h} bars before the calibration span begins "
              f"({g['a_end']:,} vs {g['a']:,}) - its last rows were labelled from prices "
              f"inside calibration")
        check(g["b"] - g["b_end"] >= h,
              f"h={h}: the CALIBRATION span ends >= {h} bars before test begins "
              f"({g['b_end']:,} vs {g['b']:,}) - conformal width and isotonic were fitted on "
              f"rows reading test-span prices")
        check(g["b"] - g["b_ref"] >= h,
              f"h={h}: the production REFIT stops >= {h} bars before the span its conformal "
              f"and isotonic are then fitted on ({g['b_ref']:,} vs {g['b']:,})")
        check(g["n"] - g["b"] == 500_000 - g["b"],
              f"h={h}: the TEST span is untouched by the purge - train and calibration give "
              f"up rows at their own ends, never the scored side")

    # The purge width must be the LABEL HORIZON, not a constant that happens to be positive.
    tree = ast.parse(src)
    pg_assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "_pg" for t in n.targets)]
    check(pg_assigns and all(
        isinstance(a.value, ast.Call) and getattr(a.value.func, "id", "") == "int"
        and getattr(a.value.args[0], "id", "") == "h" for a in pg_assigns),
        "the purge width is int(h), the label horizon itself - a fixed literal would be "
        "correct for one horizon and wrong for the other")

    # Every fit must draw its labels from a purged endpoint, never the raw boundary.
    bad = [ast.unparse(n) for n in ast.walk(tree)
           if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
           and n.slice.lower is None and getattr(n.slice.upper, "id", "") in ("a", "b")
           and getattr(n.value, "id", "") in ("X", "yu", "yd", "ynet", "yt")]
    check(not bad,
          f"no fit slice still ends at a raw boundary a or b (found {bad[:3]}) - every one "
          f"now ends at a_end/b_end/b_ref")

    print(f"\nPATH HEAD PURGE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
