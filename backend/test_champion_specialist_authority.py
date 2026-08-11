"""Every specialist that CHANGES a champion decision must hold may_rank to do so.

WHAT WAS WRONG
    Head health was enforced for exactly one head - p_hold, at the final bet-authority
    branch. Five other tiers reached the decision completely ungated:

        big_move_tier  == "quiet"  -> WAIT
        activity_tier  == "quiet"  -> WAIT
        big_drop_risk  == "HIGH"   -> AVOID_LONG
        big_up_tier    == "HIGH"   -> blocks DOWN
        big_down_tier  == "HIGH"   -> blocks UP

    None of these produce a PRICE, so may_price never covered them, and nothing else did.
    A big_drop head measured as having no skill could still suppress profitable longs; a
    big_move head could still skip profitable windows. That is an economic cost paid by a
    head that was never authorized to impose it.

    The horizon needed to authorize them was computed 120 lines BELOW the tier reads, with
    nine early returns in between - so these heads were changing decisions before a horizon
    was even in scope.

    python backend/test_champion_specialist_authority.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _round(**kw):
    # current_price and price_to_beat are load-bearing: without a fresh price the champion
    # returns AVOID at a feed-staleness guard ~20 lines ABOVE the gated tier reads, so a
    # fixture missing them exercises none of the authority logic and every assertion below
    # would be scoring an unrelated early return.
    base = {"current_position": "UP", "p_hold": 0.55, "horizon": 5, "seconds_left": 60.0,
            "current_price": 100_000.0, "price_to_beat": 99_988.0,
            "current_move": 12.0, "regime": "NORMAL", "live_lean": "UP",
            "big_move_tier": "quiet", "activity_tier": "normal",
            "big_drop_risk": "LOW", "big_up_tier": "LOW", "big_down_tier": "LOW"}
    base.update(kw)
    return base


def _unauth(result) -> set:
    """The set of heads the champion says it suppressed, parsed from its own flag.

    Asserting merely that the flag EXISTS is not enough: the fixtures below authorize one
    head at a time, so the other three are always unauthorized and always produce it. Two
    mutations (drop the horizon argument, drop the artifact sha) survived against exactly
    that weakness - the assertion was satisfied by heads it was not testing.
    """
    for f in (result.get("risk_flags") or []):
        if "without rank authority" in f:
            return {h.strip() for h in f.split(":", 1)[1].split(",")}
    return set()


def main() -> int:
    src = (BACKEND / "decision_champion.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)

    # 1. Structure: the tiers must be read THROUGH the gate, not directly.
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "champion_decision")
    gated = {c.args[0].value for c in ast.walk(fn) if isinstance(c, ast.Call)
             and getattr(c.func, "id", "") == "_ranked"
             and c.args and isinstance(c.args[0], ast.Constant)}
    check(gated == {"big_drop", "big_move", "directional", "activity"},
          f"every decision-changing specialist is read through the authority gate {sorted(gated)}")

    hz = [a.lineno for a in ast.walk(fn) if isinstance(a, ast.Assign)
          and any(getattr(x, "id", "") == "horizon" for x in a.targets)]
    rk = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call)
          and getattr(c.func, "id", "") == "_ranked"]
    check(len(hz) == 1 and max(hz) < min(rk),
          f"the horizon is computed ONCE (line {hz[0]}) and before every gated read "
          f"(first at {min(rk)}) - it used to be assigned below nine early returns")

    # 2. BEHAVIOURAL: an unauthorized head must stop changing the decision.
    with tempfile.TemporaryDirectory() as tmp:
        import head_permissions as hp
        import decision_champion as dc
        rep_dir = Path(tmp) / "research" / "head_health"
        rep_dir.mkdir(parents=True, exist_ok=True)
        rep = rep_dir / "head_health.json"
        hp.REPORT = str(rep)
        os.environ["BTC_ENFORCE_HEAD_HEALTH"] = "1"
        hp.ENFORCED = True

        def publish(payload):
            payload = dict(payload)
            payload.setdefault("evidence_last_ts_ms", int(time.time() * 1000))
            rep.write_text(json.dumps(payload), encoding="utf-8")
            hp._CACHE["val"], hp._CACHE["ts"] = None, 0.0

        # Nothing authorized: a "quiet" big_move must NOT force WAIT.
        publish({"heads": {}})
        quiet = dc.champion_decision(_round(big_move_tier="quiet"), {})
        qf = quiet.get("risk_flags")
        check(isinstance(qf, list) and qf,
              "the champion returns a non-empty risk_flags list, so the assertions below are "
              "reading a key that exists - an absent key would make every 'not in' check pass "
              "vacuously")
        check("quiet round" not in " ".join(qf),
              "with NO health report, a 'quiet' big_move tier no longer flags or forces WAIT "
              "- an unmeasured head is diagnostic, not decisive")
        check(any("without rank authority" in f for f in (quiet.get("risk_flags") or [])),
              "and the suppression is REPORTED, so an operator is not shown a decision that "
              "silently ignored a head")

        # Each remaining head needs an input that ACTUALLY changes something, or gating it is
        # untestable: a fixture carrying activity_tier="normal" looks fine while the gate on
        # activity does nothing at all. Mutation testing caught exactly that.
        for tier_kw, phrase, head in (
                ({"activity_tier": "quiet"}, "low activity", "activity"),
                ({"big_drop_risk": "HIGH"}, "big-drop risk", "big_drop"),
                ({"big_up_tier": "HIGH"}, "big-up confirmation", "directional"),
                ({"big_down_tier": "HIGH"}, "big-down confirmation", "directional")):
            r = dc.champion_decision(_round(**tier_kw), {})
            joined = " ".join(r.get("risk_flags") or [])
            check(phrase not in joined and head in _unauth(r),
                  f"an unauthorized {head} cannot raise {phrase!r} - every gated head is "
                  f"exercised with an input that would otherwise change the decision")

        # Authorized: the same input must now bite. This is the check that stops the whole
        # test being satisfied by a champion that simply ignores these heads - a pure deny
        # test passes just as well against code that never reads the tiers at all.
        #
        # A real artifact is planted so resolve_serving_sha returns a real sha, rather than
        # skipping the case when the repo has no trained keeper on disk.
        import head_artifact_identity as hai
        hai.MODELS = Path(tmp)
        (Path(tmp) / hai.head_artifacts()["big_move"]).write_bytes(b"planted-bigmove-artifact")
        sha = hai.resolve_serving_sha("big_move")
        assert sha, "fixture failed to plant an artifact"
        entry = {"state": "USABLE", "artifact_sha": sha or ("c" * 64),
                 "permissions": {"may_price": False, "may_rank": True},
                 "by_horizon": {"5": {"state": "USABLE",
                                      "permissions": {"may_price": False, "may_rank": True},
                                      "by_region": {"60-90s": {
                                          "state": "USABLE",
                                          "permissions": {"may_price": False,
                                                          "may_rank": True}}}}}}
        publish({"heads": {"big_move": entry}})
        armed = dc.champion_decision(_round(big_move_tier="quiet"), {})
        check("big_move" not in _unauth(armed)
              and "quiet round" in " ".join(armed.get("risk_flags") or []),
              "once big_move HOLDS may_rank for THIS artifact sha and horizon it is not in "
              "the suppressed set AND its 'quiet' tier flags again - the gate withholds "
              "authority without disabling the head, and a champion that ignored these tiers "
              "entirely would fail the second half")

        # Same head, same report, DIFFERENT artifact on disk: a retrain must revoke.
        (Path(tmp) / hai.head_artifacts()["big_move"]).write_bytes(b"retrained-bigmove-artifact")
        after = dc.champion_decision(_round(big_move_tier="quiet"), {})
        check("big_move" in _unauth(after),
              "and rewriting the artifact revokes big_move immediately - the champion follows "
              "the sha on disk, so a retrained specialist cannot act on old evidence")

        # A head authorized at 5m must not act at 15m.
        #
        # The sha is RECOMPUTED here. The revocation case above rewrote the artifact, so
        # reusing the old value would make this assertion pass on ARTIFACT_MISMATCH while
        # appearing to test the horizon - it did exactly that until mutation testing showed
        # "drop the horizon argument" surviving.
        sha_now = hai.resolve_serving_sha("big_move")
        assert sha_now != sha, "fixture: the artifact should have changed above"
        publish({"heads": {"big_move": {
            "state": "USABLE", "artifact_sha": sha_now,
            "permissions": {"may_rank": True},
            "by_horizon": {"5": {"state": "USABLE", "permissions": {"may_rank": True},
                                    "by_region": {"60-90s": {
                                        "state": "USABLE",
                                        "permissions": {"may_rank": True}}}}}}}})
        near = dc.champion_decision(_round(big_move_tier="quiet", horizon=5), {})
        check("big_move" not in _unauth(near),
              "with the CURRENT sha and a 5m block, big_move SPECIFICALLY is authorized at 5m "
              "- so the 15m denial below can only come from the horizon, not a stale artifact")
        far = dc.champion_decision(_round(big_move_tier="quiet", horizon=15), {})
        check("big_move" in _unauth(far),
              "while big_move SPECIFICALLY is refused at 15m on the same artifact - authority "
              "applies where the evidence is, not to the head's name")

        # A permission check that cannot RUN has not granted permission.
        _real = hp.may_rank

        def _boom(*a, **k):
            raise RuntimeError("permission backend down")

        hp.may_rank = _boom
        try:
            broken = dc.champion_decision(_round(big_move_tier="quiet", horizon=5), {})
            check("big_move" in _unauth(broken),
                  "a permission check that RAISES denies big_move - a broken health reader "
                  "must not silently restore the authority the gate exists to withhold")
        finally:
            hp.may_rank = _real

    print(f"\nCHAMPION SPECIALIST AUTHORITY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
