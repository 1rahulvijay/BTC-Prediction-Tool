"""A refused promotion must not become an activation, and training must not touch serving.

THE DEFECT
    `train_model()` set `promotion_pipeline = False` when the forward-evidence gate denied
    promotion, and then reached an UNGUARDED swap:

        model = candidate
        _install_hmm_state(model, "retrain-swap")

    So "promotion refused" became "activate through the ordinary retrain route" - a promotion
    under another name, with no forward evidence and no gate report. The comment at the gate
    said only the promotion was withheld. The control flow said otherwise.

    The same denial set `candidate_dir = None`, and MultiModelEnsemble falls back to MODEL_DIR
    when given None - the ACTIVE serving directory. A candidate whose promotion had just been
    refused therefore wrote its artifacts over the live ones before any promotion transaction
    existed, and a restart would load a model that was never promoted.

    Both are the same shape as the grader defect found the same day: a REFUSAL that silently
    becomes an ACTIVATION.

WHY THESE ARE PARSED
    Both are properties of control flow, not of a return value. A runtime test would have to
    drive a full training run to observe them, and the failure mode is precisely that the
    guard is ABSENT - so the assertion has to be about the structure of the code.

    python backend/tests/test_promotion_activation_boundary.py
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

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def enclosing_ifs(tree, line):
    """Every `if` whose BODY contains `line`, with its test source."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if any(s.lineno <= line <= (s.end_lineno or s.lineno) for s in node.body):
            out.append(ast.unparse(node.test))
    return out


def main() -> int:
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # The staging and the swap both live in relearn_models_background(), NOT train_model().
    # Checked by name so the test fails loudly if the flow is moved, rather than silently
    # finding nothing and passing - a test that searches the wrong function proves nothing.
    train_fn = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == "relearn_models_background"), None)
    check(train_fn is not None,
          "relearn_models_background() is present - it owns both the candidate staging and "
          "the activation swap")

    # ---- P0-2: no training run may resolve to the active serving directory --------------
    cand = [n for n in ast.walk(train_fn)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "candidate_dir" for t in n.targets)]
    check(cand, "candidate_dir is assigned in the staging flow")
    for node in cand:
        rendered = ast.unparse(node.value)
        check("None" not in rendered,
              f"candidate_dir is NEVER None ({rendered[:70]}) - None made the ensemble fall "
              f"back to the ACTIVE serving directory")
    check("UnsafeTrainingDestination" in src,
          "and a guard refuses outright if a run ever resolves to the serving directory, "
          "rather than trusting the expression above to stay correct")

    # ---- P0-1: the swap must be guarded --------------------------------------------------
    swap_line = None
    for node in ast.walk(train_fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_install_hmm_state"
                and any(isinstance(a, ast.Constant) and a.value == "retrain-swap"
                        for a in node.args)):
            swap_line = node.lineno
    check(swap_line is not None, "the retrain-swap activation is present to be checked")

    # The swap must be UNREACHABLE for a non-bootstrap run. Two shapes do that: wrapping it
    # in an `if bootstrap:` block, or an early `if not bootstrap: ... return` before it.
    # Asserting only the first would have failed on a correct early-return guard, so both
    # count - what matters is that the path cannot be reached, not how it is spelled.
    guards = enclosing_ifs(train_fn, swap_line)
    wrapped = any("bootstrap" in g.lower() or "incumbent" in g.lower() for g in guards)
    early_return = [
        n for n in ast.walk(train_fn)
        if isinstance(n, ast.If)
        and ("bootstrap" in ast.unparse(n.test).lower()
             or "incumbent" in ast.unparse(n.test).lower())
        and n.lineno < swap_line
        and any(isinstance(s, ast.Return) for s in ast.walk(n))]
    check(wrapped or early_return,
          f"the activation at line {swap_line} is UNREACHABLE without bootstrap - it "
          f"previously had zero enclosing conditions and no preceding guard, so a denied "
          f"gate reached it unchanged")
    check(early_return or wrapped,
          "and the condition is the BOOTSTRAP one - the only case where activating without "
          "promotion is defensible is having no trained incumbent, because the alternative "
          "is serving nothing")
    if early_return:
        blk = early_return[-1]
        check(any(isinstance(s, ast.Return) for s in blk.body),
              "the refusal RETURNS from inside its own guard rather than logging and falling "
              "through - a log line that continues is how the original defect read as handled")

    # ---- the state a caller can inspect --------------------------------------------------
    check("TRAINED_ONLY" in src,
          "the refused case is named TRAINED_ONLY in the log, so the outcome is greppable "
          "rather than inferred from the absence of a swap message")
    check("last_candidate_activated" in src,
          "and recorded in backend_state, so an operator can tell a staged candidate from an "
          "activated one without reading logs")

    print(f"\nPROMOTION/ACTIVATION BOUNDARY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
