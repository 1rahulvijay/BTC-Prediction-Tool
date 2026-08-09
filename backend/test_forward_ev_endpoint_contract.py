"""The forward-EV ledger may only resolve economically from a genuine ENDPOINT observation.

THE DEFECT
    `forward_ev_ledger` computes notional trading return - net_pnl_usd, avoided_loss_usd,
    opportunity_cost_usd. The resolver passed:

        v.get("endpoint_price",    v.get("actual_price", current_price))
        v.get("endpoint_move_usd", v.get("actual_move_usd", 0.0))

    without consulting `endpoint_price_basis`.

    Subtler than a fallback: on a BARRIER_FALLBACK row `endpoint_move_usd` IS present, computed
    from the first-touch barrier price, so the `.get(..., default)` never fires and the barrier
    value flows straight through. Under the first-touch contract |move| is a constant by
    construction, so what reached the ledger was a fixed barrier distance dressed as a realised
    return - in the exact table intended to answer whether a strategy makes money.

    The classification row records the basis two lines above. This consumer never read it, the
    same shape as the expectancy defect fixed in 0342e18.

    python backend/test_forward_ev_endpoint_contract.py
"""
from __future__ import annotations

import ast
import sys
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


def main() -> int:
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    call = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "resolve_forward_ev_event"), None)
    check(call is not None, "the forward-EV economic resolver is called somewhere")

    # It must sit inside a branch conditioned on the endpoint basis. Parsed, because the
    # failure mode is that the guard is ABSENT - a substring search for the basis name would
    # also match the classification row two lines above, which always carried it.
    guarding = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        if "_ep_basis" not in test_src and "endpoint_price_basis" not in test_src:
            continue
        for sub in ast.walk(node):
            if sub is call:
                guarding.append(test_src)
    check(guarding,
          f"and it is REACHABLE ONLY through a branch testing the endpoint basis "
          f"({guarding[:1]}) - it previously had no such guard, so barrier economics resolved "
          f"as realised returns")

    check(any("ENDPOINT" in g for g in guarding),
          "the branch compares against ENDPOINT specifically, not merely 'a basis exists' - "
          "BARRIER_FALLBACK is also a basis")

    # A skipped row must be visible, not silent.
    # PARSED for the assignment, not a substring: a mutation that renamed only the write
    # target left the same key in the adjacent .get(), so the counter was broken while a
    # substring check still passed. The key must be the one actually WRITTEN.
    written = {
        ast.unparse(tgt.slice)
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for tgt in node.targets
        if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name)
        and tgt.value.id == "backend_state"
    }
    check("'forward_ev_economic_skips'" in written,
          f"skipped rows are COUNTED into backend_state under that exact key - asserted by "
          f"parsing the assignment target, so a renamed write cannot pass on a leftover read")
    idx = src.find("NOT resolved economically")
    check(idx > 0 and "logger.warning" in src[max(0, idx - 300):idx],
          "and logged at WARNING, not DEBUG - a silently skipped economic outcome is how this "
          "class of defect stays invisible for weeks")

    # The known remaining gap must be recorded where the next reader will hit it.
    check("outcome_status" in src,
          "the missing outcome_status column is named in-place as the follow-up, so 'stays "
          "unresolved' is a recorded limitation rather than an accident: the ledger cannot "
          "yet distinguish ECONOMIC_OUTCOME_UNAVAILABLE from not-yet-resolved")

    print(f"\nFORWARD-EV ENDPOINT CONTRACT: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
