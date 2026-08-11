"""Every place that names this head's target must name the SAME target.

This repository has repeatedly had a stale description read as verified architecture. The
settlement head's own docstring claimed `ENDPOINT_SETTLEMENT_V1` while its TARGET_CONTRACT was
something else, and separately claimed to answer "the question Polymarket actually resolves on"
while its labels used the wrong price series AND the wrong reference point.

Prose cannot be trusted to stay in step with code, so the agreement is asserted:

    module docstring  ==  TARGET_CONTRACT  ==  registry row  ==  fitted artifact  ==  served output

    python backend/tests/test_target_declaration_consistency.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

import numpy as np

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


def main() -> int:
    import target_contract as tc
    import settlement_head as sh
    from model_registry import REGISTRY

    target = sh.TARGET_CONTRACT
    check(target in tc.KNOWN_CONTRACTS, f"TARGET_CONTRACT {target!r} is a declared contract")

    # 1. the docstring must name it, and must NOT name a different known contract
    doc = ast.get_docstring(ast.parse((BACKEND / "settlement_head.py").read_text("utf-8"))) or ""
    # Case-insensitive: prose names the CONSTANT (upper), the value is lower. Both count.
    low = doc.lower()
    check(target.lower() in low,
          "the module docstring names the contract the head is actually stamped with")
    # Scoped to the sentence that makes the CLAIM. A docstring may legitimately mention other
    # contracts as context - this one explains that TRAINING_CONTRACT is the first-touch
    # contract - so a blunt "mentions no other contract" check fails on correct prose.
    # A character window, not whole lines. The claim wraps across a line break, so a
    # line-scoped search finds "stamped" and none of the contract name that follows it.
    check("stamped" in low,
          "the docstring states what the head is STAMPED with, in those words, so the claim is "
          "locatable rather than implied")
    _at = low.index("stamped")
    claim = " ".join(low[_at:_at + 200].split())
    check(target.lower() in claim,
          "and that claim names the head's actual target")
    others = [c for c in tc.KNOWN_CONTRACTS
              if c != target and c.lower() in claim and c.lower() not in target.lower()]
    check(not others,
          f"with no OTHER contract named in the same claim (found {others}) - the docstring "
          f"said ENDPOINT_SETTLEMENT_V1 while the code stamped something else")

    # 2. the registry row
    row = next((e for e in REGISTRY if e.name == sh.REGISTRY_NAME), None)
    check(row is not None and row.target == target,
          "the registry row declares the same target as the module")

    # 3. the fitted artifact and 4. the served output
    rng = np.random.default_rng(0)
    n, feats = 3000, 5
    X = rng.normal(0, 1, (n, feats))
    score = X[:, 0] + rng.normal(0, 0.8, n)
    Y = {5: np.eye(2)[(score > 0).astype(int)].astype(np.float32)}
    bundle = sh.train_settlement_head(X, Y, 2400, horizons=[5])
    check(bundle["target_contract"] == target,
          "the FITTED artifact carries the same target, not merely the module constant")
    served = sh.settlement_probability(bundle, X[2400], 5)
    check(served["target_contract"] == target,
          "and so does the served probability, so a consumer checks the same name end to end")

    # 5. the claim it must NOT make
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, target)
        raise AssertionError("the head was admitted to price a real Polymarket settlement")
    except tc.ContractMisuse:
        global CHECKS
        CHECKS += 1
        print("  PASS  and it is REFUSED for POLYMARKET_SETTLEMENT_EV - its labels use the "
              "wrong price series and the wrong reference point, so it may be measured but "
              "not used to price that market")

    print(f"\nTARGET DECLARATION CONSISTENCY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
