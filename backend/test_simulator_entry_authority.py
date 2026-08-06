"""A model LEAN may not become a simulated trade. Only an authorized decision may.

    python backend/test_simulator_entry_authority.py

THE DEFECT

`TradingSimulator.process_signal()` consumed `direction` and nothing else:

    direction = prediction.get("direction", "NEUTRAL")
    if direction not in ["UP", "DOWN"]:
        return

The decision gate deliberately keeps a committed UP/DOWN side visible on a WEAK_LEAN verdict -
decision_gate.py calls it "a committed side but with caveats" - and `finalAction` is assigned in
server.py at line ~4480, BEFORE process_signal is called at ~4652. So the authoritative verdict
existed, was sitting on the same dict, and was never read.

This state opened a position:

    direction        = UP
    actionable       = False
    trade_verdict    = WEAK_LEAN
    no_trade_reasons = ["not_actionable"]

Its P&L then fed win rate and Kelly sizing, so a lean the decision layer refused to act on
became evidence about a strategy nobody authorized.

SECOND DEFECT, SAME CALL: the EV formula reads `prob_win = prediction["confidence"]`, but the
main ensemble is trained under FIRST_TOUCH_TRIPLE_BARRIER_V1. "Which barrier is touched first"
is not "does this position finish ahead at the horizon". target_contract already refuses that
substitution for BINANCE_DIRECTIONAL_EV; this engine never asked.

THIRD DEFECT: `_neutralize_prediction` changed direction, signal and reasons - and left
`actionable`, `positionSize`, `stopLoss` and `takeProfit` untouched, so a refused lean carried
live-looking instructions.
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

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def code_only(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    doc: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr):
            v = body[0].value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                doc.update(range(v.lineno, (v.end_lineno or v.lineno) + 1))
    return chr(10).join(ln for i, ln in enumerate(src.splitlines(), start=1)
                        if i not in doc and not ln.strip().startswith("#"))


def _authorized(**over) -> dict:
    """A prediction that SHOULD be allowed through every gate except the ones under test."""
    import target_contract as tc

    p = {
        "id": "p1", "horizon": 5, "direction": "UP", "confidence": 0.62,
        "expectedMove": 120.0, "finalAction": "TRADE", "actionable": True,
        "no_trade_reasons": [], "targetContract": tc.ENDPOINT_SETTLEMENT_V1,
    }
    p.update(over)
    return p


def main() -> int:
    from trading_simulator import TradingSimulator

    sim = TradingSimulator.__new__(TradingSimulator)
    sim.blocked_entries = {}

    print("the exact state that used to open a position is refused")
    weak = _authorized(finalAction="WEAK_LEAN", actionable=False,
                       no_trade_reasons=["not_actionable"])
    chk(sim._entry_refusal(weak).startswith("verdict_weak_lean"),
        "direction=UP with trade_verdict=WEAK_LEAN and actionable=False is REFUSED, naming the "
        "verdict - this is the state the old direction-only check let through")

    print("every gate fails CLOSED")
    chk(sim._entry_refusal(_authorized(finalAction=None, trade_verdict=None))
        == "no_verdict_on_prediction",
        "a prediction carrying NO verdict refuses - defaulting to TRADE is how the original "
        "defect returns the moment a caller forgets a key")
    chk(sim._entry_refusal(_authorized(actionable=False)) == "not_actionable",
        "actionable must be exactly True, not merely truthy-by-absence")
    chk(sim._entry_refusal(_authorized(actionable="yes")) == "not_actionable",
        "and a non-boolean does not satisfy it")
    chk(sim._entry_refusal(_authorized(no_trade_reasons=["stale_feed"]))
        .startswith("blocked:"),
        "a populated blocker list refuses and names the blockers")
    chk(sim._entry_refusal(_authorized(finalAction="NO_TRADE")) == "verdict_no_trade",
        "NO_TRADE refuses")

    print("the target contract must be admissible for a DIRECTIONAL EV trade")
    import target_contract as tc

    chk(sim._entry_refusal(_authorized(targetContract=None)) == "target_contract_missing",
        "an unlabelled probability refuses - a float in [0,1] is not provenance")
    ft = sim._entry_refusal(_authorized(targetContract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1))
    chk(ft.startswith("target_contract_inadmissible"),
        f"a FIRST-TOUCH probability is refused for endpoint EV ({ft}) - which barrier is "
        f"touched first is not whether the position finishes ahead")
    chk(sim._entry_refusal(_authorized()) == "",
        "while a fully authorized, endpoint-contract prediction passes every gate")

    print("refusals are counted, not silently skipped")
    sim2 = TradingSimulator.__new__(TradingSimulator)
    sim2.blocked_entries = {}
    for _ in range(3):
        sim2.process_signal(_authorized(finalAction="WEAK_LEAN", actionable=False),
                            65000.0, 1_700_000_000_000)
    chk(sum(sim2.blocked_entries.values()) == 3 and not getattr(sim2, "active_trades", {}),
        f"three refused leans are recorded as {sim2.blocked_entries} and opened zero positions "
        f"- 'the simulator stopped trading' and 'the simulator is refusing for a named reason' "
        f"look identical without this")

    print("WEAK_LEAN is deliberately not an authorizing verdict")
    from trading_simulator import TradingSimulator as _TS

    chk("WEAK_LEAN" not in _TS.AUTHORIZED_ACTIONS and "TRADE" in _TS.AUTHORIZED_ACTIONS,
        f"AUTHORIZED_ACTIONS is {set(_TS.AUTHORIZED_ACTIONS)} - a committed side the decision "
        f"layer declined to act on is not an authorization")

    print("a rejection clears the action fields atomically")
    server_code = code_only(BACKEND / "server.py")
    for field in ('prediction["actionable"] = False', 'prediction["positionSize"] = 0',
                  'prediction["stopLoss"] = None', 'prediction["takeProfit"] = None',
                  'prediction["finalAction"] = "NO_TRADE"'):
        chk(field in server_code,
            f"_neutralize_prediction sets {field.split('=')[0].strip()}")
    chk('"model_stopLoss"' in server_code and '"model_positionSize"' in server_code,
        "and the model's own recommendation is PRESERVED under model_* names rather than "
        "deleted - useful diagnostics, impossible to mistake for an instruction")

    print("the paper engine is told WHICH question the probability answers")
    # _paper_fields is a whitelist; targetContract was stamped upstream and never copied, so
    # the model-consensus strategy refused for "missing" rather than "inadmissible" - and would
    # have kept refusing even once an admissible endpoint head existed.
    import re

    m = re.search(r"_paper_fields = \(\s*(.*?)\)", server_code, re.S)
    chk(m is not None and '"targetContract"' in m.group(1),
        "targetContract crosses the _binance_paper_context boundary, so the strategy can "
        "refuse for the CORRECT reason and can succeed once the target is admissible")

    print("\nSIMULATOR ENTRY AUTHORITY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
