"""
backend.decision — the live execution-decision layer (Stage-2, rule-based).
============================================================================
This package sits ON TOP of the prediction heads. It does NOT predict and it does
NOT require retraining the 136-feature ensemble. It consumes head outputs
(P(big_move), P(tradable), P(fail_fast)) + microstructure state (basis/CVD/VPIN) +
an expected-move/expected-cost estimate, and decides ACTIONABILITY + TIER:

    NO_TRADE  — a gate failed outright (weak selectivity, bad feed/spread)
    WATCH     — statistical edge exists but it is NOT economically tradable yet
    T1/T2/T3  — every gate agrees (T3 only once maker-fill is live-shadow proven)

Why a SEPARATE package (not threaded into server.py yet): it is offline-safe,
unit-testable, and reuses the already-validated `rules.microstructure_side_engine`
for SIDE selection. Phase 15/16 proved the bottleneck is execution economics, not
prediction — so the highest-leverage layer is this deterministic gate ladder, not
another model. See docs: feature_finding/docs/phase_16_execution_redesign_results.md.

Build stage (STRATEGY_VOLATILITY_TIMING_AND_ENSEMBLES §Staged build): this is
"Stage 2 — RULE-based composer". A meta-stacker (Stage 3) is intentionally NOT here
— it is only justified after >=500 resolved LIVE-shadow actionable samples.
"""
from .cost_gate import cost_gate, expected_cost_bps, MIN_MOVE_COST_RATIO, EXEC_BASE_COST_BPS
from .decision_composer import compose_decision, DecisionContext, DecisionResult
from .event_exits import should_exit, ExitContext
from .drift_monitor import recommend_cap, TierHealth

__all__ = [
    "recommend_cap",
    "TierHealth",
    "cost_gate",
    "expected_cost_bps",
    "MIN_MOVE_COST_RATIO",
    "EXEC_BASE_COST_BPS",
    "compose_decision",
    "DecisionContext",
    "DecisionResult",
    "should_exit",
    "ExitContext",
]
