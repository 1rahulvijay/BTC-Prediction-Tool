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


def head_state(head: str) -> tuple[str, dict]:
    """(state, permissions) for a head. Missing / stale / unknown / corrupt -> DENIED.

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
    state = str(h.get("state") or h.get("status") or "UNKNOWN")
    perms = dict(h.get("permissions") or {})
    if state in NO_ACTION_STATES:
        # A report that grants permission to a no-action state is self-contradictory; the STATE
        # wins, so a malformed or hand-edited report cannot re-open the gate.
        return state, _denied(h.get("reason") or f"state {state} carries no action authority")
    perms = {
        "may_price": bool(perms.get("may_price", False)),
        "may_rank": bool(perms.get("may_rank", False)),
        "may_display_confidence": bool(perms.get("may_display_confidence", False)),
        "reason": h.get("reason", ""),
    }
    return state, perms


def may_price(head: str) -> tuple[bool, str]:
    """May this head supply a FAIR VALUE (i.e. authorize money-relevant arithmetic)?"""
    state, perms = head_state(head)
    if not ENFORCED:
        return True, f"{head}={state} (enforcement disabled)"
    ok = bool(perms.get("may_price", False))      # default DENY, never allow
    return ok, f"{head}={state}: {perms.get('reason', '')}"


def may_rank(head: str) -> tuple[bool, str]:
    """May this head ORDER candidates (weaker permission than pricing)?"""
    state, perms = head_state(head)
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
    global REPORT, _CACHE
    tmp = tempfile.mkdtemp()
    REPORT = os.path.join(tmp, "head_health.json")

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

    # --- the graded permissions that SHOULD be honoured --------------------------------------
    write({"heads": {"p_hold": {"state": "CALIBRATION_ONLY", "reason": "ECE 0.07",
                                "permissions": {"may_price": False, "may_rank": True}}}})
    _, p = head_state("p_hold")
    chk(not p["may_price"] and p["may_rank"], "CALIBRATION_ONLY may rank, may NOT price")

    write({"heads": {"p_hold": {"state": "USABLE",
                                "permissions": {"may_price": True, "may_rank": True,
                                                "may_display_confidence": True}}}})
    _, p = head_state("p_hold")
    chk(p["may_price"] and p["may_rank"], "USABLE may price and rank (the gate re-opens)")
    chk(may_price("p_hold")[0], "may_price() honours a measured USABLE head")

    # --- defaults are DENY, not ALLOW --------------------------------------------------------
    write({"heads": {"p_hold": {"state": "USABLE", "permissions": {}}}})
    _, p = head_state("p_hold")
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
