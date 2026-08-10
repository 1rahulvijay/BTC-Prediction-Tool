"""Blueprint §31.2 - enforce head health in the decision path.

`monitoring/head_health.py` measures, from live outcomes, what each head is still allowed to do:

    USABLE            may price, may rank
    CALIBRATION_ONLY  may RANK, may NOT supply fair value
    DISABLED_NO_SKILL may do neither
    SHADOW / INSUFFICIENT_DATA / DRIFTED   likewise restricted

Until now nothing read those permissions, so a head measured as unable to price could still price.
This module is the reader, and it FAILS CLOSED. Missing, stale, unknown or corrupt health means
NO action authority - the head may neither price nor rank. The app stays online and may still
display diagnostics (may_display_confidence is a separate permission); what is withheld is the
authority to act. Absence of measurement is never a passing grade.

WHY THIS MATTERS MORE THAN IT LOOKS
    `BTC_ENABLE_PAPER_BET=1` is an operator override. Without this check, that override also
    re-enables betting on a probability the live data says cannot price - which is exactly the
    failure the lockdown existed to prevent. With it, the override can only act on a head that
    currently measures as USABLE. The switch stops being a way to overrule the evidence.

    Deleting or ageing the health report cannot restore permissions either: both states deny.

    python backend/head_permissions.py            # print current permissions
    python backend/head_permissions.py --selftest
"""
from __future__ import annotations

import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
REPORT = os.path.join(DATA, "research", "head_health", "head_health.json")

# A report older than this is not evidence about today's models.
MAX_REPORT_AGE_S = 14 * 24 * 3600

# Enforcement is ON by default. Set BTC_ENFORCE_HEAD_HEALTH=0 to observe-only (logged, not silent).
ENFORCED = os.environ.get("BTC_ENFORCE_HEAD_HEALTH", "1") != "0"

_CACHE = {"ts": 0.0, "val": None}
_CACHE_TTL_S = 60.0


def _load():
    now = time.time()
    if _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL_S:
        return _CACHE["val"]
    _CACHE["ts"] = now
    try:
        with open(REPORT, encoding="utf-8") as fh:
            rep = json.load(fh)
        age = now - os.path.getmtime(REPORT)
        rep["_age_s"] = age
        rep["_stale"] = age > MAX_REPORT_AGE_S
        _CACHE["val"] = rep
    except Exception:
        _CACHE["val"] = None
    return _CACHE["val"]


# FAIL CLOSED. Action authority is DENIED unless a current report affirmatively grants it.
#
# The previous default granted may_price/may_rank on a missing, stale or unknown head, on the
# reasoning that this module must not be able to disable the app. That conflated two different
# things: the app staying ONLINE, and a head being AUTHORIZED to move money. Deleting or ageing
# the report restored exactly the authority the lockdown exists to withhold - absence of
# measurement was read as a pass.
#
# The app stays online regardless; only ACTION authority is withheld. Diagnostics may still be
# displayed, which is why may_display_confidence is separated from the two action permissions.
DENIED = {
    "may_price": False,
    "may_rank": False,
    "may_display_confidence": False,
}

# States that never carry action authority, whatever the report's own permissions block says.
NO_ACTION_STATES = frozenset({
    "SHADOW", "INSUFFICIENT_DATA", "DISABLED_NO_SKILL", "UNKNOWN", "STALE", "MISSING",
})


def _denied(reason: str) -> dict:
    return {**DENIED, "reason": reason}


def _registry_cap(head: str) -> dict:
    """The static ceiling from model_registry. Unresolvable -> no authority, never a default.

    Imported lazily so this module keeps working (denying) if the registry cannot be loaded -
    an import error here must not become an exception that a caller's except-branch turns
    into a pass."""
    try:
        from head_artifact_identity import registry_authority
        return registry_authority(head)
    except Exception:      # noqa: BLE001
        return {"may_price": False, "may_rank": False, "may_size": False}


def head_state(head: str, *, artifact_sha: str | None = None,
               horizon: int | None = None) -> tuple[str, dict]:
    """(state, permissions) for a head. Missing / stale / unknown / corrupt -> DENIED.

    Authority is bound to WHICH ARTIFACT and WHICH HORIZON was measured, not to a head's
    NAME. Keying on the name alone let a retrained head inherit its predecessor's evidence:

        p_hold artifact A -> 5,000 live outcomes -> USABLE
        retrain
        p_hold artifact B -> zero live outcomes  -> may_price("p_hold") still True,
                                                    because the report was under 14 days old

    Freshness of the FILE is not freshness of the EVIDENCE. A report that cannot say which
    artifact it measured certifies nothing, so an entry without `artifact_sha` is denied
    outright rather than treated as applying to whatever happens to be serving.

    The app remains online in every one of these cases; what is withheld is the authority to
    price or rank, never the ability to serve or display."""
    rep = _load()
    if not rep:
        return "MISSING", _denied(
            "no head-health report; action authority denied until health is measured"
        )
    if rep.get("_stale"):
        return "STALE", _denied(
            f"head-health report is {rep['_age_s'] / 86400:.0f}d old; "
            f"stale measurement carries no action authority"
        )
    h = (rep.get("heads") or {}).get(head)
    if not h:
        return "UNKNOWN", _denied(
            f"'{head}' absent from the head-health report; unmeasured heads cannot act"
        )

    # ── ARTIFACT BINDING ───────────────────────────────────────────────────────────────────
    measured_sha = str(h.get("artifact_sha") or "")
    if not measured_sha:
        return "UNBOUND_EVIDENCE", _denied(
            f"'{head}' health names no artifact_sha, so it cannot certify any particular "
            f"model; evidence measured on an unnamed artifact does not transfer to a "
            f"retrained one"
        )
    if artifact_sha is None:
        # NOT "skip the comparison". If identity is required for authority, a caller that
        # cannot name what it is asking about has not met the requirement. Treating None as
        # "no comparison needed" made the binding opt-OUT: any call site that simply omitted
        # the argument kept its old name-keyed authority, which is the exact bug this module
        # exists to close.
        return "ARTIFACT_UNSPECIFIED", _denied(
            f"'{head}' authority requires the serving artifact sha; the caller supplied none, "
            f"so there is nothing to check the measured {measured_sha[:12]} against"
        )
    if str(artifact_sha) != measured_sha:
        return "ARTIFACT_MISMATCH", _denied(
            f"'{head}' health was measured on {measured_sha[:12]}, but {str(artifact_sha)[:12]} "
            f"is serving; a retrained head starts from zero evidence"
        )

    # ── HORIZON BINDING ────────────────────────────────────────────────────────────────────
    # P(Hold) at 5m and P(Hold) at 15m are different calibration problems. A pooled figure
    # can read USABLE on 5m strength while 15m is unusable, and 15m then acts on authority
    # 5m earned.
    if horizon is not None:
        by_h = h.get("by_horizon") or {}
        key = str(int(horizon))
        if not by_h:
            return "HORIZON_POOLED", _denied(
                f"'{head}' health pools horizons; a pooled measurement cannot authorize "
                f"{horizon}m specifically"
            )
        if key not in by_h:
            return "HORIZON_UNMEASURED", _denied(
                f"'{head}' health has no {horizon}m measurement "
                f"(measured: {sorted(by_h)}); permission applies only where evidence exists"
            )
        h = {**h, **by_h[key]}          # the horizon's own state/permissions win

    state = str(h.get("state") or h.get("status") or "UNKNOWN")
    perms = dict(h.get("permissions") or {})
    if state in NO_ACTION_STATES:
        # A report that grants permission to a no-action state is self-contradictory; the STATE
        # wins, so a malformed or hand-edited report cannot re-open the gate.
        return state, _denied(h.get("reason") or f"state {state} carries no action authority")
    # ── REGISTRY CAP ───────────────────────────────────────────────────────────────────────
    # Live evidence may REVOKE authority, never grant more than the static contract allows.
    # There were two disagreeing sources: model_registry declares persistence/P(Hold) as
    # may_rank but NOT may_price - live P(Hold) is overconfident, so pricing is deliberately
    # withheld - while head-health handed may_price to anything it classified USABLE. When two
    # systems disagree about authority the more permissive one wins by accident. Intersecting
    # them makes the registry a ceiling and health a further restriction on top.
    cap = _registry_cap(head)
    capped = {
        "may_price": bool(perms.get("may_price", False)) and cap["may_price"],
        "may_rank": bool(perms.get("may_rank", False)) and cap["may_rank"],
        # Display is diagnostic, not authority, so the registry does not gate it.
        "may_display_confidence": bool(perms.get("may_display_confidence", False)),
        "reason": h.get("reason", ""),
    }
    for act in ("may_price", "may_rank"):
        if perms.get(act) and not cap[act]:
            capped["reason"] = (
                f"{capped['reason']} | {act} withheld by the model registry, which caps this "
                f"head below what health measured"
            ).strip(" |")
    return state, capped


def may_price(head: str, *, artifact_sha: str | None = None,
              horizon: int | None = None) -> tuple[bool, str]:
    """May this head supply a FAIR VALUE (i.e. authorize money-relevant arithmetic)?

    Pass the SERVING artifact's sha and the horizon being priced. Omitting them does not
    grant a broader permission - an entry that names no artifact is denied either way - but
    passing them is what makes a retrain revoke authority instead of inheriting it."""
    state, perms = head_state(head, artifact_sha=artifact_sha, horizon=horizon)
    if not ENFORCED:
        return True, f"{head}={state} (enforcement disabled)"
    ok = bool(perms.get("may_price", False))      # default DENY, never allow
    return ok, f"{head}={state}: {perms.get('reason', '')}"


def may_rank(head: str, *, artifact_sha: str | None = None,
             horizon: int | None = None) -> tuple[bool, str]:
    """May this head ORDER candidates (weaker permission than pricing)?"""
    state, perms = head_state(head, artifact_sha=artifact_sha, horizon=horizon)
    if not ENFORCED:
        return True, f"{head}={state} (enforcement disabled)"
    return bool(perms.get("may_rank", False)), f"{head}={state}: {perms.get('reason', '')}"


def selftest() -> int:
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and bool(c)

    print("head_permissions selftest (FAIL-CLOSED)")
    import tempfile
    global REPORT
    tmp = tempfile.mkdtemp()
    REPORT = os.path.join(tmp, "head_health.json")
    SHA_A = "a" * 64          # the artifact the report measured
    SHA_B = "b" * 64          # what a retrain produces

    def reset():
        global _CACHE
        _CACHE = {"ts": 0.0, "val": None}

    def write(payload, age_days=0.0):
        with open(REPORT, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        if age_days:
            old = time.time() - age_days * 86400
            os.utime(REPORT, (old, old))
        reset()

    # --- the four denial paths -------------------------------------------------------------
    if os.path.exists(REPORT):
        os.remove(REPORT)
    reset()
    st, p = head_state("p_hold")
    chk(st == "MISSING" and not p["may_price"] and not p["may_rank"],
        f"MISSING report -> denied ({st})")
    chk(not may_price("p_hold")[0] and not may_rank("p_hold")[0],
        "missing report denies BOTH price and rank")

    write({"heads": {"p_hold": {"state": "USABLE",
                               "permissions": {"may_price": True, "may_rank": True}}}},
          age_days=30)
    st, p = head_state("p_hold")
    chk(st == "STALE" and not p["may_price"] and not p["may_rank"],
        "STALE report -> denied even though it says USABLE")

    write({"heads": {"other": {"state": "USABLE",
                              "permissions": {"may_price": True, "may_rank": True}}}})
    st, p = head_state("p_hold")
    chk(st == "UNKNOWN" and not p["may_price"] and not p["may_rank"],
        "head ABSENT from the report -> denied")

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    reset()
    st, p = head_state("p_hold")
    chk(st == "MISSING" and not p["may_price"], "CORRUPT json -> denied, never a crash")

    # --- states that must never carry action authority --------------------------------------
    for state in ("SHADOW", "INSUFFICIENT_DATA", "DISABLED_NO_SKILL"):
        write({"heads": {"p_hold": {"state": state,
                                    # a self-contradictory report must not re-open the gate
                                    "permissions": {"may_price": True, "may_rank": True}}}})
        _, p = head_state("p_hold")
        chk(not p["may_price"] and not p["may_rank"],
            f"{state} denied even when the report grants permission")

    # --- ARTIFACT AND HORIZON BINDING --------------------------------------------------------
    # Authority belongs to an ARTIFACT, not to a name. Keying on the name let a retrained head
    # inherit its predecessor's evidence for up to the 14-day freshness window.
    write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                "permissions": {"may_price": True, "may_rank": True}}}})
    chk(may_rank("p_hold", artifact_sha=SHA_A)[0],
        "the MEASURED artifact may rank")
    chk(not may_price("p_hold", artifact_sha=SHA_A)[0],
        "and still may not PRICE - the registry cap applies on top of a valid artifact "
        "binding, not instead of it")
    st, p = head_state("p_hold", artifact_sha=SHA_B)
    chk(st == "ARTIFACT_MISMATCH" and not p["may_price"] and not p["may_rank"],
        "a RETRAINED artifact inherits nothing - a different sha is a different model")

    write({"heads": {"p_hold": {"state": "USABLE",
                                "permissions": {"may_price": True, "may_rank": True}}}})
    st, p = head_state("p_hold", artifact_sha=SHA_A)
    chk(st == "UNBOUND_EVIDENCE" and not p["may_price"],
        "a report naming NO artifact certifies nothing, even when it says USABLE")
    chk(not may_price("p_hold")[0] and not may_rank("p_hold")[0],
        "and omitting the sha does not sidestep it - unbound evidence denies either way")

    # A head the registry caps at NO RANK. `fade_roundtrip` is declared may_rank=False -
    # research only - so health calling it USABLE must not make it rankable. Without a case
    # like this the rank half of the cap is untested: every other head here happens to have
    # may_rank=True, so deleting that half of the intersection changes nothing.
    write({"heads": {"fade_roundtrip": {
        "state": "USABLE", "artifact_sha": SHA_A,
        "permissions": {"may_price": True, "may_rank": True}}}})
    st, p = head_state("fade_roundtrip", artifact_sha=SHA_A)
    chk(st == "USABLE" and not p["may_rank"] and not p["may_price"],
        "a research-only head stays research-only however healthy it measures - the registry "
        "caps RANK as well as PRICE")

    # A registry lookup that RAISES must deny. The cap is wrapped in a try/except so a
    # registry import problem cannot crash the gate - but that except branch must fail
    # CLOSED, or an unrelated import error silently restores full authority.
    import head_artifact_identity as _hai
    _real_auth = _hai.registry_authority

    def _boom(*a, **k):
        raise RuntimeError("registry unavailable")

    _hai.registry_authority = _boom
    try:
        write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                    "permissions": {"may_price": True, "may_rank": True}}}})
        _, p = head_state("p_hold", artifact_sha=SHA_A)
        chk(not p["may_price"] and not p["may_rank"],
            "a registry lookup that RAISES denies - the cap's except branch fails closed, so "
            "an import error cannot restore the authority it exists to withhold")
    finally:
        _hai.registry_authority = _real_auth

    # A head with no registry contract at all.
    write({"heads": {"not_registered_anywhere": {
        "state": "USABLE", "artifact_sha": SHA_A,
        "permissions": {"may_price": True, "may_rank": True}}}})
    _, p = head_state("not_registered_anywhere", artifact_sha=SHA_A)
    chk(not p["may_price"] and not p["may_rank"],
        "an UNREGISTERED head gets no authority - an artifact outside the registry has no "
        "identity contract to check against, so it cannot be granted one by a health file")

    # Horizon. P(Hold) at 5m and 15m are different calibration problems; a pooled figure must
    # not let one act on the other's evidence.
    write({"heads": {"p_hold": {
        "state": "USABLE", "artifact_sha": SHA_A,
        "permissions": {"may_price": True, "may_rank": True},
        "by_horizon": {
            "5": {"state": "USABLE",
                  "permissions": {"may_price": True, "may_rank": True}},
            "15": {"state": "DISABLED_NO_SKILL",
                   "permissions": {"may_price": False, "may_rank": False}}}}}})
    chk(may_rank("p_hold", artifact_sha=SHA_A, horizon=5)[0],
        "the horizon with evidence may rank")
    chk(not may_rank("p_hold", artifact_sha=SHA_A, horizon=15)[0],
        "while the WEAK horizon may not, though the pooled entry says USABLE - 15m no longer "
        "acts on authority 5m earned")
    st, _ = head_state("p_hold", artifact_sha=SHA_A, horizon=60)
    chk(st == "HORIZON_UNMEASURED",
        "and an unmeasured horizon is denied rather than falling back to the pooled figure")

    write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                "permissions": {"may_price": True, "may_rank": True}}}})
    st, _ = head_state("p_hold", artifact_sha=SHA_A, horizon=5)
    chk(st == "HORIZON_POOLED",
        "a report with no per-horizon blocks cannot authorize a specific horizon at all")

    # OMITTING the sha is a denial in its own right, not a skipped comparison. Treating None
    # as "nothing to compare" made the binding opt-OUT - any call site that just left the
    # argument off kept its old name-keyed authority.
    write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                "permissions": {"may_price": True, "may_rank": True}}}})
    st, p = head_state("p_hold")
    chk(st == "ARTIFACT_UNSPECIFIED" and not p["may_rank"] and not p["may_price"],
        "a caller that names NO artifact is denied - it has not met the identity requirement, "
        "so there is nothing to check the measured sha against")

    # --- the graded permissions that SHOULD be honoured --------------------------------------
    write({"heads": {"p_hold": {"state": "CALIBRATION_ONLY", "reason": "ECE 0.07",
                                "artifact_sha": SHA_A,
                                "permissions": {"may_price": False, "may_rank": True}}}})
    _, p = head_state("p_hold", artifact_sha=SHA_A)
    chk(not p["may_price"] and p["may_rank"], "CALIBRATION_ONLY may rank, may NOT price")

    write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                "permissions": {"may_price": True, "may_rank": True,
                                                "may_display_confidence": True}}}})
    _, p = head_state("p_hold", artifact_sha=SHA_A)
    chk(p["may_rank"], "USABLE may rank (the gate re-opens)")
    chk(may_rank("p_hold", artifact_sha=SHA_A)[0], "may_rank() honours a measured USABLE head")
    # The REGISTRY CAP. model_registry gives persistence/P(Hold) may_rank but not may_price;
    # health classifying it USABLE must not manufacture pricing authority the contract
    # withholds. Live evidence subtracts from the ceiling, it never adds.
    chk(not p["may_price"] and not may_price("p_hold", artifact_sha=SHA_A)[0],
        "but may NOT price: the registry caps this head below what health measured")
    chk("registry" in p.get("reason", ""),
        "and the cap SAYS SO in the reason, so an operator sees why pricing was withheld "
        "rather than assuming the head measured badly")

    # --- defaults are DENY, not ALLOW --------------------------------------------------------
    write({"heads": {"p_hold": {"state": "USABLE", "artifact_sha": SHA_A,
                                "permissions": {}}}})
    _, p = head_state("p_hold", artifact_sha=SHA_A)
    chk(not p["may_price"] and not p["may_rank"],
        "an empty permissions block defaults to DENY, not ALLOW")
    chk(DENIED == {"may_price": False, "may_rank": False, "may_display_confidence": False},
        "the module-level default is total denial")

    print("head-permissions:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    rep = _load()
    print(f"report: {REPORT}")
    print(f"enforcement: {'ON' if ENFORCED else 'OFF (observe only)'}")
    if not rep:
        print("  no report found - permissions are NOT measured: all action DENIED")
    else:
        print(f"  age: {rep.get('_age_s', 0)/3600:.1f}h  stale={rep.get('_stale')}")
        for head in (rep.get("heads") or {}):
            st, p = head_state(head)
            print(f"  {head:<14}{st:<20}price={p.get('may_price')}  rank={p.get('may_rank')}"
                  f"   {p.get('reason', '')[:44]}")
