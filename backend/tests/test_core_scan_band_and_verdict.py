"""
Core-app scan 2026-08-08: two defects where a value was replaced or a label decided.

BAND     Every consumer resolved the grading barrier as
         `float(pred.get("neutralBand", 0.0008) or 0.0008)`. A declared band of 0.0 is
         falsy, and 0.0 is REACHABLE - `BTC_LABEL_COST_FLOOR=0` makes
         `causal_neutral_band` return it. Training would label at 0.0 while the verifier
         graded at 8bps: the exact train/serve mismatch `causal_neutral_band` exists to
         remove, reintroduced by an `or`.

VERDICT  `decision_gate` documents WEAK_LEAN as "a committed side but with caveats" and
         then let ANY reason block TRADE - including `grade_unproven`, which nothing in
         production can clear. `trade_verdict` could never be TRADE, so `model_consensus`,
         whose entry condition is exactly that, could never enter.

Run directly:  python backend/tests/test_core_scan_band_and_verdict.py
"""

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
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


def function_body(path: Path, name: str) -> str:
    """Source of a function WITHOUT its docstring, extracted by node.

    `ast.get_docstring` returns the cleaned text, so subtracting it from raw source removes
    nothing and the assertion then matches the fix's own description of the removed code.
    """
    src = path.read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)
                            and isinstance(fn.body[0].value.value, str)) else fn.body
    return "\n".join(ast.get_source_segment(src, s) or "" for s in stmts)


def test_neutral_band():
    print("\nBAND a declared zero is a WIDTH, not an absence")
    import target_contract as tc

    chk(tc.resolve_neutral_band(0.0) == 0.0,
        "0.0 survives - it is what a zero-cost configuration declares, and the old `or` "
        "silently widened it to 8bps")
    chk(tc.resolve_neutral_band(0.0029) == 0.0029,
        "a measured band is passed through unchanged")
    chk(tc.resolve_neutral_band(None) == tc.DEFAULT_NEUTRAL_BAND,
        "absent falls back, which is the only case the fallback was ever for")
    chk(tc.resolve_neutral_band("") == tc.DEFAULT_NEUTRAL_BAND
        and tc.resolve_neutral_band("x") == tc.DEFAULT_NEUTRAL_BAND,
        "and so do non-numeric values")
    chk(tc.resolve_neutral_band(-1.0) == tc.DEFAULT_NEUTRAL_BAND
        and tc.resolve_neutral_band(float("nan")) == tc.DEFAULT_NEUTRAL_BAND,
        "negative and NaN are not widths either - a barrier must be a non-negative distance")

    print("\n     ... and the reachable zero is real, not hypothetical")
    import os
    from model import MultiModelEnsemble
    prev = os.environ.get("BTC_LABEL_COST_FLOOR")
    os.environ["BTC_LABEL_COST_FLOOR"] = "0"
    try:
        band = MultiModelEnsemble.__new__(MultiModelEnsemble).causal_neutral_band([])
        chk(band == 0.0,
            f"BTC_LABEL_COST_FLOOR=0 makes causal_neutral_band return {band} - the same env "
            f"var sets the TRAINING label floor, so the two would have disagreed")
        chk(tc.resolve_neutral_band(band) == 0.0,
            "and the verifier now records that band rather than 0.0008")
    finally:
        if prev is None:
            os.environ.pop("BTC_LABEL_COST_FLOOR", None)
        else:
            os.environ["BTC_LABEL_COST_FLOOR"] = prev

    print("\n     ... at every site that grades")
    for name, fname in (("prediction_verifier.py", None), ("server.py", None),
                        ("model_verifier.py", None), ("historical_replay.py", None)):
        src = (BACKEND / name).read_text(encoding="utf-8")
        chk("resolve_neutral_band" in src, f"{name} resolves the band through one rule")
        chk(', 0.0008) or 0.0008' not in src,
            f"{name} no longer replaces a declared zero")


def test_verdict_split():
    print("\nVERDICT a caveat annotates the verdict; it does not decide it")
    from decision_gate import compute_no_trade_reasons, VERDICT_CAVEATS

    clean = compute_no_trade_reasons({"direction": "UP", "rawDirection": "UP",
                                      "actionable": True, "confluence": {"grade": "A"}})
    chk(clean["trade_verdict"] == "TRADE",
        "a committed, actionable lean whose ONLY reason is an unproven grade is a TRADE - "
        "`grade_validated` has no production writer and `_confluence` returns A/B/C "
        "unconditionally, so this was previously unreachable for EVERY prediction")
    chk(clean["no_trade_reasons"] == ["grade_unproven"],
        "the reason is still REPORTED - removing it was proposed once and refused, and this "
        "does not remove it")
    chk(clean["verdict_blocked_by"] == [] and clean["verdict_caveats"] == ["grade_unproven"],
        "and the row states which reasons decided and which merely annotated")

    blocked = compute_no_trade_reasons({"direction": "UP", "rawDirection": "NEUTRAL",
                                        "actionable": True, "confluence": {"grade": "A"}})
    chk(blocked["trade_verdict"] == "WEAK_LEAN"
        and blocked["verdict_blocked_by"] == ["fallback_lean_only"],
        "a caveat does not rescue a real blocker - `fallback_lean_only` is measured "
        "~coin-flip and its own comment calls it a real skip")

    for code in ("stale_feed", "model_confusion", "negative_expectancy", "meta_reject",
                 "low_confidence", "poor_regime", "wide_target_range"):
        p = compute_no_trade_reasons({"direction": "UP", "rawDirection": "UP",
                                      "actionable": True, "neutralReasonCode": code})
        if p["trade_verdict"] == "TRADE":
            chk(False, f"hard block {code} no longer blocks")
            break
    else:
        chk(True, "every hard block still forces WEAK_LEAN or NO_TRADE")

    chk(VERDICT_CAVEATS == frozenset({"grade_unproven"}),
        "exactly one reason is a caveat, and it is the one that cannot be cleared")

    class _Hostile(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    # NON-EMPTY on purpose. An empty dict subclass is FALSY, so `p.get("setupQuality") or
    # p.get("confluence") or {}` skips straight past it, `.get` is never called, and the
    # test passes while proving nothing. That exact fixture error already shipped once.
    hostile = _Hostile({"grade": "A"})
    assert bool(hostile), "the fixture must be truthy or the `or` chain never reaches it"
    err = compute_no_trade_reasons({"direction": "UP", "rawDirection": "UP",
                                    "actionable": True, "confluence": hostile})
    chk(err["trade_verdict"] != "TRADE" and "decision_gate_error" in err["verdict_blocked_by"],
        "and a gate that failed mid-analysis BLOCKS rather than annotates - an incomplete "
        "reason list must never be read as a clean one")

    print("\n     ... and the label that claimed not to gate no longer claims it")
    srv = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk("NOT a live gate; no bet/abstain/champion" not in srv,
        "_confluence documented itself as never read by a bet/abstain decision while being "
        "the sole thing preventing every trade")

    print("\n     ... and nothing starts trading as a result")
    import target_contract as tc
    try:
        tc.assert_admissible(tc.BINANCE_DIRECTIONAL_EV, tc.TRAINING_CONTRACT)
        opened = True
    except Exception:
        opened = False
    chk(not opened,
        "model_consensus still refuses at the contract gate, which is the gate that SHOULD "
        "hold - this change reopens a gate closed by accident, not the one closed on purpose")


def main():
    print("=" * 78)
    print("CORE SCAN: GRADING BAND AND VERDICT BLOCKERS")
    print("=" * 78)
    test_neutral_band()
    test_verdict_split()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"CORE SCAN: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("CORE SCAN: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
