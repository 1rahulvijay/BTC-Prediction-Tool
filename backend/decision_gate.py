"""
decision_gate.py — the "do-not-trade reason engine" (no-train, pure, testable).
================================================================================
Turns the app into the ruthless ABSTENTION machine both our analysis and the Codex review
converge on: for every finalized prediction, assemble a STRUCTURED list of WHY it is not a
trade (or confirm it is), from flags the gates already computed. Surfacing "why not" builds
trust more than any extra model — and it makes the default state (NO TRADE) explicit.

Pure + crash-safe: reads existing prediction flags, never raises, no heavy deps — so it is
unit-tested standalone and cannot destabilise the serving loop (the §5av lesson).

Verdict (3-state): NO_TRADE (neutralised / no side) | WEAK_LEAN (a committed side but with
caveats) | TRADE (committed, actionable, no blocking reason). Most ticks should be NO_TRADE.
"""

REASON_LABELS = {
    # HARD blocks — the exact codes apply_live_quality_filters + the expectancy filter emit via
    # _neutralize_prediction (server.py). Keep these in sync with those call sites.
    "stale_feed": "order-flow feed stale/disconnected",
    "model_confusion": "models in high disagreement (ensemble entropy)",
    "poor_regime": "regime cell has no proven edge (<50% historically)",
    "low_confidence": "confidence below the safety bar",
    "meta_reject": "meta-model trust filter blocked the signal",
    "negative_expectancy": "negative expected value after costs",
    "wide_target_range": "expected move range too wide / target too uncertain",
    # SOFT reasons (assembled here from existing flags)
    "fallback_lean_only": "fallback lean only — model is neutral, side from prob tilt (~coin-flip)",
    "weak_conviction_capped": "conviction below the proven-edge threshold (B2 cap)",
    "grade_unproven": "setup grade not a trust signal yet (currently inverted, §5br)",
    "p_hold_low": "P(hold) below the late-entry threshold",
    "not_actionable": "did not clear the actionable gate",
}


def compute_no_trade_reasons(p: dict) -> dict:
    """Attach `no_trade_reasons` (codes), `no_trade_reason_text` (human), and `trade_verdict`
    to a finalized prediction dict. Mutates + returns p. Never raises."""
    reasons = []
    try:
        # 1. Hard block already applied by apply_live_quality_filters (stale_feed/low_ev/...)
        code = p.get("neutralReasonCode")
        if code:
            reasons.append(code)
        direction = p.get("direction")
        raw = p.get("rawDirection", direction or "NEUTRAL")
        # 2. Fallback lean: committed 3-class is NEUTRAL, the shown side is the two-way prob tilt
        #    only — measured ~coin-flip (the mirror evidence). A real skip.
        if raw not in ("UP", "DOWN") and direction in ("UP", "DOWN"):
            reasons.append("fallback_lean_only")
        # 3. B2 conviction cap (50-54% cells kept read-only)
        if p.get("convictionCapped"):
            reasons.append("weak_conviction_capped")
        # 4. Grade not yet a trust signal (inverted, §5br) — only flag if a grade is shown
        cfl = p.get("setupQuality") or p.get("confluence") or {}
        if cfl.get("grade") in ("A", "B", "C") and not cfl.get("grade_validated"):
            reasons.append("grade_unproven")
        # 5. Generic not-actionable (only if a side exists and no harder reason already covers it)
        _hard = ("stale_feed", "model_confusion", "poor_regime", "low_confidence",
                 "meta_reject", "negative_expectancy", "wide_target_range",
                 "weak_conviction_capped")
        if (not p.get("actionable")) and direction in ("UP", "DOWN") \
                and not any(r in reasons for r in _hard):
            reasons.append("not_actionable")
    except Exception as exc:
        # FAIL CLOSED. This was `pass`, and execution then fell through to verdict construction
        # with a PARTIALLY assembled reason list - so a malformed setup object produced a
        # verdict computed from fewer blockers than actually applied, in a function whose
        # docstring promises it "never raises".
        #
        # Never raising is still honoured: the exception becomes a blocker rather than a
        # traceback, and an incomplete analysis can only ever produce NO_TRADE.
        reasons.append("decision_gate_error")
        p["decision_gate_error"] = f"{type(exc).__name__}: {exc}"[:200]

    # de-dup, preserve order
    seen, ordered = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r); ordered.append(r)
    p["no_trade_reasons"] = ordered
    p["no_trade_reason_text"] = [REASON_LABELS.get(r, r) for r in ordered]

    if p.get("direction") not in ("UP", "DOWN"):
        p["trade_verdict"] = "NO_TRADE"
    elif p.get("actionable") and not ordered:
        p["trade_verdict"] = "TRADE"
    else:
        p["trade_verdict"] = "WEAK_LEAN"
    return p


def _selftest():
    # neutralised (stale feed) -> NO_TRADE, reason captured
    p = compute_no_trade_reasons({"direction": "NEUTRAL", "neutralReasonCode": "stale_feed"})
    assert p["trade_verdict"] == "NO_TRADE" and "stale_feed" in p["no_trade_reasons"]

    # fallback lean (rawDirection NEUTRAL but a side shown) -> WEAK_LEAN + fallback reason
    p = compute_no_trade_reasons({"direction": "UP", "rawDirection": "NEUTRAL", "actionable": False})
    assert "fallback_lean_only" in p["no_trade_reasons"] and p["trade_verdict"] == "WEAK_LEAN"

    # clean committed actionable model lean, validated grade -> TRADE, no reasons
    p = compute_no_trade_reasons({"direction": "UP", "rawDirection": "UP", "actionable": True,
                                  "confluence": {"grade": "B", "grade_validated": True}})
    assert p["trade_verdict"] == "TRADE" and not p["no_trade_reasons"]

    # capped + unproven grade -> WEAK_LEAN with BOTH reasons, human text present
    p = compute_no_trade_reasons({"direction": "DOWN", "rawDirection": "DOWN", "actionable": False,
                                  "convictionCapped": True, "confluence": {"grade": "A"}})
    assert {"weak_conviction_capped", "grade_unproven"} <= set(p["no_trade_reasons"])
    assert len(p["no_trade_reason_text"]) == len(p["no_trade_reasons"])
    assert p["trade_verdict"] == "WEAK_LEAN"

    # idempotent / de-dup: a hard code that repeats isn't duplicated
    p = compute_no_trade_reasons({"direction": "NEUTRAL", "neutralReasonCode": "poor_regime"})
    assert p["no_trade_reasons"].count("poor_regime") == 1
    print("decision_gate self-test: ALL PASS")


if __name__ == "__main__":
    _selftest()
