"""
event_exits.py — event-driven exit rules (Phase 16: fixed-time holds FAIL).
===========================================================================
Phase 16 proved that holding for a fixed 5m/15m/30m/60m makes Net EV WORSE: on the
1m timeframe MAE (adverse drift) grows faster than MFE (favorable drift), so blindly
holding momentum just feeds mean reversion. The fix is to exit on an EVENT, not a
clock. This module encodes those events as pure, deterministic rules.

Checked in priority order (safety first, then profit-taking, then thesis-decay):
  1. hard_stop            — adverse excursion blew through the max stop
  2. mfe_target_reached   — captured the expected favorable move; bank it
  3. opposite_rule_fired  — the side engine now signals the OTHER way
  4. basis_normalized     — the basis extreme that triggered entry has reverted
  5. vpin_resolved        — the VPIN toxicity that triggered entry has cleared
  6. tradability_decay    — P(tradable) fell below the floor (path turned choppy)
  7. move_remaining_low   — P(move_remaining) says the move is largely spent
  8. anchor_cross         — price crossed the VWAP anchor against the position
  9. max_hold_backstop    — absolute safety timeout (NOT the primary exit)

Pure: no DB, no model, no network. Needs NO retraining.

RESEARCH STATUS
    This module is intentionally not exported by ``backend.decision`` and has no live
    caller. Its thresholds have not cleared forward execution gates. Import it explicitly
    only from research tests; it has no paper- or real-capital authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


RESEARCH_ONLY = True
CAPITAL_AUTHORITY = False


@dataclass
class ExitContext:
    side: int                       # +1 long, -1 short
    mfe_bps: float = 0.0            # realized favorable excursion since entry (bps)
    mae_bps: float = 0.0            # realized adverse excursion since entry (bps, >=0)
    target_mfe_bps: float = 35.0   # expected MFE to bank (from expected-move model)
    hard_stop_bps: float = 20.0    # max adverse excursion tolerated

    # Microstructure reversion (only relevant if entry was triggered by that extreme)
    entry_basis_extreme: bool = False
    basis_bps: float = 0.0
    basis_neutral_lo: float = -3.0
    basis_neutral_hi: float = 3.0

    entry_vpin_extreme: bool = False
    vpin: float = 0.0
    vpin_neutral_hi: float = 0.5

    # Thesis decay
    p_tradable: Optional[float] = None
    tradable_floor: float = 0.50
    p_move_remaining: Optional[float] = None
    move_remaining_floor: float = 0.40

    # Side flip + anchor
    opposite_side_fired: bool = False
    anchor_price: Optional[float] = None
    current_price: Optional[float] = None
    entry_above_anchor: Optional[bool] = None

    # Safety backstop
    seconds_held: float = 0.0
    max_hold_sec: float = 1800.0    # 30m hard cap; a backstop, not the strategy


def should_exit(ctx: ExitContext) -> tuple[bool, str]:
    """Returns (should_exit, reason). First triggered event wins."""
    # 1. Hard stop — non-negotiable risk control.
    if ctx.mae_bps >= ctx.hard_stop_bps:
        return True, "hard_stop"

    # 2. Profit target — captured the expected favorable move.
    if ctx.target_mfe_bps > 0 and ctx.mfe_bps >= ctx.target_mfe_bps:
        return True, "mfe_target_reached"

    # 3. The side engine flipped against us.
    if ctx.opposite_side_fired:
        return True, "opposite_rule_fired"

    # 4. The basis extreme that justified entry has reverted to neutral.
    if ctx.entry_basis_extreme and (ctx.basis_neutral_lo <= ctx.basis_bps <= ctx.basis_neutral_hi):
        return True, "basis_normalized"

    # 5. The VPIN toxicity that justified entry has cleared.
    if ctx.entry_vpin_extreme and ctx.vpin < ctx.vpin_neutral_hi:
        return True, "vpin_resolved"

    # 6. Tradability decayed — the path turned choppy.
    if ctx.p_tradable is not None and ctx.p_tradable < ctx.tradable_floor:
        return True, "tradability_decay"

    # 7. The move is largely spent.
    if ctx.p_move_remaining is not None and ctx.p_move_remaining < ctx.move_remaining_floor:
        return True, "move_remaining_low"

    # 8. Price crossed the VWAP anchor against the position.
    if (ctx.anchor_price is not None and ctx.current_price is not None
            and ctx.entry_above_anchor is not None):
        now_above = ctx.current_price > ctx.anchor_price
        if ctx.side > 0 and ctx.entry_above_anchor and not now_above:
            return True, "anchor_cross"
        if ctx.side < 0 and (not ctx.entry_above_anchor) and now_above:
            return True, "anchor_cross"

    # 9. Absolute safety backstop.
    if ctx.seconds_held >= ctx.max_hold_sec:
        return True, "max_hold_backstop"

    return False, "hold"


if __name__ == "__main__":
    print("=== event_exits self-test ===")

    # hard stop fires first even if MFE target also met
    assert should_exit(ExitContext(side=1, mae_bps=25, hard_stop_bps=20,
                                   mfe_bps=40, target_mfe_bps=35)) == (True, "hard_stop")
    # bank the expected move
    assert should_exit(ExitContext(side=1, mfe_bps=36, target_mfe_bps=35)) == (True, "mfe_target_reached")
    # side flip
    assert should_exit(ExitContext(side=-1, opposite_side_fired=True)) == (True, "opposite_rule_fired")
    # basis reverted
    assert should_exit(ExitContext(side=-1, entry_basis_extreme=True, basis_bps=0.0,
                                   basis_neutral_lo=-3, basis_neutral_hi=3)) == (True, "basis_normalized")
    # vpin cleared
    assert should_exit(ExitContext(side=1, entry_vpin_extreme=True, vpin=0.2,
                                   vpin_neutral_hi=0.5)) == (True, "vpin_resolved")
    # tradability decay
    assert should_exit(ExitContext(side=1, p_tradable=0.3, tradable_floor=0.5)) == (True, "tradability_decay")
    # anchor cross (long entered above anchor, now below)
    assert should_exit(ExitContext(side=1, anchor_price=100.0, current_price=99.0,
                                   entry_above_anchor=True)) == (True, "anchor_cross")
    # nothing fires -> hold
    assert should_exit(ExitContext(side=1, mfe_bps=5, mae_bps=5, target_mfe_bps=35,
                                   p_tradable=0.8, p_move_remaining=0.9)) == (False, "hold")
    print("event_exits self-test PASSED")
