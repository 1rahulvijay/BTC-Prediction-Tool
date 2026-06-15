"""
cost_gate.py — the hard economic gate (Phase 16's central, non-negotiable rule).
================================================================================
Phase 16 proved the 5m signal edge break-evens at EXACTLY 0 bps execution cost:
gross EV is +0.04 bps, so 2 bps of taker cost already makes it negative. The only
math that survives is reducing cost (maker) AND only trading when the expected move
dwarfs the cost. That rule, validated across the cost-sensitivity + MFE-exit probes:

    Expected Move / Execution Cost  >=  2.5    → economically tradable
    otherwise                                  → WATCH only (edge real, cost too high)

Worked examples (Phase 16):
    cost  2 bps → required move >=  5 bps
    cost  7 bps → required move >= 17.5 bps
    cost 14 bps → required move >= 35 bps   (why a 10-bps TP "win" still loses)

This module is PURE (no I/O, no DB, no model). It is the cheapest, highest-leverage
piece of the execution layer and needs NO retraining.
"""
from __future__ import annotations

# Default minimum ratio of expected move to expected cost for a TRADE (vs WATCH).
MIN_MOVE_COST_RATIO = 2.5

# Round-trip BASE execution cost (bps) per execution mode, from Phase 16's three
# tracked modes. These are the FEE/crossing component only; add the live spread
# (slippage proxy) on top via `expected_cost_bps()`.
#   MAKER_MAKER (Mode C): rest on both sides            → 0 bps base
#   MAKER_TAKER (Mode B): rest entry, cross exit        → ~7 bps base
#   TAKER_TAKER (Mode A): cross both sides              → ~14 bps base
EXEC_BASE_COST_BPS = {
    "MAKER_MAKER": 0.0,
    "MAKER_TAKER": 7.0,
    "TAKER_TAKER": 14.0,
}


def expected_cost_bps(exec_mode: str, spread_bps: float = 0.0,
                      extra_slippage_bps: float = 0.0) -> float:
    """Modeled round-trip execution cost in bps for an execution mode.

    cost = base_fee[mode] + spread_component + extra_slippage

    `spread_bps` is the current bid/ask spread (the slippage proxy). For a maker
    resting order the spread is (optimistically) earned, not paid, so it is NOT
    added for MAKER_MAKER; for any taker leg it is a real cost. This is a
    deliberately conservative proxy — the TRUE maker cost (missed fills, adverse
    selection) can only be measured in live shadow, never assumed here.
    """
    base = EXEC_BASE_COST_BPS.get(exec_mode, EXEC_BASE_COST_BPS["TAKER_TAKER"])
    spread_bps = max(0.0, float(spread_bps or 0.0))
    if exec_mode == "MAKER_MAKER":
        spread_component = 0.0          # resting both sides: do not pay the spread
    elif exec_mode == "MAKER_TAKER":
        spread_component = spread_bps   # one taker leg pays ~one spread
    else:                               # TAKER_TAKER
        spread_component = 2.0 * spread_bps
    return base + spread_component + max(0.0, float(extra_slippage_bps or 0.0))


def cost_gate(expected_move_bps: float | None, expected_cost_bps_val: float | None,
              min_ratio: float = MIN_MOVE_COST_RATIO) -> tuple[bool, float, str]:
    """The hard economic gate.

    Returns (passed, ratio, reason).
      passed  — True only if expected_move / expected_cost >= min_ratio.
      ratio   — the computed move/cost ratio (inf when cost <= 0, i.e. true maker).
      reason  — machine-readable string for the rejection-reason analytics.

    Semantics matter for the composer: a FAIL here is NOT a NO_TRADE — the
    statistical edge is real, it is just too expensive. The composer maps a fail
    to WATCH, never to a hard reject.
    """
    move = float(expected_move_bps or 0.0)
    cost = expected_cost_bps_val
    if cost is None or cost <= 0.0:
        # Zero/negative modeled cost = ideal maker fill. Gate passes by construction,
        # but the composer still requires maker-fill to be LIVE-SHADOW proven before T3.
        return True, float("inf"), "zero_or_maker_cost"
    cost = float(cost)
    if move <= 0.0:
        return False, 0.0, "no_expected_move"
    ratio = move / cost
    if ratio >= min_ratio:
        return True, ratio, f"cost_gate_passed_ratio_{ratio:.2f}"
    return False, ratio, f"cost_gate_failed_ratio_{ratio:.2f}"


def required_move_bps(expected_cost_bps_val: float,
                      min_ratio: float = MIN_MOVE_COST_RATIO) -> float:
    """Minimum expected move (bps) needed to clear the gate at a given cost."""
    return max(0.0, float(expected_cost_bps_val or 0.0)) * float(min_ratio)


if __name__ == "__main__":
    # Self-test (offline, no deps). Reproduces Phase 16's worked numbers.
    print("=== cost_gate self-test ===")
    for cost in (0.0, 2.0, 7.0, 14.0):
        need = required_move_bps(cost)
        print(f"cost {cost:>5.1f} bps -> required move >= {need:>5.1f} bps")
    assert required_move_bps(14.0) == 35.0
    assert required_move_bps(2.0) == 5.0

    ok, r, why = cost_gate(40.0, 14.0)          # 40/14 = 2.86x -> pass
    assert ok and r > 2.5, (ok, r, why)
    ok, r, why = cost_gate(20.0, 14.0)          # 20/14 = 1.43x -> WATCH
    assert (not ok) and 1.4 < r < 1.5, (ok, r, why)
    ok, r, why = cost_gate(20.0, 0.0)           # maker -> passes by construction
    assert ok and r == float("inf"), (ok, r, why)
    ok, r, why = cost_gate(0.0, 5.0)            # no move
    assert (not ok) and why == "no_expected_move", (ok, r, why)

    # exec cost model
    assert expected_cost_bps("MAKER_MAKER", spread_bps=3.0) == 0.0
    assert expected_cost_bps("MAKER_TAKER", spread_bps=3.0) == 10.0   # 7 + 3
    assert expected_cost_bps("TAKER_TAKER", spread_bps=3.0) == 20.0   # 14 + 2*3
    print("cost_gate self-test PASSED")
