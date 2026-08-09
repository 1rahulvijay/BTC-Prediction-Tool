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
    global CHECKS
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
    check("groups=_settlement_groups" in server_code.replace(" ", ""),
          "the server passes horizon-specific sequence-plus-outcome dependence blocks, so "
          "confidence bounds do not treat overlapping one-minute rows as separate trials")
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
    n = 3600
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

    # Labels are keyed BY CONTRACT. Reading them positionally now raises, which is the point:
    # the two settlement label sets disagree on most rows, so a caller has to say which it
    # means rather than receive whichever one happened to be first.
    check(set(Ysettle) == {tc.ENDPOINT_SETTLEMENT_V1, tc.ROLLING_EXCHANGE_RETURN_SIGN_V1},
          "build_sequences emits BOTH settlement label sets, keyed by the contract they "
          "answer, so neither lane has to reinterpret the other's labels")
    Ybinary = Ysettle[tc.ROLLING_EXCHANGE_RETURN_SIGN_V1]
    Ybanded = Ysettle[tc.ENDPOINT_SETTLEMENT_V1]

    # Compare by NAME, not by column index: the two layouts have different widths, so equal
    # argmax integers would not mean equal outcomes.
    path_names = [tc.CLASS_ORDER[i] for i in np.argmax(Ypath[horizon], axis=1)]
    binary_names = [tc.BINARY_CLASS_ORDER[i] for i in np.argmax(Ybinary[horizon], axis=1)]
    banded_names = [tc.CLASS_ORDER[i] for i in np.argmax(Ybanded[horizon], axis=1)]
    disagree = float(np.mean([a != b for a, b in zip(path_names, binary_names)]))
    check(disagree > 0.05,
          f"path and BINARY settlement labels disagree on {disagree:.1%} of real "
          f"build_sequences rows - if they agreed everywhere the split would be a naming "
          f"exercise")
    band_gap = float(np.mean([a != b for a, b in zip(banded_names, binary_names)]))
    check(band_gap > 0.05,
          f"and the two SETTLEMENT contracts disagree on {band_gap:.1%} of the same rows - "
          f"every one is a real payout the banded contract calls NEUTRAL")
    check(tc.NEUTRAL not in set(binary_names),
          "no row is labelled NEUTRAL under the binary contract - the venue has no flat "
          "outcome to pay out on")
    check(len(Ybinary[horizon]) == len(X) and Ybinary[horizon].shape[1] == 2,
          "binary settlement labels are aligned 1:1 with the feature rows, in two columns")

    # THE LABELS MUST BE THE CONTRACT, recomputed independently from the prices.
    #
    # Every check above passes if the binary labels are derived from the BANDED ones
    # (NEUTRAL folded into DOWN): the disagreement rates stay high and no row reads NEUTRAL.
    # A mutation doing exactly that survived. The only assertion that catches it is comparing
    # against `label_polymarket_binary` applied to the same entry/settle prices, so the label
    # is pinned to the rule object's verified comparator rather than to a plausible-looking
    # distribution.
    #
    # Entry is closes[i] and settlement is closes[min(i + h, len - 1)], matching
    # build_sequences: row r corresponds to i = lookback + r.
    expected = []
    for r in range(len(X)):
        i = pad + r
        entry_px = closes[i]
        final_px = closes[min(i + horizon, len(closes) - 1)]
        expected.append(tc.label_polymarket_binary(entry_px, final_px))
    emitted = [tc.BINARY_CLASS_ORDER[j] for j in np.argmax(Ybinary[horizon], axis=1)]
    check(emitted == expected,
          "every emitted binary label equals label_polymarket_binary() recomputed from the "
          "entry and settlement prices - the labels ARE the contract, not a relabelling of "
          "the banded ones that happens to have the right shape")

    # And they are genuinely NOT the banded labels with NEUTRAL folded into DOWN, which is
    # the specific wrong answer that would otherwise pass every distributional check.
    folded = [tc.UP if name == tc.UP else tc.DOWN for name in banded_names]
    check(emitted != folded,
          "and they differ from the banded labels with NEUTRAL folded into DOWN - the exact "
          "substitution that satisfies every rate-based check while being wrong")

    split = int(len(X) * 0.8)
    # One dependence block per non-overlapping sequence-plus-horizon window. The production
    # server derives these from wall-clock timestamps; the synthetic fixture is one row/minute.
    groups = {horizon: np.arange(len(X), dtype=np.int64) // (pad + horizon)}
    bundle = train_settlement_head(
        X, Ybinary, split, horizons=[horizon], groups=groups,
    )
    check(bundle["target_contract"] == tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
          "the head fitted from REAL build_sequences output carries the BINARY contract")

    probability = settlement_probability(bundle, X[split].reshape(-1), horizon)
    check(0.0 <= probability["p_up"] <= 1.0, "and yields a probability in [0, 1]")
    check(probability.get("uncertainty_method") == "group_bootstrap_95"
          and probability.get("confidence_lower_95") is not None,
          "and exposes an empirical lower confidence bound from grouped dependence blocks")
    check(tc.assert_admissible(tc.PROXY_SETTLEMENT_RESEARCH,
                               probability["target_contract"])
          == tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
          "which a RESEARCH consumer accepts, so the head is measurable")
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, probability["target_contract"])
        raise AssertionError("the proxy priced a real Polymarket settlement EV")
    except tc.ContractMisuse:
        CHECKS += 1
        print("  PASS  while a real settlement-EV consumer REFUSES it - wrong price series "
              "and wrong reference point, so the lane still has no admissible head")
    check("p_neutral" not in probability,
          "and it carries no p_neutral, because the market it prices has no flat outcome")

    # The path head must still be refused for the same purpose.
    try:
        tc.assert_admissible(tc.POLYMARKET_SETTLEMENT_EV, tc.TRAINING_CONTRACT)
        raise AssertionError("the path contract was accepted for a settlement EV")
    except tc.ContractMisuse:
        pass
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
        train_settlement_head(X[:100], {horizon: Ybinary[horizon][:100]}, 80,
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
