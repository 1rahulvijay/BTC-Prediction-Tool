"""P1-6: an uncertainty band must come from rows the model never saw.

    python backend/tests/test_conformal_source_honesty.py

THE DEFECT
    When a regime's held-out slice held fewer than 200 rows, the magnitude head fell back to:

        residuals = mag_target - reg_mag.predict(X_reg)
        _conf_src = "in-sample-fallback"

    and stored those quantiles as the conformal band. Residuals on the model's own training
    rows measure FIT, not coverage, and they are narrowest exactly where the held-out cut was
    too thin to use - so the system produced its most confident intervals in its least
    trustworthy regimes. The caveat existed only as a training log line: `_conf_src` was never
    written to the artifact, so nothing downstream could have honoured it.

    The ladder is now: this regime's untouched rows -> the GLOBAL untouched rows -> no
    conformal band at all. A missing band is a state the caller already handles; a fabricated
    one is not.
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
SRC = (Path(__file__).resolve().parents[1] / "model.py").read_text(encoding="utf-8")


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _code_only(src: str) -> str:
    doc_lines: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc_lines.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return chr(10).join(
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in doc_lines and not ln.strip().startswith("#"))


def main() -> int:
    code = _code_only(SRC)

    print("the in-sample fallback is gone from the CODE")
    chk("residuals = mag_target - reg_mag.predict(X_reg)" not in code,
        "training residuals are no longer computed as a conformal band")
    chk('"in-sample-fallback"' not in code,
        "and the label that described them is gone with it")

    print("the replacement ladder is present")
    chk('_conf_src = "held-out-regime"' in code, "1. the regime's own untouched rows")
    chk('_conf_src = "held-out-global"' in code, "2. the GLOBAL untouched rows")
    chk("if residuals is None:" in code, "3. otherwise no band is recorded at all")

    print("the source is written to the ARTIFACT, not just logged")
    chk('"source": _conf_src,' in code,
        "the stored dict carries where its residuals came from")
    chk('"n": int(len(residuals)),' in code,
        "and how many rows produced them, so a thin band is visible as thin")

    print("and it reaches the caller")
    chk('"expectedMoveRangeSource": move_range_source,' in code,
        "the served prediction reports the provenance of its range")
    chk('move_range_source = "regime_prior_only"' in code,
        "an empirical prior standing in for a conformal band says so - it is an honest claim "
        "about realised moves, but it is NOT model coverage")
    chk('str(resids.get("source") or "unknown")' in code,
        "a bundle predating the field reports 'unknown' rather than claiming 'held-out'")

    print("absence is safe for the consumer")
    chk("move_range = None" in code, "move_range defaults to None")
    guarded = code.count("if move_range:")
    chk(guarded >= 2,
        f"and every use is guarded ({guarded} guards) - removing a band cannot crash serving")

    print("the property itself: in-sample residuals ARE narrower")
    # Not an assumption. Fit a flexible model, compare its residual spread on the rows it
    # trained on against untouched rows drawn from the same process.
    from sklearn.ensemble import HistGradientBoostingRegressor

    rng = np.random.default_rng(7)
    n = 300
    X_tr = rng.normal(size=(n, 3))
    y_tr = X_tr[:, 0] + rng.normal(scale=1.0, size=n)
    X_ho = rng.normal(size=(n, 3))
    y_ho = X_ho[:, 0] + rng.normal(scale=1.0, size=n)

    m = HistGradientBoostingRegressor(max_iter=300, max_leaf_nodes=31, learning_rate=0.1,
                                      random_state=46)
    m.fit(X_tr, y_tr)
    in_sample = y_tr - m.predict(X_tr)
    held_out = y_ho - m.predict(X_ho)

    def iqr(r):
        return float(np.quantile(r, 0.75) - np.quantile(r, 0.25))

    chk(iqr(in_sample) < iqr(held_out),
        f"in-sample IQR {iqr(in_sample):.3f} < held-out IQR {iqr(held_out):.3f} - the fallback "
        f"was systematically optimistic, not merely different")

    print("\nCONFORMAL SOURCE HONESTY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
