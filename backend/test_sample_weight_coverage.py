"""P1-4: every directional seat must respect the sample weights, including zero.

    python backend/test_sample_weight_coverage.py

THE DEFECT
    train_model builds one weight vector per regime/horizon:

        sw = recency_w[reg_idx] * class_w[y_train_h] * h_weight[reg_idx]

    carrying recency, class balancing, and a ZERO for every AMBIGUOUS row (a bar that touched
    both barriers, whose first-touch order is unknowable). Six seats passed it. HistGradient-
    Boosting did not:

        base_histgb.fit(X_train_h, y_train_h)

    So one member of the ensemble trained on a different empirical distribution from its
    neighbours - no recency, no class balance, and fitted on exactly the rows every other seat
    was told to ignore - while the OOF stacker combined them all as if they had agreed on what
    the training set was.

    Separately, the class weights themselves were counted over ALL training rows including the
    zero-weight ones. Ambiguity is not uniform across classes (a bar violent enough to touch
    both barriers is not a NEUTRAL-looking bar), so outcomes the model never learns from were
    setting the class frequencies for the ones it does.
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


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


#: Seats that must receive the weights. The LR seat uses its own sw_lr (a differently indexed
#: subset) and the magnitude regressor uses sw_reg - both deliberate, both still weighted.
_UNWEIGHTED_IS_A_BUG = ("base_histgb", "base_xgb", "base_lgb", "cat_model", "rf_model",
                        "dl_model")


def test_zero_weight_rows_do_not_train() -> None:
    """The property the weight vector is supposed to buy: a zero-weight row is not learned.

    Fitting on {good rows + poisoned rows at weight 0} must equal fitting on {good rows}. If
    the weights were dropped, the poisoned labels move the decision surface."""
    print("zero-weight rows contribute nothing to a HistGB fit")
    from sklearn.ensemble import HistGradientBoostingClassifier

    rng = np.random.default_rng(11)
    n = 400
    X_good = rng.normal(size=(n, 4))
    y_good = (X_good[:, 0] > 0).astype(int)          # a clean, learnable rule

    # Poison: the SAME feature region labelled the opposite way.
    X_bad = rng.normal(size=(n, 4))
    y_bad = 1 - (X_bad[:, 0] > 0).astype(int)

    X_all = np.vstack([X_good, X_bad])
    y_all = np.concatenate([y_good, y_bad])
    w_all = np.concatenate([np.ones(n), np.zeros(n)])

    probe = rng.normal(size=(60, 4))

    def fit(X, y, w=None):
        m = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=100, max_depth=5,
                                           random_state=44, min_samples_leaf=5)
        m.fit(X, y, sample_weight=w)
        return m.predict_proba(probe)

    weighted = fit(X_all, y_all, w_all)
    only_good = fit(X_good, y_good)
    unweighted = fit(X_all, y_all)

    def agreement(a, b):
        return float(((a[:, 1] > 0.5) == (b[:, 1] > 0.5)).mean())

    chk(agreement(weighted, only_good) == 1.0,
        "the weighted fit makes the SAME decision as the fit on good rows alone, on every "
        "probe point - the poisoned labels do not move the surface")
    chk(agreement(unweighted, only_good) < 0.7,
        f"while dropping the weights destroys it ({agreement(unweighted, only_good):.0%} "
        f"agreement) - so the check above tests something real")

    # MEASURED, not assumed: zeroing a weight is NOT identical to removing the row. HistGB
    # computes its feature bin edges from every row it is handed, weight or not, so the
    # probabilities differ slightly even though the decisions do not. Verified to be binning
    # rather than a loss contribution: the gap is unchanged at max_bins=255.
    chk(not np.allclose(weighted, only_good, atol=1e-6),
        "and the two are NOT bit-identical - zero weight excludes a row from the loss, not "
        "from the histogram binning, which is a caveat worth pinning rather than assuming away")
    chk(float(np.abs(weighted - only_good).mean()) < 0.02,
        f"the residual is small ({float(np.abs(weighted - only_good).mean()):.4f} mean) and "
        f"far below the {float(np.abs(unweighted - only_good).mean()):.4f} caused by ignoring "
        f"the weights entirely")


def test_every_classifier_receives_sample_weight() -> None:
    """Read train_model's source: no directional seat may be fitted unweighted."""
    print("every directional seat is fitted with sample_weight")
    src = (Path(__file__).resolve().parent / "model.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    unweighted = []
    weighted = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "fit"):
            continue
        target = func.value
        name = target.id if isinstance(target, ast.Name) else None
        if name not in _UNWEIGHTED_IS_A_BUG:
            continue
        has_weight = any(kw.arg == "sample_weight" for kw in node.keywords)
        (weighted if has_weight else unweighted).append((name, node.lineno))

    chk(len(weighted) >= 6,
        f"the scan actually found the fit calls ({len(weighted)} weighted) - a scan that "
        f"matched nothing would pass vacuously")
    chk(not unweighted,
        f"no directional seat is fitted without sample_weight (offenders: {unweighted})")
    chk(any(n == "base_histgb" for n, _ in weighted),
        "HistGradientBoosting specifically - the seat that was missing it")


def test_class_weights_exclude_zero_weight_rows() -> None:
    """Class frequencies must be counted over the rows that actually train."""
    print("class weights are counted over trainable rows only")
    src = (Path(__file__).resolve().parent / "model.py").read_text(encoding="utf-8")
    code = _code_only(src)
    chk("_counted = y_train[h_weight > 0]" in code,
        "the count is taken over rows with non-zero weight")
    chk("np.bincount(_counted, minlength=3)" in code,
        "and the bincount consumes that subset, not the raw y_train")
    chk("np.bincount(y_train, minlength=3)" not in code,
        "the unfiltered count is gone from the code, not merely shadowed")

    # And the arithmetic actually differs, so the change is not cosmetic.
    y_train = np.array([0] * 50 + [1] * 10 + [2] * 40)
    h_weight = np.ones(100)
    h_weight[:40] = 0.0                      # ambiguity concentrated in class 0

    def weights(counted):
        cnt = np.bincount(counted, minlength=3).astype(float)
        inv = cnt.sum() / (3.0 * np.maximum(cnt, 1.0))
        return np.clip(inv / inv.mean(), 0.5, 2.0)

    chk(not np.allclose(weights(y_train), weights(y_train[h_weight > 0])),
        "including the excluded rows produces DIFFERENT class weights - the bug changed the "
        "loss every seat optimised, not just a log line")


def _code_only(src: str) -> str:
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


def main() -> int:
    for test in (test_zero_weight_rows_do_not_train,
                 test_every_classifier_receives_sample_weight,
                 test_class_weights_exclude_zero_weight_rows):
        test()
    print("\nSAMPLE WEIGHT COVERAGE:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
