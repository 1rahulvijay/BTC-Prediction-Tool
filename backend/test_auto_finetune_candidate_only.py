"""The nightly refit must produce a CANDIDATE, never replace a serving artifact.

Before this test, auto_finetune.py rewrote five serving .pkl files in place and the live app
hot-reloaded them within ~30 seconds. There was no challenger gate and no record of the swap,
so a position could open under one artifact and be managed under another - both logged under a
single logical name. Nothing in the repository would show it had happened.

So the assertion here is on BYTES, not on intent: run the guard against a directory where a
trainer has deliberately overwritten serving artifacts, and require that every serving digest
is identical afterwards.

    python backend/test_auto_finetune_candidate_only.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def _code_only(source: str) -> str:
    """Source with docstrings stripped.

    A substring search over raw source produced a false PASS three times in this work by
    matching a name inside the very comment documenting the retired behaviour."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


def main() -> int:
    import auto_finetune as af

    # ---- every trainer in REFIT must honour the redirect ------------------------------
    # train_fade_model.py hardcoded the serving path, so a redirected run still replaced
    # fade_model.pkl underneath the live app. Checked per-file so a trainer added later
    # cannot quietly reintroduce it.
    for _label, script, _extra, _sd in af.REFIT:
        path = BACKEND / script
        if not path.is_file():
            continue
        code = _code_only(path.read_text(encoding="utf-8"))
        check("BTC_MODEL_OUTPUT_DIR" in code,
              f"{script} honours BTC_MODEL_OUTPUT_DIR, so the redirect reaches it")

    check(len(af.REFIT_ARTIFACTS) == len(af.REFIT),
          "every REFIT step has a declared artifact, so none escapes the guard")

    # ---- the guard restores bytes, whatever the trainer did ---------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        serving = tmp / "saved_models"
        serving.mkdir(parents=True)
        candidates = serving / "candidates"

        original = {}
        for i, name in enumerate(af.REFIT_ARTIFACTS):
            body = f"SERVING-CHAMPION-{i}".encode() * 64
            (serving / name).write_bytes(body)
            original[name] = af._digest(str(serving / name))

        # Point the module at the sandbox.
        saved = (af.SERVING_DIR, af.CANDIDATE_ROOT)
        af.SERVING_DIR, af.CANDIDATE_ROOT = str(serving), str(candidates)
        try:
            # One artifact is deliberately NOT being served before the run, so the guard's
            # "there was nothing here before" branch is exercised rather than assumed.
            rogue, unserved = af.REFIT_ARTIFACTS[0], af.REFIT_ARTIFACTS[1]
            os.remove(serving / unserved)
            original[unserved] = None

            # The snapshot must be taken BEFORE any trainer runs - that is the only moment
            # the champion bytes are still on disk to be recorded.
            before = af.snapshot_serving()
            check(before["digests"] == original,
                  "the pre-run snapshot records the serving digests it must defend")

            # Simulate the exact failure: a trainer that ignores the redirect and writes
            # straight to the serving directory, plus one that lands under a name nothing
            # was serving.
            candidate_dir = candidates / "20260805T000000Z"
            (serving / rogue).write_bytes(b"CHALLENGER-WROTE-OVER-THE-CHAMPION" * 64)
            (serving / unserved).write_bytes(b"NEW-ARTIFACT-NEVER-SERVED" * 64)

            report = af.protect_serving(before, str(candidate_dir))

            check(af._digest(str(serving / rogue)) == original[rogue],
                  "an artifact overwritten by a rogue trainer is restored BYTE-FOR-BYTE - "
                  "this is the check the old job had no way to fail")
            check(rogue in report["captured"],
                  "and its output is not thrown away, it is captured as a candidate")
            check((candidate_dir / rogue).read_bytes().startswith(b"CHALLENGER"),
                  "the candidate directory holds the challenger's actual output")
            check(not (serving / unserved).exists(),
                  "an artifact that was NOT being served is removed rather than silently "
                  "promoted by virtue of appearing in the serving directory")

            # The load-bearing assertion, stated plainly.
            after = {n: af._digest(os.path.join(str(serving), n)) for n in af.REFIT_ARTIFACTS}
            check(after == original,
                  "AFTER a run that overwrote serving, every serving digest is unchanged")
        finally:
            af.SERVING_DIR, af.CANDIDATE_ROOT = saved

    # ---- the job must not claim to deploy ---------------------------------------------
    src = (BACKEND / "auto_finetune.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = (ast.get_docstring(tree) or "").lower()
    check("candidate" in doc,
          "the module docstring states the output is a candidate, so the next reader is not "
          "told it deploys")
    check("no restart" not in doc and "no restart needed" not in src.lower(),
          "and no longer promises 'no restart needed' - it does not touch serving at all")
    # Describing the RETIRED behaviour is allowed and useful; claiming it in the present is
    # not. The distinction is what the bytes do, asserted above; this only stops the old
    # promise from creeping back into the docstring.

    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    sets_env = any(
        isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
        and n.value.attr == "environ"
        for n in ast.walk(main_fn))
    check(sets_env, "main() sets the output-directory environment variable itself, so the "
                    "redirect does not depend on the caller remembering to")
    calls = {n.func.id for n in ast.walk(main_fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("protect_serving" in calls and "snapshot_serving" in calls,
          "and invokes the guard - parsed, because both names also appear in definitions")
    tries = [n for n in ast.walk(main_fn) if isinstance(n, ast.Try) and n.finalbody]
    check(any(any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                  and c.func.id == "protect_serving"
                  for c in ast.walk(stmt))
              for t in tries for stmt in t.finalbody),
          "from a finally: block, so a crash mid-run cannot leave serving half-rewritten")

    print(f"\nAUTO-FINETUNE CANDIDATE-ONLY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
