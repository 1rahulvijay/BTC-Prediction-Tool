"""
decision_composer.py — the live, single-tick decision ladder (Stage-2, rule-based).
===================================================================================
Turns the head outputs + microstructure state + economics into ONE honest decision:

    NO_TRADE  — a hard gate failed (bad feed/spread, weak selectivity)
    WATCH     — edge is real but not economically tradable, or no side rule fired
    T1/T2/T3  — every gate agrees (T3 only when maker-fill is LIVE-SHADOW proven)

Design rules (from the research, not invented here):
  • SIDE comes ONLY from the validated `rules.microstructure_side_engine` (fade basis
    in chop, follow basis in breakout, VPIN trap). XGBoost side-selection FAILED
    out-of-sample (Phase 14) — never reintroduce an ML side model here.
  • A cost-gate FAIL is WATCH, not NO_TRADE — the statistical edge still exists.
  • T3 is GATED on `maker_fill_proven` (default False). Until live shadow proves a
    limit order fills without adverse selection, the top live tier is capped at
    T2_SHADOW. This is the Phase 16 verdict encoded as a hard rule.
  • This module is PURE (no DB, no model, no network) and needs NO retraining.

It mirrors the Codex `final_decision(ctx)` spec but reuses the already-validated
side engine rather than duplicating its thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    # Works when `backend/` is on PYTHONPATH (the app sets this).
    from rules.microstructure_side_engine import get_side_signal
except Exception:  # pragma: no cover - fallback for direct execution
    import os
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from rules.microstructure_side_engine import get_side_signal

try:
    from .cost_gate import cost_gate, expected_cost_bps, MIN_MOVE_COST_RATIO
except ImportError:  # pragma: no cover - direct script execution
    import os
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from cost_gate import cost_gate, expected_cost_bps, MIN_MOVE_COST_RATIO


# Default gate floors (overridable per-call via DecisionContext). Grounded in the
# research: selectivity floor = top-5% selectivity (engine default 0.95), tradability
# floor 0.50 within the high-vol regime, fail-fast soft-downgrade at 0.60.
SELECTIVITY_FLOOR = 0.95
TRADABILITY_FLOOR = 0.70          # Codex live floor (stricter than the engine's 0.50 split)
FAIL_FAST_SOFT = 0.60
MOVE_REMAINING_T3 = 0.70


@dataclass
class DecisionContext:
    """Everything the composer needs for ONE window/tick. Percentile thresholds for
    basis/CVD/VPIN should be precomputed from rolling history and passed in (the
    engine also accepts raw values if you pass *_extreme as absolute thresholds)."""
    # Head probabilities
    p_big_move: float = 0.0
    p_tradable: float = 0.0
    p_fail_fast: float = 0.0
    p_move_remaining: Optional[float] = None

    # Microstructure state + their extreme thresholds (absolute values, precomputed)
    basis_bps: float = 0.0
    cvd_div: float = 0.0
    vpin: float = 0.0
    basis_up: float = 95.0
    basis_dn: float = 5.0
    cvd_up: float = 95.0
    cvd_dn: float = 5.0
    vpin_up: float = 95.0
    vpin_dn: float = 5.0

    # Economics
    expected_move_bps: float = 0.0
    exec_mode: str = "MAKER_MAKER"     # MAKER_MAKER | MAKER_TAKER | TAKER_TAKER
    spread_bps: float = 0.0
    expected_cost_bps_override: Optional[float] = None  # if you already modeled cost

    # Integrity / live-readiness gates
    feed_stale: bool = False
    spread_too_wide: bool = False
    maker_fill_proven: bool = False    # only True after live-shadow proves fills
    live_calibration_ok: bool = False  # only True after live calibration holds

    # Floors (overridable)
    selectivity_floor: float = SELECTIVITY_FLOOR
    tradability_floor: float = TRADABILITY_FLOOR
    fail_fast_soft: float = FAIL_FAST_SOFT
    min_move_cost_ratio: float = MIN_MOVE_COST_RATIO


@dataclass
class DecisionResult:
    action: str                 # NO_TRADE | WATCH | T1 | T2 | T2_SHADOW | T3
    side: str                   # LONG | SHORT | NO_SIDE
    tier: str                   # raw tier from the side engine (T0..T3)
    reason: str                 # primary rejection/decision reason (machine-readable)
    move_cost_ratio: float = 0.0
    expected_cost_bps: float = 0.0
    side_reason: str = ""       # which microstructure rule fired
    diagnostics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def compose_decision(ctx: DecisionContext) -> DecisionResult:
    """The deterministic gate ladder. Order matters — each gate explains itself."""
    cost = (ctx.expected_cost_bps_override
            if ctx.expected_cost_bps_override is not None
            else expected_cost_bps(ctx.exec_mode, ctx.spread_bps))
    passed_cost, ratio, cost_reason = cost_gate(
        ctx.expected_move_bps, cost, ctx.min_move_cost_ratio)

    diag = {
        "p_big_move": ctx.p_big_move, "p_tradable": ctx.p_tradable,
        "p_fail_fast": ctx.p_fail_fast, "p_move_remaining": ctx.p_move_remaining,
        "expected_move_bps": ctx.expected_move_bps, "expected_cost_bps": cost,
        "move_cost_ratio": ratio, "exec_mode": ctx.exec_mode,
        "maker_fill_proven": ctx.maker_fill_proven,
        "live_calibration_ok": ctx.live_calibration_ok,
    }

    def result(action, side, tier, reason, side_reason=""):
        return DecisionResult(action=action, side=side, tier=tier, reason=reason,
                              move_cost_ratio=round(ratio, 3) if ratio != float("inf") else ratio,
                              expected_cost_bps=round(cost, 3), side_reason=side_reason,
                              diagnostics=diag)

    # 1. Feed / spread integrity — hard NO_TRADE.
    if ctx.feed_stale:
        return result("NO_TRADE", "NO_SIDE", "T0", "feed_stale")
    if ctx.spread_too_wide:
        return result("NO_TRADE", "NO_SIDE", "T0", "spread_too_wide")

    # 2. Selectivity floor — below it, the window is not worth predicting.
    if ctx.p_big_move < ctx.selectivity_floor:
        return result("NO_TRADE", "NO_SIDE", "T0", "weak_selectivity")

    # 3. SIDE from the validated deterministic engine (never an ML side model).
    side, tier, side_reason = get_side_signal(
        ctx.p_big_move, ctx.p_tradable, ctx.p_fail_fast,
        ctx.basis_bps, ctx.cvd_div, ctx.vpin,
        t1_selectivity_thresh=ctx.selectivity_floor,
        fail_fast_thresh=ctx.fail_fast_soft,
        basis_extreme_pct_up=ctx.basis_up, basis_extreme_pct_down=ctx.basis_dn,
        cvd_extreme_pct_up=ctx.cvd_up, cvd_extreme_pct_down=ctx.cvd_dn,
        vpin_extreme_pct_up=ctx.vpin_up, vpin_extreme_pct_down=ctx.vpin_dn,
    )
    if side == "NO_SIDE":
        return result("WATCH", side, tier, "no_side_rule", side_reason)

    # 4. Cost gate — FAIL is WATCH (edge real, too expensive), not a hard reject.
    if not passed_cost:
        return result("WATCH", side, tier, cost_reason, side_reason)

    # 5. Tradability floor — clean path required for an actionable tier.
    if ctx.p_tradable < ctx.tradability_floor:
        return result("WATCH", side, tier, "low_tradability", side_reason)

    # 6. Invalidation soft-downgrade — weak path caps the tier at T1.
    if ctx.p_fail_fast > ctx.fail_fast_soft:
        return result("T1", side, tier, "fail_fast_soft_downgrade", side_reason)

    # 7. Maker-fill gate — until live shadow proves fills, cap below T3.
    if not ctx.maker_fill_proven:
        return result("T2_SHADOW", side, tier, "maker_fill_unproven", side_reason)

    # 8. Top tier — all gates pass AND live calibration holds AND move has room left.
    if (ctx.live_calibration_ok and tier == "T3"
            and (ctx.p_move_remaining is None or ctx.p_move_remaining >= MOVE_REMAINING_T3)):
        return result("T3", side, tier, "all_gates_passed", side_reason)

    return result("T2", side, tier, "partial_high_quality", side_reason)


if __name__ == "__main__":
    print("=== decision_composer self-test ===")

    # A: bad feed -> NO_TRADE
    r = compose_decision(DecisionContext(feed_stale=True, p_big_move=0.99))
    assert r.action == "NO_TRADE" and r.reason == "feed_stale", r

    # B: weak selectivity -> NO_TRADE
    r = compose_decision(DecisionContext(p_big_move=0.40))
    assert r.action == "NO_TRADE" and r.reason == "weak_selectivity", r

    # C: strong selectivity + extreme basis in CHOP (p_tradable low) -> side fires,
    #    taker cost too high vs small move -> WATCH cost_gate_failed
    r = compose_decision(DecisionContext(
        p_big_move=0.97, p_tradable=0.20, basis_bps=10.0, basis_up=5.0, basis_dn=-5.0,
        expected_move_bps=20.0, exec_mode="TAKER_TAKER", spread_bps=0.0))
    assert r.action == "WATCH" and "cost_gate_failed" in r.reason and r.side == "SHORT", r

    # D: clean breakout, maker cost, tradable, but maker-fill NOT proven -> T2_SHADOW
    r = compose_decision(DecisionContext(
        p_big_move=0.995, p_tradable=0.85, basis_bps=10.0, basis_up=5.0, basis_dn=-5.0,
        expected_move_bps=40.0, exec_mode="MAKER_MAKER", maker_fill_proven=False))
    assert r.action == "T2_SHADOW" and r.reason == "maker_fill_unproven", r

    # E: same but maker-fill proven + calibration ok + T3 selectivity -> T3
    r = compose_decision(DecisionContext(
        p_big_move=0.999, p_tradable=0.90, basis_bps=10.0, basis_up=5.0, basis_dn=-5.0,
        expected_move_bps=40.0, exec_mode="MAKER_MAKER",
        maker_fill_proven=True, live_calibration_ok=True, p_move_remaining=0.80))
    assert r.action in ("T2", "T3"), r   # T3 requires the engine to label tier T3

    print("decision_composer self-test PASSED")
