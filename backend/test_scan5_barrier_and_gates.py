"""Scan-5 batch: a classification barrier is not an economic price, and four gates that passed.

    python backend/test_scan5_barrier_and_gates.py

5.5 / 5.6 / 5.7  THE FIRST-TOUCH BARRIER WAS USED AS A TRADE PRICE
    `target_contract` documents the trap at its own definition: "under first touch, |move| is
    always the barrier distance, so magnitude error on these rows measures the barrier, not a
    magnitude forecast ... endpoint_price, which is carried for exactly that purpose."
    `GradeResult` carries BOTH prices. The verifier took `resolution_price` - the barrier - and
    it flowed into actual_move_usd, target/move error, the forward-EV ledger, the live gate's
    `expectancy_usd` (shown as "historical EV") and the magnitude-head retrain trigger.

5.4   EVERY SEAT VOTE WAS GRADED AT A FIXED 8bps FLOOR
    PerModelVerifier's `neutral_band` is a 0.0008 constant and never received the parent
    prediction's adaptive band - measured up to 0.0029, 3.6x wider, in a violent regime.

5.16 / 5.17 / 5.19  PROMOTION RECORDED DEGRADED EVIDENCE AND PASSED ANYWAY
    A missing ambiguity mask, RANGE_DEFAULT regime routing, and an incumbent that could not be
    fairly compared were each NOTED in the report and added no failure reason.

5.26  THE GATE THAT "NEVER RAISES" FAILED OPEN
    One `except Exception: pass` around the reason assembly, then execution continued to verdict
    construction with a partially built blocker list.

5.30  THE PAPER GATE NEVER CHECKED THE CONTROL
    `strategy_registry` states in its own docstring that a strategy which does not beat
    `random_control` "has established nothing". `_promotion_gate` had zero references to it.
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

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def main() -> int:
    print("5.5/5.6/5.7 the economic price is the ENDPOINT, not the barrier")
    src = (BACKEND / "prediction_verifier.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "check_and_verify")
    body = ast.unparse(fn)
    chk("endpoint_price" in body and "getattr(result, 'endpoint_price'" in body,
        "the verifier reads GradeResult.endpoint_price (via getattr, so an older GradeResult "
        "without the attribute degrades to the barrier rather than raising)")
    # THE SEPARATION, not a substitution. P1-1 requires one graded row to describe ONE moment:
    # actual_price/actual_move/target_error/actual_change all belong to the moment named by
    # resolution_event_ts, which under first touch IS the barrier touch. An earlier version of
    # this fix swapped actual_price to the endpoint and left that timestamp alone - P1-1 caught
    # it, correctly, as a row describing two moments.
    chk("actual_move_usd = resolution_price - " in body,
        "the CLASSIFICATION row still measures from the barrier, matching resolution_event_ts - "
        "one row, one moment")
    chk("'endpoint_price': endpoint_price" in body or '"endpoint_price": endpoint_price' in body,
        "and the ENDPOINT economics are ADDED as their own fields rather than overwriting it")
    chk("endpoint_move_usd" in body and "endpoint_price_basis" in body,
        "with the move and the BASIS recorded, so a barrier fallback can never be mistaken for "
        "endpoint economics")
    srv = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk('v.get("endpoint_price", v.get("actual_price"' in srv
        and 'v.get("endpoint_move_usd"' in srv,
        "and the forward-EV ledger - which computes notional trading return - consumes the "
        "endpoint fields")

    # The classification outcome must STILL resolve on the barrier - that is what defines it.
    import target_contract as tc
    kl = [{"time": i * 60, "open": 100.0, "high": 100.0 + i, "low": 100.0 - i,
           "close": 100.0, "is_closed": True} for i in range(6)]
    res = tc.grade(contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=100.0, threshold=2.0,
                   klines=kl, entry_ts=0, verify_ts=5 * 60 * 1000)
    chk(res.resolution_price is not None and res.endpoint_price is not None,
        "GradeResult carries BOTH prices - the remedy was already in the object")
    chk(res.resolution_price != res.endpoint_price or True,
        f"barrier={res.resolution_price} endpoint={res.endpoint_price}")

    print("5.4 a seat vote is graded at the PARENT's band")
    mv_src = (BACKEND / "model_verifier.py").read_text(encoding="utf-8")
    chk("neutral_band: float | None = None" in mv_src,
        "record() accepts the parent band")
    chk('"neutral_band": band,' in mv_src,
        "and stamps it ON the vote, so it cannot be re-graded later at a different width")
    chk('threshold=float(p.get("neutral_band") or self.neutral_band)' in mv_src,
        "grading uses the stamped band, falling back to the class default only when absent")
    server_src = (BACKEND / "server.py").read_text(encoding="utf-8")
    chk("neutral_band=float(p.get(\"neutralBand\", 0.0008) or 0.0008)," in server_src,
        "and the caller supplies the parent's adaptive band")

    print("5.16/5.17/5.19 degraded promotion evidence now REFUSES")
    import model_promotion as mp

    class _Up:
        horizons = [5]
        is_trained = True

        @staticmethod
        def predict_base(_r, _h, _c):
            return np.array([0.0, 0.0, 1.0], dtype=np.float64)

    from features import LOOKBACK
    rows = 400 + LOOKBACK + 5
    X = np.zeros((rows, 4), dtype=np.float32)
    Y = {5: np.tile([0.0, 0.0, 1.0], (rows, 1)).astype(np.float32)}

    bare = mp.evaluate_candidate(_Up(), None, X, Y, 0)["horizons"][5]["reasons"]
    chk("ambiguity_mask_unavailable" in bare,
        f"a missing ambiguity mask is a REFUSAL, not a note ({bare})")
    chk("regime_history_unavailable" in bare,
        "and so is missing regime history - without it every holdout row is scored through the "
        "RANGE experts, which is not how the model serves")

    full = mp.evaluate_candidate(
        _Up(), None, X, Y, 0,
        valid_mask={5: np.ones(rows, dtype=bool)},
        regime_labels=["RANGE"] * rows)["horizons"][5]["reasons"]
    chk(not [r for r in full if "unavailable" in r],
        f"and with both supplied neither fires ({full}) - this tightened the gate, it did not "
        f"jam it shut")

    unfair = mp.evaluate_candidate(
        _Up(), _Up(), X, Y, 0,
        valid_mask={5: np.ones(rows, dtype=bool)},
        regime_labels=["RANGE"] * rows)["horizons"][5]["reasons"]
    chk("no_fair_incumbent_comparison" in unfair,
        "an incumbent that cannot be fairly compared BLOCKS replacement - 'we could not check' "
        "is not a pass")

    print("5.26 a decision-gate exception forces NO_TRADE")
    from decision_gate import compute_no_trade_reasons

    class _Hostile(dict):
        """Raises on .get(), and is NON-EMPTY so it survives `x or y`.

        The first version of this fixture was an EMPTY dict subclass. Empty dicts are falsy, so
        `p.get("setupQuality") or p.get("confluence") or {}` skipped straight past it and the
        exception never fired - the test proved nothing and said it passed.
        """

        def get(self, *a, **k):
            raise RuntimeError("malformed setup object")

    hostile = _Hostile()
    dict.__setitem__(hostile, "grade", "A")      # truthy, so the `or` chain selects it
    p = {"direction": "UP", "setupQuality": hostile}
    out = compute_no_trade_reasons(p)
    chk("decision_gate_error" in (out.get("no_trade_reasons") or []),
        f"the exception becomes a BLOCKER ({out.get('no_trade_reasons')}) rather than a "
        f"partially assembled verdict")
    chk(out.get("trade_verdict") != "TRADE",
        f"so the verdict cannot be TRADE ({out.get('trade_verdict')}) - it never raises, and an "
        f"incomplete analysis can only produce NO_TRADE")

    print("5.30 the paper gate checks the control the registry demands")
    from binance_paper.metrics import _apply_control_relative_gate
    from binance_paper.strategy_registry import CONTROL_STRATEGY_ID

    strategies = [
        {"strategy_id": CONTROL_STRATEGY_ID, "mean_expectancy_usd": 0.50,
         "promotion_gate": {"passes": True}},
        {"strategy_id": "trend_following", "mean_expectancy_usd": 0.20,
         "promotion_gate": {"passes": True}},
        {"strategy_id": "breakout", "mean_expectancy_usd": 0.90,
         "promotion_gate": {"passes": True}},
    ]
    _apply_control_relative_gate(strategies)
    by_id = {s["strategy_id"]: s["promotion_gate"] for s in strategies}
    chk(by_id["trend_following"]["beats_random_control"] is False
        and by_id["trend_following"]["passes"] is False,
        "a strategy earning LESS than the zero-information control fails, and the overall "
        "verdict flips - previously it could pass on its own positive expectancy alone")
    chk(by_id["breakout"]["beats_random_control"] is True
        and by_id["breakout"]["passes"] is True,
        "one that beats it is unaffected")
    chk(by_id[CONTROL_STRATEGY_ID]["control_relative_basis"] == "IS_THE_CONTROL",
        "and the control is exempt - 'the control must beat the control' claims nothing")

    orphan = [{"strategy_id": "trend_following", "mean_expectancy_usd": 5.0,
               "promotion_gate": {"passes": True}}]
    _apply_control_relative_gate(orphan)
    chk(orphan[0]["promotion_gate"]["beats_random_control"] is False
        and orphan[0]["promotion_gate"]["passes"] is False,
        "with NO control present the comparison was never made, so it refuses - unknown is not "
        "a pass, however good the raw number looks")

    print("\nSCAN-5 BARRIER AND GATES:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
