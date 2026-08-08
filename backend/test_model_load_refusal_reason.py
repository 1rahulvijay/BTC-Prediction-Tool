"""
When the main ensemble will not load, the app says WHY.

Readiness reported `main_ensemble: UNAVAILABLE` and nothing else. That one word covers a
missing identity manifest (regenerate it), an incompatible bundle (retrain it), a missing
dependency (install it) and a feature-schema mismatch (retrain it) - four different actions.
`load_models` computed the exact reason at every refusal and threw it away, and one refusal
path returned False with no log at all, so a serving instance could be dead with nothing in
the record explaining it.

This matters here and now: the shipped bundle IS refused, and the app has served no
prediction since 2026-07-04.

Run directly:  python backend/test_model_load_refusal_reason.py
"""

import ast
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
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


def main():
    print("=" * 78)
    print("MODEL LOAD REFUSAL REASON")
    print("=" * 78)

    from model import MultiModelEnsemble

    print("\nthe reason exists before anything is attempted")
    fresh = MultiModelEnsemble(horizons=[5, 15])
    chk(fresh.load_refusal == "not_attempted",
        "a fresh instance says so rather than reporting None, which a caller would read as "
        "'loaded fine'")

    print("\nan absent bundle directory is named, not silent")
    tmp = Path(tempfile.mkdtemp(prefix="load_refusal_")) / "definitely_absent"
    try:
        m = MultiModelEnsemble(horizons=[5, 15], model_dir=str(tmp))
        ok = m.load_models()
        chk(ok is False and (m.load_refusal or "").startswith("model_dir_absent:"),
            f"-> {m.load_refusal!r}. This path returned False with NO log at all, so a "
            f"misconfigured directory looked identical to an untrained model")
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    print("\na bundle without an identity manifest is named")
    tmp2 = Path(tempfile.mkdtemp(prefix="load_refusal2_"))
    try:
        (tmp2 / "placeholder.pkl").write_bytes(b"not a real artifact")
        m2 = MultiModelEnsemble(horizons=[5, 15], model_dir=str(tmp2))
        ok2 = m2.load_models()
        chk(ok2 is False and "no_identity_manifest" in (m2.load_refusal or ""),
            f"-> {m2.load_refusal!r}, which points at regenerating provenance rather than "
            f"at retraining")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\nevery refusal path records one")
    src = code_of(BACKEND / "model.py", "load_models")
    returns_false = src.count("return False")
    assignments = src.count("self.load_refusal =")
    chk(assignments >= returns_false,
        f"{assignments} reasons recorded against {returns_false} refusal paths - a path that "
        f"refuses without recording is the silent failure this exists to remove")
    chk("self.load_refusal = None" in src,
        "and the reason is CLEARED at entry, so a reload never reports the previous "
        "attempt's cause")

    print("\nreadiness carries it")
    srv = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk('"main_ensemble_refusal"' in srv,
        "the readiness endpoint reports the cause beside the state")
    chk('None if getattr(model, "is_trained", False)' in srv,
        "and reports None once the model IS trained, so a stale reason cannot outlive the "
        "condition it described")

    print("\n     ... and the shipped bundle is genuinely refused right now")
    live = MultiModelEnsemble(horizons=[5, 15])
    live_ok = live.load_models()
    print(f"       load_models() -> {live_ok}   refusal -> {live.load_refusal!r}")
    chk(live_ok is False or live.load_refusal is None,
        "either it loads and has no refusal, or it refuses and names one - never refused "
        "and silent")

    print("\n" + "=" * 78)
    if FAILURES:
        print(f"MODEL LOAD REFUSAL REASON: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("MODEL LOAD REFUSAL REASON: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
