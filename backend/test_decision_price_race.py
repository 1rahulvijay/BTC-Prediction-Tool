"""Every prediction consumer must read the FROZEN decision price, never the live global.

THE DEFECT
    `main_loop` freezes a decision view, then suspends twice - once per horizon for the
    inference executor, once for the revision-ledger write:

        4760  current_price = decision_state["decision_price"]     frozen
        4832  current_price = decision_state["decision_price"]     frozen
        4873  await run_in_executor(... revision ledger ...)       SUSPENSION
        4889  current_price = decision_state["decision_price"]     frozen
        4930  current_price = data_state["klines"][-1]["close"]    LIVE RE-READ
              verifier.record_prediction(p, current_price, now_ms)

    A WebSocket callback can move `data_state` while those awaits are in flight, so the price
    persisted as a prediction's entry was one the model never saw. That corrupts realized
    move, target error, forward-EV entry economics and eventual training feedback - and it
    manufactures or destroys apparent alpha depending only on which way price ticked.

    Three of the four consumers already read the frozen value. The earlier decision-snapshot
    fix was real; ONE consumer downstream of the suspension point had reverted, which is
    exactly why it looked complete.

WHY THIS IS PARSED
    Reaching line 4930 at runtime needs a trained model, live feeds and a full main_loop
    cycle. The property is structural - "no consumer between the freeze and the record reads
    the live global" - so it is asserted against the source, and the mutation that reverts it
    is checked to fail.

    python backend/test_decision_price_race.py
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
LIVE_READ = 'data_state["klines"][-1]["close"]'


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()

    loop = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "main_loop")

    # Anchor on the two real landmarks rather than on line numbers, which drift.
    freeze = next(n.lineno for n in ast.walk(loop)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "_build_decision_snapshot")
    record = next(n.lineno for n in ast.walk(loop)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "record_prediction")
    check(freeze < record,
          f"the decision is frozen (line {freeze}) before it is recorded (line {record})")

    # There MUST be a suspension point between them, or this test proves nothing: with no
    # await there is no race and a live re-read would be harmless.
    awaits = sorted(n.lineno for n in ast.walk(loop)
                    if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
                    and freeze < n.lineno < record)
    check(awaits,
          f"and {len(awaits)} suspension point(s) sit between them {awaits[:4]} - a WebSocket "
          f"callback can mutate data_state at each one, which is what makes a live re-read a "
          f"race rather than a style choice")

    # THE ASSERTION. No live-global price read between the freeze and the record.
    offenders = [i + 1 for i, ln in enumerate(lines)
                 if freeze < i + 1 < record and LIVE_READ in ln
                 and not ln.strip().startswith("#")]
    check(not offenders,
          f"no consumer between the freeze and the record reads the LIVE global "
          f"(found {offenders}) - line 4930 did exactly this, after both suspension points")

    # And the recording consumer positively reads the frozen value.
    window = "\n".join(lines[freeze:record])
    check('decision_state["decision_price"]' in window,
          "the recording path reads decision_state['decision_price'] - asserted positively, "
          "because absence of the live read could also mean the code moved elsewhere")

    # The oracle reference must come from the same instant as the price it reconciles against.
    ref = [ln for ln in lines[freeze:record] if "reference_price =" in ln]
    check(ref, "a reference_price is computed in the window")
    cl_src = "\n".join(lines[freeze:record])
    idx = cl_src.find("_cl =")
    check(idx >= 0 and "decision_state" in cl_src[idx:idx + 200],
          "and its oracle input is taken from the frozen view first - an oracle read at a "
          "later instant than the price it is reconciled against reintroduces the same defect "
          "in the chainlink_price column")

    print(f"\nDECISION PRICE RACE: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
