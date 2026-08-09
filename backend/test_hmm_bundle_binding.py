"""P0-1: regime routing follows the BUNDLE, and changes only when a bundle is installed.

    python backend/test_hmm_bundle_binding.py

TWO DEFECTS, both silent

  1. `MarketRegime.state_dict()` / `load_state_dict()` existed and were tested, but NOTHING in
     production called them. After a restart the live engine came up fresh with
     hmm_ready=False, so experts trained on HMM-derived TREND/RANGE/VOLATILE partitions were
     routed by the heuristic fallback instead. Nothing raised - the fallback answers every
     call - so a train/serve partition mismatch looked exactly like normal operation.

  2. `train_model()` called `regime_engine.fit_hmm(...)` on the module-global engine that the
     serving loop reads for every prediction. So the instant a retrain began, the INCUMBENT's
     live routing changed to parameters belonging to a candidate that had not been trained
     yet, let alone gated, shadowed or promoted. A challenger that was ultimately REJECTED
     still permanently repartitioned the model that kept serving.

WHAT THIS TEST WILL NOT CLAIM
    It does not start a server. It asserts the two properties that were actually broken: the
    parameters travel with the bundle, and fitting a candidate cannot reach the live engine.
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

from regime import MarketRegime                       # noqa: E402

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _series(n=900, seed=5):
    rng = np.random.default_rng(seed)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    volumes = np.abs(rng.normal(1000, 200, n))
    return closes, volumes


def _code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
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
    here = Path(__file__).resolve().parent
    closes, volumes = _series()

    print("fitting a candidate cannot reach the live engine")
    live = MarketRegime()
    candidate = MarketRegime()
    chk(not live.hmm_ready, "the live engine starts un-fitted")
    chk(candidate.fit_hmm(closes, volumes), "the candidate fits")
    chk(candidate.hmm_ready and not live.hmm_ready,
        "the candidate is ready while the live engine is UNCHANGED - training a challenger no "
        "longer repartitions the model that is still serving")

    print("routing changes only when a bundle is installed")
    state = candidate.state_dict()
    chk(live.load_state_dict(state) and live.hmm_ready,
        "installing the bundle's state makes the live engine ready")
    same = [live.classify_series(closes, volumes)[i] == candidate.classify_series(
        closes, volumes)[i] for i in range(0, len(closes), 97)]
    chk(all(same),
        f"and it then routes IDENTICALLY to the engine that trained the partition "
        f"({sum(same)}/{len(same)} sampled rows agree)")

    print("a bundle with no HMM does not silently pretend")
    blank = MarketRegime()
    chk(blank.load_state_dict(None) is False, "a missing state is refused")
    chk(blank.load_state_dict({}) is False, "and an empty one is too")
    chk(not blank.hmm_ready, "so the engine stays honestly un-ready")

    # ---- WIRING. The helpers worked before; nothing called them. ---------------------------
    print("the production paths actually call it")
    server = _code_only(here / "server.py")
    chk("def _install_hmm_state(" in server,
        "server owns one installer, so routing has a single entry point")
    chk("_install_hmm_state(model, \"boot\")" in server,
        "BOOT installs the loaded bundle's HMM - this is the call that did not exist")
    for reason in ("bootstrap-promotion", "retrain-swap", "challenger-promotion"):
        chk(f'_install_hmm_state(model, "{reason}")' in server,
            f"{reason} installs the promoted bundle's HMM")

    chk("regime_engine.fit_hmm(" not in server,
        "training no longer fits the LIVE engine - the global is never a fit target")
    chk("candidate_regime = MarketRegime()" in server
        and "candidate_regime.fit_hmm(" in server,
        "it fits a candidate engine instead")
    chk("target_model.hmm_state = candidate_regime.state_dict()" in server,
        "and the candidate's parameters are attached to its own bundle")
    chk("candidate_regime.classify_series(" in server,
        "the training partition comes from the candidate, not from whatever is serving")

    print("the bundle persists and restores it")
    model_src = _code_only(here / "model.py")
    chk('save_bundle_value(getattr(self, "hmm_state", None), "hmm_state.pkl")' in model_src,
        "save_models writes the HMM into the bundle")
    chk('hmm_state = values["hmm_state.pkl"]' in model_src
        and 'required_hmm.issubset(hmm_state)' in model_src,
        "load_models reads it back and rejects a partial state")
    chk("self.hmm_state = None" in model_src,
        "and an older bundle without one restores as None rather than an empty dict - "
        "'carries no HMM' and 'carries an HMM that failed to fit' must stay distinguishable")

    print("\nHMM BUNDLE BINDING:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
