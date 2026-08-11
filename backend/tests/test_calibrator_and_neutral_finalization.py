"""Two defects where a component fed on its own output, and one where NEUTRAL carried orders.

    python backend/tests/test_calibrator_and_neutral_finalization.py

Scan-5 claims 8, 9, 24 and 25, verified in source before being fixed.

1. THE LIVE CALIBRATOR CALIBRATED ITS OWN PREVIOUS OUTPUT  (5.8)
   `refit_confidence_calibrators` says it maps "raw confidence" and fitted on
   `prediction["confidence"]` - a value that had already been through regime calibration AND
   this very isotonic map. Every refit therefore learned from its own last answer, and the
   result was fed back in.

2. AND IT WAS SELECTION-BIASED  (5.9)
   It trained only on rows whose FINAL direction stayed UP/DOWN - rows that survived the server
   gates - then applied the map BEFORE those gates to every future prediction. That estimates
   P(correct | survived the gates, score) and uses it as P(correct | score).

   The two interact: fixing the score without fixing the population would have produced a
   correctly-scaled map of the wrong conditional.

3. A NATURALLY NEUTRAL PREDICTION CARRIED TRADING INSTRUCTIONS  (5.24, 5.25)
   `positionSize` is assigned from confidence alone and the stop/take-profit geometry is built
   with an `if UP else SHORT-shaped` branch, so NEUTRAL could carry a positive size and
   short-looking stop/target values. The server's atomic `_neutralize_prediction()` only fires
   when an existing DIRECTIONAL call is rejected - it never saw a row that was NEUTRAL from the
   start, nor the quantile skip, which rewrote direction and reasons and left the geometry
   describing the old side.
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
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    doc: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return chr(10).join(ln for i, ln in enumerate(src.splitlines(), start=1)
                        if i not in doc and not ln.strip().startswith("#"))


def _row(*, raw_dir, actual, conf_raw, conf, final_dir, hit, has_raw=True):
    return {
        "raw_direction": raw_dir, "actual_direction": actual,
        "confidence_raw": conf_raw, "confidence_raw_available": has_raw,
        "confidence": conf, "direction": final_dir, "hit": hit,
    }


def main() -> int:
    print("1+2. the calibrator fits the RAW score on EVERY scoreable lean")
    from prediction_verifier import PredictionVerifier

    v = PredictionVerifier()

    # A population where the two defects point in OPPOSITE directions, so a fix to one alone
    # cannot pass: survivors are mostly right, abstained rows mostly wrong. Filtering on the
    # final direction therefore inflates the fitted hit rate.
    rows = []
    for i in range(60):
        rows.append(_row(raw_dir="UP", actual="UP", conf_raw=0.80, conf=0.62,
                         final_dir="UP", hit=True))
    for i in range(60):
        # Same raw score, but the server neutralised these - and they were WRONG.
        rows.append(_row(raw_dir="UP", actual="DOWN", conf_raw=0.80, conf=0.62,
                         final_dir="NEUTRAL", hit=False))
    for i in range(40):
        rows.append(_row(raw_dir="DOWN", actual="DOWN", conf_raw=0.30, conf=0.41,
                         final_dir="DOWN", hit=True))
    for i in range(40):
        rows.append(_row(raw_dir="DOWN", actual="UP", conf_raw=0.30, conf=0.41,
                         final_dir="NEUTRAL", hit=False))
    for r in rows:
        v.verified_by_horizon[5].append(r)

    v.refit_confidence_calibrators(min_samples=40)
    cal = v.get_confidence_calibrators().get(5)
    chk(cal is not None, "a calibrator is produced from these rows")
    if cal is not None:
        p_high = float(cal["iso"].predict([0.80])[0])
        chk(abs(p_high - 0.5) < 0.12,
            f"at raw score 0.80 the fitted P(correct) is {p_high:.3f} - close to the TRUE 0.50 "
            f"of that population. Filtering on the final direction would have fitted ~1.0, "
            f"because every surviving 0.80 row was a winner")
        chk(cal["n"] == 200,
            f"all {cal['n']} scoreable raw leans are used, not just the 100 that survived the "
            f"gates")

    print("   a row with no raw score is skipped rather than substituted")
    v2 = PredictionVerifier()
    for i in range(80):
        v2.verified_by_horizon[5].append(
            _row(raw_dir="UP", actual="UP", conf_raw=0.0, conf=0.7,
                 final_dir="UP", hit=True, has_raw=False))
    v2.refit_confidence_calibrators(min_samples=40)
    chk(v2.get_confidence_calibrators().get(5) is None,
        "legacy rows without confidence_raw produce NO calibrator - raw score cannot be "
        "recovered retroactively, and unavailable is the honest state")

    print("   and the verifier records the raw score in the first place")
    src = code_only(BACKEND / "prediction_verifier.py")
    chk('"confidence_raw"' in src and "confidenceRaw" in src,
        "record_prediction stores confidence_raw from the model's confidenceRaw")
    chk('v.get("confidence_raw"' in src or "confidence_raw\"" in src,
        "and the refit reads it")
    # SCOPED TO THE FUNCTION UNDER TEST. A file-wide search also hits the accuracy reporters at
    # lines ~911 and ~969, which filter on the final direction CORRECTLY - they are measuring
    # what the server actually did. Asserting file-wide would fail for the right code.
    _tree = ast.parse((BACKEND / "prediction_verifier.py").read_text(encoding="utf-8"))
    _refit = next(n for n in ast.walk(_tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "refit_confidence_calibrators")
    # DOCSTRING STRIPPED FIRST. ast.unparse() re-emits it, and this function's docstring
    # explains the very defect - so it contains "direction" and "confidence" and would match a
    # naive scan of the fix's own documentation. That trap has now fired four times in this
    # repository; it is why every source check here removes docstrings before matching.
    _body = list(_refit.body)
    if _body and isinstance(_body[0], ast.Expr) and isinstance(_body[0].value, ast.Constant) \
            and isinstance(_body[0].value.value, str):
        _body = _body[1:]
    _refit_src = chr(10).join(ast.unparse(stmt) for stmt in _body)
    # Blank the two legitimate names so any REMAINING `direction` is the bare final-direction
    # lookup this fix removed.
    _stripped = _refit_src.replace("raw_direction", "").replace("actual_direction", "")
    chk("raw_direction" in _refit_src and "direction" not in _stripped,
        "inside refit_confidence_calibrators the selection is on raw_direction, never on the "
        "FINAL direction the server gates decide")
    # The ACCESS, not the word. A word search matched the log line this fix added, which
    # legitimately says "confidence calibrator UNAVAILABLE" - the fix's own message failing
    # the fix's own test.
    _reads = {ast.unparse(n) for n in ast.walk(_refit) if isinstance(n, ast.Call)}
    chk(any("confidence_raw" in r and ".get(" in r for r in _reads),
        "the refit READS confidence_raw")
    chk(not any(r.endswith("get('confidence', 0.0)") or r.endswith("get('confidence')")
                for r in _reads),
        "and never reads the calibrated `confidence` - the value this map itself produced on "
        "the previous cycle")

    print("3. the apply end matches the fit end")
    model_src = code_only(BACKEND / "model.py")
    chk('cc["iso"].predict([conf_raw])' in model_src,
        "the model predicts from conf_raw - the same quantity the map is fitted on. Predicting "
        "from post-regime-calibration `conf` would reintroduce the recursion at the apply end")
    chk('cc["iso"].predict([conf])' not in model_src,
        "and no longer from the already-calibrated value")

    print("4. a finalized NEUTRAL carries no executable instruction")
    from model import _normalize_neutral

    n = _normalize_neutral({"direction": "NEUTRAL", "positionSize": 24,
                            "stopLoss": 63200.0, "takeProfit": 64500.0, "actionable": True})
    chk(n["positionSize"] == 0 and n["stopLoss"] is None and n["takeProfit"] is None
        and n["actionable"] is False,
        "size, stop and target are cleared and it is not actionable")
    chk(n["model_positionSize"] == 24 and n["model_stopLoss"] == 63200.0,
        "the model's own recommendation survives under model_* names - diagnostics that cannot "
        "be mistaken for an order")
    d = _normalize_neutral({"direction": "UP", "positionSize": 24, "stopLoss": 63200.0})
    chk(d["positionSize"] == 24 and d["stopLoss"] == 63200.0,
        "a DIRECTIONAL prediction is untouched")
    chk(_normalize_neutral(dict(n))["positionSize"] == 0,
        "and it is idempotent, so a second pass cannot corrupt the diagnostics")
    chk("return _normalize_neutral(res)" in model_src,
        "generate_ensemble_prediction returns through it, so BOTH a naturally-neutral row and "
        "the quantile skip are normalized - the server's _neutralize_prediction only ever saw "
        "actively-rejected directional calls")

    print("\nCALIBRATOR AND NEUTRAL FINALIZATION:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
