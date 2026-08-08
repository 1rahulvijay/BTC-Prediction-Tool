"""
Scan-6: five defects, three of them introduced by earlier fixes in this same audit series.

D1  Replay multiplied the neutral band by the entry price before handing it to a grader that
    wants a FRACTION. A declared 8bps band arrived as 80, barriers became +-8,000,000, and
    every row timed out to NEUTRAL - while the status still read GRADED_FIRST_TOUCH.

D2  Replay graded `actual_dir` through the contract and then computed its headline
    `direction_hit` from the endpoint move sign. Two truths in one row.

D5  The executed-training identity was recorded and never enforced.

D6  ...and the flag D5 would enforce compares a NumPy tensor digest against a Parquet FILE
    digest. Those never agree, so the flag was structurally False and enforcing it would have
    rejected every honest retrain. D5 and D6 have to be fixed together or not at all.

D34 The control-relative promotion gate wrote its criterion into the wrong dict and then
    fail-closed on keys the gate does not have. Recorded, never enforced - the exact defect
    that function was written to remove.

Run directly:  python backend/test_scan6_replay_identity_control.py
"""

import pathlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _bars(entry, n=6, hi=1.0015, lo=0.9993, close=1.001):
    base = 1_800_000_000_000
    return [{"time": (base + i * 60_000) // 1000, "open": entry, "high": entry * hi,
             "low": entry * lo, "close": entry * close, "is_closed": True}
            for i in range(n)]


def test_replay_band_units():
    print("\nD1 the replay grader is handed a FRACTION, because that is what it takes")
    import target_contract as tc
    from historical_replay import _graded_direction

    entry, band = 100_000.0, 0.0008
    direction, status = _graded_direction(entry, band, _bars(entry),
                                          1_800_000_000_000, 1_800_000_000_000 + 300_000)
    chk(direction == "UP" and status == "GRADED_FIRST_TOUCH",
        "a path that touches the upper barrier grades UP")

    # What the product did, reproduced against the grader directly.
    wrong = tc.grade(contract=tc.TRAINING_CONTRACT, entry=entry, threshold=entry * band,
                     klines=_bars(entry), entry_ts=1_800_000_000_000,
                     verify_ts=1_800_000_000_000 + 300_000)
    chk(wrong.direction == "NEUTRAL",
        f"whereas entry*band (={entry * band:.0f}) grades the SAME path {wrong.direction} - "
        f"first_touch_at builds entry*(1 +/- threshold), so the barriers became "
        f"[{entry * (1 - entry * band):,.0f} .. {entry * (1 + entry * band):,.0f}] and nothing "
        f"could ever touch them")
    chk(wrong.status == "GRADED_FIRST_TOUCH",
        "and it still reported GRADED_FIRST_TOUCH - the collapse was silent, which is why it "
        "survived: replay claimed first-touch grading and returned a constant")

    src = (BACKEND / "historical_replay.py").read_text(encoding="utf-8")
    chk("threshold = entry_price * float(neutral_band" not in src,
        "the multiplication is gone")
    chk("threshold = _tc.resolve_neutral_band(neutral_band)" in src,
        "and the band goes through the one resolver, so a declared zero also survives")


def test_replay_direction_hit():
    print("\nD2 replay's headline hit is the CONTRACT's, not the endpoint sign")
    import ast
    src = (BACKEND / "historical_replay.py").read_text(encoding="utf-8")
    # BY NAME. A substring match picked `_finalize_replay_prediction`, which has no
    # direction_hit at all, so the assertion failed against the wrong subject.
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "run_replay")
    body = "\n".join(ast.unparse(x) for x in fn.body)
    chk("direction_hit = bool(raw_dir == actual_dir)" in body,
        "direction_hit compares against the graded direction")
    chk("(raw_dir == 'UP') == (actual_move > 0)" not in body,
        "and the endpoint-sign form is gone - a row that graded UP by first touch and closed "
        "lower was previously counted BOTH ways in the same record")


def test_executed_identity():
    print("\nD6 two hash domains cannot agree, and the identity says so")
    import numpy as np
    from artifact_identity import current_training_identity, executed_training_identity

    X = np.arange(6000, dtype=np.float32).reshape(100, 60)
    ex = executed_training_identity(X, {5: np.zeros(100, dtype=np.int64)})
    ident = current_training_identity(requested_days=60, feature_names=["a", "b"], executed=ex)

    chk(ident.get("executed_matches_matrix") is None,
        "the flag is None - NOT COMPARABLE - rather than a False that only restates that a "
        "tensor digest is not a file digest")
    chk("HASH_DOMAINS_NOT_COMPARABLE" in str(ident.get("executed_matrix_comparison_basis")),
        f"with the reason attached: {ident.get('executed_matrix_comparison_basis')}")
    chk(ident.get("executed_rows_match_matrix_rows") in (True, False),
        "and the part that IS comparable - row counts - is still reported")

    print("\nD5 the executed identity is now enforced for what it can prove")
    # CODE ONLY. `inspect.getsource` carries the comments, and the comment explaining WHY
    # the incomparable flag is not enforced necessarily names it - so a raw-text assertion
    # matches the fix's own description of what it refuses to do. Same trap, fourth time.
    import ast as _ast
    from artifact_identity import artifact_compatibility
    _mod = _ast.parse((BACKEND / "artifact_identity.py").read_text(encoding="utf-8"))
    _fn = next(n for n in _ast.walk(_mod)
               if isinstance(n, _ast.FunctionDef)
               and n.name == artifact_compatibility.__name__)
    code = "\n".join(_ast.unparse(x) for x in _fn.body)
    # BEHAVIOURAL, not textual. A source assertion survives a mutation that guts the `if`
    # while leaving the string in the reason text.
    import json as _json, shutil as _shutil, tempfile as _tempfile
    from artifact_identity import artifact_manifest_path
    _d = pathlib.Path(_tempfile.mkdtemp(prefix="scan6_ident_"))
    try:
        _man = {"artifact_type": "multi_model_ensemble", "artifact_files": [],
                "requested_days": 60}
        artifact_manifest_path(_d).write_text(_json.dumps(_man), encoding="utf-8")
        ok_no_rec, why_no_rec = artifact_compatibility(_d, {"requested_days": 60}, strict=True)
        chk(ok_no_rec is False
            and any("executed_identity_recorded" in r for r in why_no_rec),
            f"a strict load REFUSES a bundle that cannot say what it was fitted on: "
            f"{[r for r in why_no_rec if 'executed' in r]}")

        _man["executed_identity_recorded"] = True
        artifact_manifest_path(_d).write_text(_json.dumps(_man), encoding="utf-8")
        _ok2, why2 = artifact_compatibility(_d, {"requested_days": 60}, strict=True)
        chk(not any("executed_identity_recorded" in r for r in why2),
            "and stops objecting once the bundle records it")
    finally:
        _shutil.rmtree(_d, ignore_errors=True)
    chk("executed_matches_matrix" not in code,
        "and it deliberately does NOT enforce the incomparable flag - doing so would reject "
        "every honest retrain, which is why D5 and D6 had to be fixed together")


def test_control_gate_enforces():
    print("\nD34 the control-relative criterion decides the verdict")
    from binance_paper.metrics import _apply_control_relative_gate
    from binance_paper.strategy_registry import CONTROL_STRATEGY_ID

    def gate():
        return {"status": "FORWARD_GATE_PASSED_PAPER_ONLY", "checks": {"a": True, "b": True}}

    rows = [
        {"strategy_id": CONTROL_STRATEGY_ID, "mean_expectancy_usd": 5.0, "promotion_gate": gate()},
        {"strategy_id": "loser", "mean_expectancy_usd": 1.0, "promotion_gate": gate()},
        {"strategy_id": "winner", "mean_expectancy_usd": 9.0, "promotion_gate": gate()},
    ]
    _apply_control_relative_gate(rows)
    by_id = {r["strategy_id"]: r["promotion_gate"] for r in rows}

    chk(by_id["loser"]["checks"].get("beats_random_control") is False
        and by_id["loser"]["status"] == "BLOCKED_FAILED_GATE",
        "a strategy that loses to a zero-information control is BLOCKED - the fail-close used "
        "to look for `passes`/`eligible`/`promotable`/`ready`, none of which this gate has, so "
        "it was a no-op and the loser still read PASSED")
    chk(by_id["winner"]["status"] == "FORWARD_GATE_PASSED_PAPER_ONLY",
        "and one that beats it is unaffected")
    chk(by_id[CONTROL_STRATEGY_ID]["checks"].get("beats_random_control") is True,
        "the control is exempt - 'the control must beat the control' claims nothing")
    chk("beats_random_control" in by_id["winner"]["checks"],
        "the criterion lives in `checks`, which is what the verdict is computed from - it "
        "used to land beside `status` because `gate.get('criteria')` fell back to the gate "
        "itself and there is no `criteria` key")

    orphan = [{"strategy_id": "alone", "mean_expectancy_usd": 9.0, "promotion_gate": gate()}]
    _apply_control_relative_gate(orphan)
    g = orphan[0]["promotion_gate"]
    chk(g["checks"]["beats_random_control"] is False
        and g["status"] == "BLOCKED_FAILED_GATE"
        and g["checks"]["control_relative_basis"] == "CONTROL_UNAVAILABLE",
        "and a missing control BLOCKS rather than passes: the comparison the registry "
        "requires was never made")


def test_risk_scales_with_capital():
    print("\nD31 risk limits are fractions of the CONFIGURED bankroll")
    from binance_paper.config import StrategyRiskConfig

    for cash, expect_pos in ((250.0, 25.0), (10_000.0, 1_000.0), (1_000_000.0, 100_000.0)):
        r = StrategyRiskConfig.for_capital(cash)
        chk(abs(r.max_position_notional_usd - expect_pos) < 1e-6,
            f"${cash:,.0f} bankroll -> ${r.max_position_notional_usd:,.2f} position cap "
            f"({r.max_position_notional_usd / cash:.0%})")

    default_r = StrategyRiskConfig()
    chk(default_r.max_position_notional_usd == 25.0,
        "the dataclass default stays tied to the $250 stake, which is correct FOR that stake")
    chk(StrategyRiskConfig.for_capital(10_000.0).max_position_notional_usd
        > default_r.max_position_notional_usd,
        "but a larger bankroll gets larger limits - the previous fix replaced 'absolute "
        "dollars that do not scale down' with 'fractions of a constant that do not scale up', "
        "which is the same defect pointing the other way")

    reg = (BACKEND / "binance_paper" / "strategy_registry.py").read_text(encoding="utf-8")
    chk("StrategyRiskConfig.for_capital(starting_cash_usd)" in reg,
        "and the registry binds them where the real starting capital is known")


def main():
    print("=" * 78)
    print("SCAN-6: REPLAY UNITS, EXECUTED IDENTITY, CONTROL GATE, CAPITAL SCALING")
    print("=" * 78)
    test_replay_band_units()
    test_replay_direction_hit()
    test_executed_identity()
    test_control_gate_enforces()
    test_risk_scales_with_capital()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"SCAN-6: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("SCAN-6: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
