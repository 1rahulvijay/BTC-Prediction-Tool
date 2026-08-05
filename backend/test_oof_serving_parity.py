"""P1-5: the OOF stacker must be trained on the seats it is served.

    python backend/test_oof_serving_parity.py

FOUR MISMATCHES, all confirmed in source before fixing

 1. CALIBRATION.  `base_est = getattr(model, "estimator", ...)` extracts the UNCALIBRATED inner
    model out of a CalibratedClassifierCV. OOF folds therefore produced raw probabilities while
    serving produces isotonic-calibrated ones - the stacker was trained on one probability
    distribution and served another.

 2. WEIGHTS.  `fold_model.fit(X_tr, y_tr_local)` with no sample_weight, while every production
    seat is fitted with recency, class balancing and ambiguity exclusion baked into `sw`. The
    weights were already returned by recent_classification_slice and discarded into `_`.

 3. PURGE GAP.  `min(required, len(X_stack) // 8)` silently shrank the gap below the label
    overlap in thin buckets - exactly where sample size makes leakage hardest to notice. A
    leakage requirement is not a preference to be traded against sample size.

 4. DOUBLE COUNT.  The TCN is a stacker seat AND was blended again afterwards at a fixed 0.15.
    That blend predates v6, when the TCN was trained but not stacked; after v6 it overrode part
    of what the meta-model had learned, counting the seat twice.

None of these is leakage in the classic sense (except 3). They are covariate shift in the one
component whose entire job is combining the others.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True
SRC_PATH = Path(__file__).resolve().parent / "model.py"


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    doc: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return chr(10).join(
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in doc and not ln.strip().startswith("#"))


def main() -> int:
    code = code_only(SRC_PATH)

    print("the mismatches themselves: uncalibrated vs calibrated probabilities differ")
    # Not asserted - demonstrated. If calibration were a no-op the parity fix would be pointless.
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier

    rng = np.random.default_rng(5)
    n = 600
    X = rng.normal(size=(n, 4))
    y = (X[:, 0] + rng.normal(0, 0.8, n) > 0).astype(int)
    raw = HistGradientBoostingClassifier(max_iter=80, random_state=1).fit(X, y)
    cal = CalibratedClassifierCV(
        HistGradientBoostingClassifier(max_iter=80, random_state=1),
        method="isotonic", cv=3).fit(X, y)
    probe = rng.normal(size=(200, 4))
    gap = float(np.abs(raw.predict_proba(probe)[:, 1] - cal.predict_proba(probe)[:, 1]).mean())
    chk(gap > 0.01,
        f"a calibrated seat and its raw inner estimator disagree by {gap:.3f} on average - "
        f"training the stacker on one and serving the other is a real distribution shift")

    print("and unweighted vs weighted fits differ")
    w = np.ones(n)
    w[: n // 2] = 0.0                     # half the rows excluded, as ambiguity exclusion does
    m_unw = HistGradientBoostingClassifier(max_iter=80, random_state=1).fit(X, y)
    m_wgt = HistGradientBoostingClassifier(max_iter=80, random_state=1).fit(X, y, sample_weight=w)
    wgap = float(np.abs(m_unw.predict_proba(probe)[:, 1]
                        - m_wgt.predict_proba(probe)[:, 1]).mean())
    chk(wgap > 0.01,
        f"dropping the weights moves the fitted probabilities by {wgap:.3f} - the OOF folds "
        f"were optimising a different objective from the seats they represent")

    print("1. calibration parity")
    chk("seat_is_calibrated = isinstance(model, CalibratedClassifierCV)" in code,
        "the fold detects whether the SERVED seat is calibrated")
    # STRUCTURAL, not substring. Asserting that the ARGUMENTS appear somewhere passes even if
    # they are handed to something other than the calibrator - a mutation that swapped the
    # constructor for a pass-through lambda survived the string check.
    wraps_fold = False
    for node in ast.walk(ast.parse(SRC_PATH.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id == "CalibratedClassifierCV"):
            continue
        for kw in node.keywords:
            if kw.arg == "estimator" and isinstance(kw.value, ast.Name) \
                    and kw.value.id == "fold_model":
                wraps_fold = True
    chk(wraps_fold,
        "and the fold is wrapped by an actual CalibratedClassifierCV(estimator=fold_model) "
        "call, verified through the syntax tree rather than by matching argument text")
    chk("method=_cal_method," in code,
        "using the SERVED seat's calibration method, not a hardcoded one")
    chk("refusing an uncalibrated substitute" in SRC_PATH.read_text(encoding="utf-8"),
        "a fold too small to calibrate DROPS the seat rather than substituting a raw one - a "
        "quiet fallback would reinstate the exact mismatch being fixed")

    print("2. sample weights")
    chk("X_stack, y_stack, sw_stack = recent_classification_slice(" in code,
        "the weights are captured instead of discarded into _")
    # NOT sliced. The global weights reference split_idx - the end of the whole training slice
    # - through both recency and regime similarity, so for an early fold that point lies in its
    # own future. Slicing them would have replaced an unweighted fit with a future-referenced
    # one: a subtler mismatch than the original, and harder to see.
    chk("sw_tr = np.asarray(sw_stack, dtype=np.float64)[tr_idx]" not in code,
        "the global weight vector is NOT sliced into folds")
    chk("_fold_end = _pos.max() if len(_pos) else 0.0" in code
        and "_rec = 0.5 ** ((_fold_end - _pos) / _half_life)" in code,
        "recency is rebuilt against the FOLD's own last training row")
    chk("y_tr_local[_keep > 0]" in code,
        "class balance is counted over the fold's own non-ambiguous rows")
    chk("_keep = (_sw_global[tr_idx] > 0).astype(np.float64)" in code,
        "while ambiguity exclusion IS carried across - it is row-local, so no future leaks "
        "through it")
    chk("weighted rows after ambiguity exclusion" in SRC_PATH.read_text(encoding="utf-8")
        and "if not np.any(sw_tr > 0):" in code,
        "and a fold left with nothing to train on refuses rather than fitting on zeros")
    chk("fold_model.fit(X_tr, y_tr_local, sample_weight=sw_tr)" in code,
        "the fold is fitted WITH them")
    chk("does not accept " in SRC_PATH.read_text(encoding="utf-8"),
        "a wrapper that cannot take weights logs the mismatch rather than dropping them "
        "silently")

    print("3. purge gap is a requirement, not a preference")
    chk("purge_gap = min(" not in code,
        "the gap is no longer min(required, whatever the data supports)")
    chk("required_gap = max(LOOKBACK + int(h), 1)" in code,
        "the required gap is stated explicitly")
    chk("Refusing to shrink the gap" in SRC_PATH.read_text(encoding="utf-8"),
        "and too little data REFUSES rather than under-purging - the failure mode is loud")
    chk("purge_gap = required_gap" in code, "the gap used IS the required one")

    print("4. the TCN is not counted twice")
    chk('_dl_in_stacker = "dl" in (stacker_info.get("features") or ())' in code,
        "serving checks whether the TCN is already a stacker seat")
    chk("if HAS_TORCH and not _dl_in_stacker" in code,
        "and skips the post-hoc 0.15 blend when it is")
    chk('horizon in (store.get("dl") or {})' in code,
        "the lookup is also safe when no dl seat exists at all")

    # The cap must SURVIVE for a bundle whose stacker never received the TCN.
    print("   while the cap still applies when the TCN is NOT stacked")
    chk("0.15 if HAS_TORCH and not _dl_in_stacker" in code,
        "the 0.15 weight is retained for that case, not deleted outright")

    print("\nOOF / SERVING PARITY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
