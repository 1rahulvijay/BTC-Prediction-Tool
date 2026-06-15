"""
drift_monitor.py — tier downgrade on live calibration drift (Codex plan #8, pure).
==================================================================================
Codex/Phase-16 rule: when live results drift from offline expectations, do NOT
retrain first — DOWNGRADE the tier. The decision layer should make itself humbler
automatically: T3 → T2 → T1 → WATCH → NO_TRADE as confidence in the live edge erodes.

This module is pure (no DB, no model, no network). It takes rolling LIVE metrics for a
tier and returns the recommended cap + reason. It does not act on its own — the live
loop (deferred) would feed it resolved-shadow stats and apply the cap.
"""
from __future__ import annotations

from dataclasses import dataclass

# Tier ladder, strongest → weakest.
_LADDER = ["T3", "T2", "T1", "WATCH", "NO_TRADE"]


def _downgrade(tier: str, steps: int = 1) -> str:
    if tier not in _LADDER:
        return "WATCH"
    return _LADDER[min(len(_LADDER) - 1, _LADDER.index(tier) + max(0, steps))]


@dataclass
class TierHealth:
    tier: str                       # the tier being assessed
    n_resolved: int = 0             # resolved live-shadow samples for this tier
    live_win_rate: float = 0.0      # realized win rate (after costs)
    offline_win_rate: float = 0.0   # expected win rate from the offline scorecard
    live_ev_bps: float = 0.0        # realized net EV (bps, maker)
    fakeout_rate: float = 0.0       # share that reversed hard right after entry
    calib_abs_error: float = 0.0    # |live - offline| calibration gap on the gate prob

    # tolerances (overridable)
    min_n: int = 100                # below this, not enough evidence → hold one step down
    win_drop_tol: float = 0.05      # live may trail offline by this before a downgrade
    ev_floor_bps: float = 0.0       # live EV must clear this (maker)
    fakeout_tol: float = 0.40       # above this fakeout share → downgrade
    calib_tol: float = 0.10         # calibration gap above this → downgrade


def recommend_cap(h: TierHealth) -> tuple[str, str]:
    """Return (capped_tier, reason). Conservative: any breach steps the tier down."""
    if h.n_resolved < h.min_n:
        return _downgrade(h.tier, 1), f"insufficient_evidence_n{h.n_resolved}"
    if h.live_ev_bps < h.ev_floor_bps:
        return _downgrade(h.tier, 2), f"negative_live_ev_{h.live_ev_bps:.2f}bps"
    if (h.offline_win_rate - h.live_win_rate) > h.win_drop_tol:
        return _downgrade(h.tier, 1), "win_rate_drift"
    if h.fakeout_rate > h.fakeout_tol:
        return _downgrade(h.tier, 1), "fakeout_rate_high"
    if h.calib_abs_error > h.calib_tol:
        return _downgrade(h.tier, 1), "calibration_drift"
    return h.tier, "healthy"


if __name__ == "__main__":
    print("=== drift_monitor self-test ===")
    # healthy T3 stays T3
    assert recommend_cap(TierHealth("T3", n_resolved=300, live_win_rate=0.60,
                                    offline_win_rate=0.61, live_ev_bps=3.0)) == ("T3", "healthy")
    # thin evidence → one step down
    assert recommend_cap(TierHealth("T3", n_resolved=20)) == ("T2", "insufficient_evidence_n20")
    # negative live EV → two steps down
    cap, why = recommend_cap(TierHealth("T3", n_resolved=300, live_win_rate=0.6,
                                        offline_win_rate=0.6, live_ev_bps=-2.0))
    assert cap == "T1" and "negative_live_ev" in why, (cap, why)
    # win-rate drift → one step
    assert recommend_cap(TierHealth("T2", n_resolved=300, live_win_rate=0.40,
                                    offline_win_rate=0.55, live_ev_bps=1.0))[0] == "T1"
    # fakeouts → one step
    assert recommend_cap(TierHealth("T2", n_resolved=300, live_win_rate=0.55,
                                    offline_win_rate=0.55, live_ev_bps=1.0,
                                    fakeout_rate=0.5))[0] == "T1"
    print("drift_monitor self-test PASSED")
