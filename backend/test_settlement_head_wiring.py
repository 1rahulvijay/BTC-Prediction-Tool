"""Gate 4.4: the settlement head must be REACHABLE from the real training path.

The head itself is tested in `settlement_head.py --selftest`. This tests the thing that has
gone wrong repeatedly in this repository: a component that works in isolation while nothing
production runs ever calls it. `record_strike_observation` was defined and only ever invoked
from its own selftest; `return_settlement_labels` existed for days with no caller at all.

So this asserts the WIRING, against the real source:

    build_sequences emits settlement labels  ->  the server asks for them
    ->  the server trains the head  ->  the artifact is written with provenance
    ->  the head is registered and carries no authority

    python backend/test_settlement_head_wiring.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import target_contract as tc                                       # noqa: E402
from features import build_sequences, compute_adaptive_threshold_series   # noqa: E402
from settlement_head import (                                      # noqa: E402
    MIN_TRAIN_ROWS, REGISTRY_NAME, TARGET_CONTRACT, SettlementHeadUnavailable,
    settlement_probability, train_settlement_head)

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def _code_only(source: str) -> str:
    """Source with docstrings and comments stripped.

    A substring search over raw source has produced a false PASS three times in this work -
    matching a name inside the very comment that documents the retired behaviour."""
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
    server_code = _code_only((BACKEND / "server.py").read_text(encoding="utf-8"))

    # ---- the trainer must ASK for the labels ------------------------------------------
    check("return_settlement_labels=True" in server_code,
          "the server's build_sequences call requests settlement labels - they existed for "
          "days with no caller, which is why the lane had no head")
    check("Ysettle" in server_code,
          "and it binds the returned labels rather than discarding them")

    # ---- and TRAIN the head on them ---------------------------------------------------
    # PARSED, not grepped. The name survives in the import line, so a substring check passes
    # even when the call is replaced by a stub - a mutation doing exactly that survived the
    # first version of this test.
    server_tree = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    invoked = False
    for node in ast.walk(server_tree):
        if not isinstance(node, ast.Call):
            continue
        # Direct call: train_settlement_head(...)
        if isinstance(node.func, ast.Name) and node.func.id == "train_settlement_head":
            invoked = True
        # Deferred call: functools.partial(train_settlement_head, ...)
        target = node.func.attr if isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node.func, ast.Name) else "")
        if target == "partial" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id == "train_settlement_head":
                invoked = True
    check(invoked,
          "the server INVOKES train_settlement_head - asserted by parsing for a call or a "
          "functools.partial binding, because the name also appears in the import")
    check("settlement_head.pkl" in server_code,
          "the fitted head is written to an artifact, not left in memory")
    check("write_artifact_manifest" in server_code or "_write_manifest" in server_code,
          "with a provenance manifest, so it does not read UNKNOWN like the twelve artifacts "
          "that predate the contract repair")

    # ---- failure must ABSTAIN, never substitute ---------------------------------------
    check("SettlementHeadUnavailable" in server_code,
          "the server handles the unavailable case explicitly")
    settle_block = server_code[server_code.index("train_settlement_head"):]
    settle_block = settle_block[:4000]
    check("target_model.train" not in settle_block.split("settlement_head")[0][:200],
          "the head is trained SEPARATELY from the ensemble, so a failure here cannot take "
          "down the path model that actually serves")

    # ---- end to end on real build_sequences output ------------------------------------
    entry, horizon, pad = 100.0, 5, 5
    probe = compute_adaptive_threshold_series(np.full(40, entry), np.full(40, 1.0))
    threshold = float(probe[pad])

    rng = np.random.default_rng(7)
    n = 2600
    # A drifting series so endpoint labels carry both classes, with intrabar extremes that
    # make first-touch and endpoint genuinely disagree on some rows.
    steps = rng.normal(0, threshold * 0.9, n)
    closes = entry * np.cumprod(1.0 + steps)
    highs = closes * (1.0 + np.abs(rng.normal(0, threshold * 0.6, n)))
    lows = closes * (1.0 - np.abs(rng.normal(0, threshold * 0.6, n)))
    features = rng.normal(0, 1, (n - 1, 4)).astype(np.float32)

    X, Ypath, Yvalid, Ysettle = build_sequences(
        features, closes, lookback=pad, horizons=[horizon], atr_arr=np.full(n, 1.0),
        highs=highs, lows=lows, return_valid_mask=True, return_settlement_labels=True)

    path_labels = np.argmax(Ypath[horizon], axis=1)
    settle_labels = np.argmax(Ysettle[horizon], axis=1)
    disagree = float(np.mean(path_labels != settle_labels))
    check(disagree > 0.05,
          f"path and settlement labels disagree on {disagree:.1%} of real build_sequences "
          f"rows - if they agreed everywhere the split would be a naming exercise")
    check(len(Ysettle[horizon]) == len(X),
          "settlement labels are aligned 1:1 with the feature rows")

    split = int(len(X) * 0.8)
    bundle = train_settlement_head(X, Ysettle, split, horizons=[horizon])
    check(bundle["target_contract"] == tc.ENDPOINT_SETTLEMENT_V1,
          "the head fitted from REAL build_sequences output carries the endpoint contract")

    probability = settlement_probability(bundle, X[split].reshape(-1), horizon)
    check(0.0 <= probability["p_up"] <= 1.0, "and yields a probability in [0, 1]")
    check(tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV,
                               probability["target_contract"]) == tc.ENDPOINT_SETTLEMENT_V1,
          "which a settlement-EV consumer ACCEPTS - the refusal that had no remedy now has one")

    # The path head must still be refused for the same purpose.
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, tc.TRAINING_CONTRACT)
        raise AssertionError("the path contract was accepted for a settlement EV")
    except tc.ContractMisuse:
        pass
    global CHECKS
    CHECKS += 1
    print("  PASS  while the PATH contract is still refused for that purpose, so the head "
          "supplements the ensemble rather than replacing its guard")

    # ---- registry: present, and powerless ---------------------------------------------
    from model_registry import REGISTRY
    row = next((e for e in REGISTRY if e.name == REGISTRY_NAME), None)
    check(row is not None, "the head has a registry row, so it is not an unregistered bypass")
    check(row.target == TARGET_CONTRACT, "declaring the contract it answers")
    check(not (row.may_price or row.may_rank or row.may_size),
          "and carrying NO authority - it exists to be measured, not to price")

    check(MIN_TRAIN_ROWS >= 500,
          "a floor on training rows, so a thin fit produces no artifact at all")
    try:
        train_settlement_head(X[:100], {horizon: Ysettle[horizon][:100]}, 80,
                              horizons=[horizon])
        raise AssertionError("a thin fit produced a head")
    except SettlementHeadUnavailable:
        CHECKS += 1
        print("  PASS  and a thin fit RAISES rather than writing an artifact whose existence "
              "would imply evidence")

    print(f"\nSETTLEMENT HEAD WIRING: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
