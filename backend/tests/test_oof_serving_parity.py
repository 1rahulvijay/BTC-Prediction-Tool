"""P1-5: the OOF stacker must be trained on the seats it is served.

    python backend/tests/test_oof_serving_parity.py

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

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True
SRC_PATH = Path(__file__).resolve().parents[1] / "model.py"


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

    print("5. the deep seat is not quietly downgraded")
    chk("epochs=max(6, int(model.epochs * 0.5))" not in code,
        "the OOF TCN no longer trains at HALF the production epoch budget")
    chk("epochs=model.epochs," in code,
        "it uses the same budget, so the stacker learns how much to trust the model it is "
        "actually served rather than a weaker one")

    print("6. dynamic weights use SKILL, not raw accuracy")
    chk("_acc_map[_canon] = float(max(0.0, _skill))" in code,
        "the weight is log-loss skill against the class prior")
    chk("_raw_acc_map[_canon]" in code,
        "raw accuracy is kept separately for the panels, under its own name")
    chk("_prior_ll = float(" in code,
        "and the prior baseline is computed from the fold's own label distribution")

    # The property, demonstrated: on an imbalanced bucket a pure-abstainer beats a genuine
    # forecaster on accuracy, and loses on skill. That inversion is the whole reason for the
    # change.
    rng = np.random.default_rng(3)
    n = 6000
    y = np.where(rng.uniform(size=n) < 0.70, 1, np.where(rng.uniform(size=n) < 0.5, 0, 2))
    prior = np.maximum(np.bincount(y, minlength=3) / n, 1e-6)
    abstain = np.tile(prior, (n, 1))                        # always predicts the base rates
    # A MODEST nudge toward the truth, not an oracle. argmax still lands on NEUTRAL almost
    # always, so accuracy cannot separate this seat from the abstainer - which is exactly the
    # blind spot. The first version of this fixture put 0.76 on the true class and scored
    # 1.000 accuracy, proving nothing about the metric being replaced.
    informed = np.tile(prior, (n, 1)).astype(np.float64)
    informed[np.arange(n), y] += 0.10
    informed /= informed.sum(axis=1, keepdims=True)

    def acc(p):
        return float(np.mean(np.argmax(p, axis=1) == y))

    def skill(p):
        p = np.clip(p, 1e-6, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        ll = float(-np.mean(np.log(p[np.arange(n), y])))
        pll = float(-np.mean(np.log(prior[y])))
        return 1.0 - ll / pll

    chk(abs(acc(abstain) - acc(informed)) < 0.02,
        f"on a 70% NEUTRAL bucket ACCURACY cannot tell the pure abstainer from a genuinely "
        f"informative seat ({acc(abstain):.3f} vs {acc(informed):.3f}) - both just answer "
        f"NEUTRAL, so the old weight ranked them the same")
    chk(skill(abstain) < 1e-6 < skill(informed),
        f"while on SKILL the abstainer scores ~0 and the informed seat scores "
        f"{skill(informed):.3f} - the weight now goes to the seat with information")

    print("7. class-set mismatch is recorded, not hidden")
    # The INCREMENT, not the variable name. A mutation that deleted the counting while leaving
    # the getattr line intact walked straight past a name-only check.
    chk("_mismatch[_key] = _mismatch.get(_key, 0) + 1" in code,
        "a fold that saw fewer classes than production is actually COUNTED, not merely named")
    chk("if len(fit_classes) < 3:" in code,
        "and the condition that detects it is the class count the fold actually fitted")
    chk("TRAINING_SEMANTICS" in SRC_PATH.read_text(encoding="utf-8"),
        "and the deliberate exclusion of synthetic class-presence rows remains a declared "
        "training-semantics decision rather than an accident")

    print("7a. the fold rebuilds the REGIME-SIMILARITY term, not just recency and class")
    # Production's default mode multiplies recency BY regime similarity. The fold rebuilt
    # recency and class and dropped similarity, so every seat feeding the stacker was fitted
    # under a different objective than the one it serves under - while the comment above the
    # code named `similarity_w` and explained why slicing it is wrong, which read as though
    # the term had been handled.
    import ast as _ast2
    _src = SRC_PATH.read_text(encoding="utf-8")
    _tree2 = _ast2.parse(_src)
    _sim_calls = [n for n in _ast2.walk(_tree2)
                  if isinstance(n, _ast2.Call) and isinstance(n.func, _ast2.Name)
                  and n.func.id == "_regime_similarity_weights"]
    chk(len(_sim_calls) >= 2,
        f"_regime_similarity_weights is called in MORE than one place ({len(_sim_calls)}) - "
        f"production alone is the state where the fold silently omitted it")

    # The fold's call must NOT be anchored to the global split_idx: for an early fold that
    # index lies in its own future, so the weights would rank rows by distance from a point
    # the fold must not know about.
    _fold_calls = [c for c in _sim_calls
                   if not (len(c.args) >= 3 and isinstance(c.args[2], _ast2.Name)
                           and c.args[2].id == "split_idx")]
    chk(_fold_calls,
        "at least one call anchors similarity to something OTHER than the global split_idx - "
        "a fold-local weight referenced to the global split would be a subtler leak than the "
        "missing term it replaced")

    # And it must be given the MODEL feature names. The `feature_names` in scope at the fold
    # is the list of stacker SEAT names; passing it would select no similarity features at all
    # and quietly return all-ones, which looks exactly like a working fix.
    for _c in _fold_calls:
        _second = _c.args[1] if len(_c.args) >= 2 else None
        chk(isinstance(_second, _ast2.Attribute) and _second.attr == "model_feature_names",
            "the fold passes self.model_feature_names, not the stacker's seat-name list - "
            "the wrong list matches no similarity features and returns all ones, which is "
            "indistinguishable from the bug")

    # The weight actually consumed must include the similarity factor.
    _sw = [n for n in _ast2.walk(_tree2)
           if isinstance(n, _ast2.Assign)
           and any(isinstance(tg, _ast2.Name) and tg.id == "sw_tr" for tg in n.targets)]
    chk(_sw, "the fold assigns sw_tr")
    # Follow the chain sw_tr <- _rs <- _sim. Accepting the intermediate name alone is not
    # enough: a mutant that computed _sim and then wrote `_rs = _rec * 1.0` kept the name in
    # the sw_tr expression and survived. The term must be traceable to the similarity call.
    def _names(node):
        return {d.id for d in _ast2.walk(node) if isinstance(d, _ast2.Name)}

    _sw_names = set().union(*(_names(a.value) for a in _sw)) if _sw else set()
    _reaches = "_sim" in _sw_names
    if not _reaches:
        for _mid in _sw_names:
            _defs = [n for n in _ast2.walk(_tree2)
                     if isinstance(n, _ast2.Assign)
                     and any(isinstance(tg, _ast2.Name) and tg.id == _mid
                             for tg in n.targets)]
            if any("_sim" in _names(d.value) for d in _defs):
                _reaches = True
                break
    chk(_reaches,
        "and sw_tr traces back to the similarity array through its intermediates, so the "
        "term reaches the fit rather than being computed and thrown away")

    # PARSED for an actual Raise in the guard's own body. Searching for the message text
    # passed even when `raise ValueError` was swapped for `logger.warning` - the string
    # survives the mutation that removes the safety boundary, which is exactly the kind of
    # check that certifies nothing.
    _guard_raises = False
    for _node in _ast2.walk(_tree2):
        if not isinstance(_node, _ast2.If):
            continue
        _body_src = " ".join(_ast2.dump(s) for s in _node.body)
        if "cannot map stack rows" in _body_src:
            _guard_raises = any(isinstance(s, _ast2.Raise) for s in _ast2.walk(_node))
    chk(_guard_raises,
        "an unmappable stack row RAISES from inside its own guard rather than falling back "
        "to unweighted - a warning is not a safety boundary")

    # Production normalises the recency*similarity product before indexing it. A uniform
    # rescale is a no-op for the tree seats, so dropping it is nearly an equivalent mutation -
    # but not for a regularised seat, where scaling sample_weight changes the effective
    # penalty. It is asserted because the code claims the fold differs from serving ONLY by
    # its fold-local anchor, and that claim should fail if it stops being true.
    _norm = [n for n in _ast2.walk(_tree2)
             if isinstance(n, _ast2.Assign)
             and any(isinstance(tg, _ast2.Name) and tg.id == "_rs" for tg in n.targets)
             and "np.mean" in _ast2.unparse(n.value)]
    chk(_norm,
        "the fold normalises the recency*similarity product the way production does, so the "
        "two differ only by the fold-local anchor the comment claims")

    print("7b. and the count is MEASURED against a preregistered tolerance")
    # Counting was the whole remedy, and nothing ever read the counter. A number no one
    # compares to a threshold cannot fail; it records a defect while permitting it.
    import model as _model
    chk(isinstance(getattr(_model, "OOF_CLASS_SET_TOLERANCE", None), float),
        "a tolerance is DECLARED as a module constant, so it is fixed before a run rather "
        "than chosen once the counts are visible")
    tol = _model.OOF_CLASS_SET_TOLERANCE
    chk(0.0 <= tol < 0.5,
        f"and it is a real bound ({tol}) - a tolerance at or above half the folds would "
        f"admit a seat whose column is structurally zero more often than not")
    chk("_cs_total += 1" in code and "_cs_short += 1" in code,
        "both a numerator and a DENOMINATOR are counted, so a rate exists to compare")
    chk("if _cs_rate > OOF_CLASS_SET_TOLERANCE:" in code,
        "the rate is compared to the tolerance - the comparison itself, not just the name")

    # The remedy must be to DROP the seat. A comparison whose only effect is a log line is
    # the same non-gate as the bare counter it replaced.
    _gate = code[code.index("if _cs_rate > OOF_CLASS_SET_TOLERANCE:"):]
    _gate = _gate[:_gate.index("oof_features.append(preds)")]
    chk("continue" in _gate,
        "and a seat over tolerance is DROPPED from the stacker, not merely logged - the "
        "remedy already used when a fold cannot be calibrated")

    # Parsed, so the drop cannot be moved after the append and left inert.
    import ast as _ast
    _tree = _ast.parse(SRC_PATH.read_text(encoding="utf-8"))
    _found = False
    for _node in _ast.walk(_tree):
        if not isinstance(_node, _ast.If):
            continue
        _cmp = _node.test
        if (isinstance(_cmp, _ast.Compare) and isinstance(_cmp.left, _ast.Name)
                and _cmp.left.id == "_cs_rate"
                and any(isinstance(c, _ast.Name) and c.id == "OOF_CLASS_SET_TOLERANCE"
                        for c in _cmp.comparators)):
            _found = any(isinstance(s, _ast.Continue) for s in _ast.walk(_node))
    chk(_found,
        "asserted by PARSING the comparison's own body for the continue, so the gate cannot "
        "be satisfied by a `continue` that happens to appear somewhere nearby")

    print("\nOOF / SERVING PARITY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
